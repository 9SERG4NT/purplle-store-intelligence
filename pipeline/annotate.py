"""Render annotated 'processed' replays for the dashboard.

Re-runs YOLOv8 + ByteTrack on each clip and burns the detection overlay into a
browser-playable MP4: person boxes + track IDs, zone polygons, the entry
counting line, and a live HUD (running entries/exits, people on screen, billing
queue depth, clip timestamp). Output goes to dashboard/media/<camera>.mp4 so the
dashboard can play it back next to the live metrics.

    python pipeline/annotate.py --footage "../CCTV Footage"

Output videos are git-ignored (footage-derived; must not be committed).
"""
from __future__ import annotations

import argparse
import json
import math
import os
from datetime import timedelta
from pathlib import Path

import cv2
import imageio.v2 as imageio
import numpy as np

from config import CONFIG
from geometry import side_of, foot_point, point_in_polygon
from layout import Camera, StoreLayout, get_layout

OUT_W = 854  # 480p-ish, even dimensions for yuv420p
# Darken the underlying scene so the bright detection overlays read clearly;
# detected people are "spotlit" back toward full brightness (see annotate_camera).
# 1.0 = no dimming, lower = darker. Override with VIZ_DIM.
VIZ_DIM = float(os.getenv("VIZ_DIM", "0.55"))


def _color(track_id: int) -> tuple[int, int, int]:
    """Stable BGR colour per track id."""
    rng = (track_id * 2654435761) & 0xFFFFFFFF
    return (50 + rng % 180, 50 + (rng >> 8) % 180, 50 + (rng >> 16) % 180)


def _draw_entry_line(frame, cam, w, h) -> None:
    """Draw the entry tripwire and an arrow pointing the inbound ('IN') direction.

    The line is a tripwire laid ACROSS the lane people walk down, not along it —
    a person is counted when their feet cross from the 'outside' side to the side
    that contains inside_point. The arrow makes that direction obvious on screen.
    """
    (ax, ay), (bx, by) = cam.entry_line
    pa = (int(ax * w), int(ay * h))
    pb = (int(bx * w), int(by * h))
    cv2.line(frame, pa, pb, (0, 0, 255), 3, cv2.LINE_AA)
    cv2.putText(frame, "ENTRY LINE", (pa[0] + 6, max(16, pa[1] - 8)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2, cv2.LINE_AA)
    if cam.inside_point:
        mx, my = (pa[0] + pb[0]) // 2, (pa[1] + pb[1]) // 2
        ix, iy = int(cam.inside_point[0] * w), int(cam.inside_point[1] * h)
        dx, dy = ix - mx, iy - my
        d = math.hypot(dx, dy) or 1.0
        reach = int(0.11 * h)
        ex, ey = int(mx + dx / d * reach), int(my + dy / d * reach)
        cv2.arrowedLine(frame, (mx, my), (ex, ey), (60, 220, 60), 3, cv2.LINE_AA, tipLength=0.35)
        cv2.putText(frame, "IN", (ex + 5, ey + 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (60, 220, 60), 2, cv2.LINE_AA)


def _poly(frame, poly, w, h, color, label=None):
    pts = np.array([[int(x * w), int(y * h)] for x, y in poly], np.int32)
    overlay = frame.copy()
    cv2.fillPoly(overlay, [pts], color)
    cv2.addWeighted(overlay, 0.12, frame, 0.88, 0, frame)
    cv2.polylines(frame, [pts], True, color, 2)
    if label:
        cv2.putText(frame, label, (pts[0][0] + 4, pts[0][1] + 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)


def annotate_camera(cam: Camera, video_path: Path, layout: StoreLayout, model, out_path: Path) -> None:
    cap = cv2.VideoCapture(str(video_path))
    native = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    stride = max(1, round(native / cam.fps_sample))
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)); src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    out_h = int(OUT_W * src_h / src_w) // 2 * 2
    clip_start = cam.clip_start
    print(f"[{cam.camera_id}] annotating {video_path.name} -> {out_path.name}", flush=True)

    writer = imageio.get_writer(
        str(out_path), fps=cam.fps_sample, codec="libx264", quality=6,
        macro_block_size=None, ffmpeg_params=["-pix_fmt", "yuv420p"],
    )
    entries = exits = 0
    prev_foot: dict[int, tuple[float, float]] = {}
    line_side: dict[int, int] = {}  # committed side of the entry line per track (hysteresis)
    samples: list[dict] = []  # per-output-frame running counts -> sidecar JSON for the dashboard
    written = 0
    idx = -1
    try:
        while True:
            if not cap.grab():
                break
            idx += 1
            if idx % stride:
                continue
            ok, frame = cap.retrieve()
            if not ok:
                break
            h, w = frame.shape[:2]
            ts = clip_start + timedelta(seconds=idx / native)
            # Detect on the bright original frame for best recall...
            res = model.track(frame, persist=True, conf=CONFIG.det_conf,
                              classes=[CONFIG.person_class_id], imgsz=CONFIG.imgsz,
                              tracker="bytetrack.yaml", verbose=False)[0]
            # ...then darken a copy to draw on, so the overlays stand out. Detected
            # people are spotlit back to full brightness in the person loop below.
            orig = frame
            frame = cv2.convertScaleAbs(frame, alpha=VIZ_DIM)

            # zones for this camera
            for zid in cam.zones:
                z = layout.zones[zid]
                _poly(frame, z.polygon, w, h, (180, 180, 60), z.zone_id)
            if cam.queue_region:
                _poly(frame, cam.queue_region, w, h, (0, 140, 255), "QUEUE")
            if cam.entry_line and cam.inside_point:
                _draw_entry_line(frame, cam, w, h)

            queue_depth = 0
            persons = 0
            if res.boxes is not None and res.boxes.id is not None:
                ids = res.boxes.id.cpu().numpy().astype(int)
                xyxys = res.boxes.xyxy.cpu().numpy()
                confs = res.boxes.conf.cpu().numpy()
                persons = len(ids)
                for tid, box, conf in zip(ids, xyxys, confs):
                    fp = foot_point(box, w, h)
                    if cam.queue_region and point_in_polygon(fp, cam.queue_region):
                        queue_depth += 1
                    if cam.entry_line and cam.inside_point:
                        cur_side = side_of(cam.entry_line[0], cam.entry_line[1], fp, CONFIG.crossing_margin)
                        if cur_side != 0:
                            if tid not in line_side:
                                line_side[tid] = cur_side
                            elif cur_side != line_side[tid]:
                                inside_side = side_of(cam.entry_line[0], cam.entry_line[1], cam.inside_point)
                                if cur_side == inside_side:
                                    entries += 1
                                else:
                                    exits += 1
                                line_side[tid] = cur_side
                    prev_foot[tid] = fp
                    x1, y1, x2, y2 = [int(v) for v in box]
                    # spotlight: paint the bright original back inside the person box
                    cx1, cy1 = max(0, x1), max(0, y1)
                    cx2, cy2 = min(w, x2), min(h, y2)
                    if cx2 > cx1 and cy2 > cy1:
                        frame[cy1:cy2, cx1:cx2] = cv2.addWeighted(
                            orig[cy1:cy2, cx1:cx2], 0.85, frame[cy1:cy2, cx1:cx2], 0.15, 0)
                    c = _color(int(tid))
                    cv2.rectangle(frame, (x1, y1), (x2, y2), c, 2)
                    cv2.putText(frame, f"#{tid} {conf:.2f}", (x1, max(12, y1 - 6)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, 1, cv2.LINE_AA)
                    cv2.circle(frame, (int(fp[0] * w), int(fp[1] * h)), 4, c, -1)

            _hud(frame, cam, ts, entries, exits, persons, queue_depth)
            small = cv2.resize(frame, (OUT_W, out_h), interpolation=cv2.INTER_AREA)
            writer.append_data(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
            samples.append({"t": round(written / cam.fps_sample, 2), "persons": persons,
                            "entries": entries, "exits": exits, "queue": queue_depth,
                            "clock": ts.strftime("%H:%M:%S")})
            written += 1
    finally:
        cap.release()
        writer.close()
    # Sidecar consumed by the dashboard to mirror these live counts in HTML,
    # synced to the video playhead (frame index = currentTime * fps).
    sidecar = {
        "camera_id": cam.camera_id, "role": cam.role, "fps": cam.fps_sample,
        "duration": round(written / cam.fps_sample, 2),
        "has_line": bool(cam.entry_line), "has_queue": bool(cam.queue_region),
        "zones": [layout.zones[z].name for z in cam.zones if z in layout.zones],
        "samples": samples,
    }
    out_path.with_suffix(".json").write_text(json.dumps(sidecar), encoding="utf-8")
    print(f"[{cam.camera_id}] done: entries={entries} exits={exits} frames={written}", flush=True)


def _hud(frame, cam, ts, entries, exits, persons, queue_depth):
    h, w = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (w, 34), (24, 18, 30), -1)
    line = f"{cam.camera_id} [{cam.role}]  {ts.strftime('%H:%M:%S')}   persons:{persons}"
    if cam.role == "entry":
        line += f"   ENTRIES:{entries}  EXITS:{exits}"
    if cam.role == "billing":
        line += f"   QUEUE:{queue_depth}"
    cv2.putText(frame, line, (10, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (240, 240, 245), 1, cv2.LINE_AA)


def main() -> None:
    from ultralytics import YOLO

    ap = argparse.ArgumentParser()
    here = Path(__file__).resolve().parent
    ap.add_argument("--footage", default=str(here.parent.parent / "CCTV Footage"))
    ap.add_argument("--out", default=str(here.parent / "dashboard" / "media"))
    ap.add_argument("--only", default=None, help="comma list of camera_ids to render")
    ap.add_argument("--layout", default=None,
                    help="store layout json (defaults to data/store_layout.json); "
                         "pass data/store_layout_2.json to render the second store")
    args = ap.parse_args()

    layout = get_layout(args.layout)
    out_dir = Path(args.out); out_dir.mkdir(parents=True, exist_ok=True)
    model = YOLO(CONFIG.model_weights)
    only = set(args.only.split(",")) if args.only else None

    for cam in layout.cameras.values():
        if only and cam.camera_id not in only:
            continue
        vp = Path(args.footage) / cam.source_file
        if not vp.exists():
            print(f"[skip] {cam.camera_id}: {vp} missing")
            continue
        annotate_camera(cam, vp, layout, model, out_dir / f"{cam.camera_id.lower()}.mp4")
    print(f"\nannotated videos -> {out_dir}")


if __name__ == "__main__":
    main()

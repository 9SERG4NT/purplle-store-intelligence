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
from datetime import timedelta
from pathlib import Path

import cv2
import imageio.v2 as imageio
import numpy as np

from config import CONFIG
from geometry import crossing_direction, foot_point, point_in_polygon
from layout import Camera, StoreLayout, get_layout

OUT_W = 854  # 480p-ish, even dimensions for yuv420p


def _color(track_id: int) -> tuple[int, int, int]:
    """Stable BGR colour per track id."""
    rng = (track_id * 2654435761) & 0xFFFFFFFF
    return (50 + rng % 180, 50 + (rng >> 8) % 180, 50 + (rng >> 16) % 180)


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
            res = model.track(frame, persist=True, conf=CONFIG.det_conf,
                              classes=[CONFIG.person_class_id], imgsz=CONFIG.imgsz,
                              tracker="bytetrack.yaml", verbose=False)[0]

            # zones for this camera
            for zid in cam.zones:
                z = layout.zones[zid]
                _poly(frame, z.polygon, w, h, (180, 180, 60), z.zone_id)
            if cam.queue_region:
                _poly(frame, cam.queue_region, w, h, (0, 140, 255), "QUEUE")
            if cam.entry_line and cam.inside_point:
                (ax, ay), (bx, by) = cam.entry_line
                cv2.line(frame, (int(ax * w), int(ay * h)), (int(bx * w), int(by * h)), (0, 0, 255), 3)

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
                    if cam.entry_line and cam.inside_point and tid in prev_foot:
                        d = crossing_direction(cam.entry_line[0], cam.entry_line[1],
                                               cam.inside_point, prev_foot[tid], fp)
                        if d == "inbound":
                            entries += 1
                        elif d == "outbound":
                            exits += 1
                    prev_foot[tid] = fp
                    x1, y1, x2, y2 = [int(v) for v in box]
                    c = _color(int(tid))
                    cv2.rectangle(frame, (x1, y1), (x2, y2), c, 2)
                    cv2.putText(frame, f"#{tid} {conf:.2f}", (x1, max(12, y1 - 6)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, 1, cv2.LINE_AA)
                    cv2.circle(frame, (int(fp[0] * w), int(fp[1] * h)), 4, c, -1)

            _hud(frame, cam, ts, entries, exits, persons, queue_depth)
            small = cv2.resize(frame, (OUT_W, out_h), interpolation=cv2.INTER_AREA)
            writer.append_data(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
    finally:
        cap.release()
        writer.close()
    print(f"[{cam.camera_id}] done: entries={entries} exits={exits}", flush=True)


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
    args = ap.parse_args()

    layout = get_layout()
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

"""Detection + tracking driver (Part A).

Per camera: YOLOv8 person detection + ByteTrack association, sampled at the
camera's configured FPS. Produces `Tracklet` summaries (zone intervals, entry
crossings, billing queue depth, appearance + staff signals) which `associate.py`
turns into the store-wide event stream.

Run:  python detect.py --footage "../CCTV Footage" --out ../data/events.jsonl
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from appearance import appearance_descriptor, dark_fraction
import reid
from config import CONFIG
from emit import EventWriter
from geometry import side_of, foot_point, point_in_polygon
from layout import Camera, StoreLayout, get_layout
from associate import SessionManager
from tracklets import Crossing, Tracklet, ZoneInterval


@dataclass
class _TrackState:
    track_id: int
    t_start: datetime
    t_end: datetime
    n_frames: int = 0
    conf_sum: float = 0.0
    dark_sum: float = 0.0
    dark_n: int = 0
    desc_sum: Optional[np.ndarray] = None
    desc_n: int = 0
    behind_counter_n: int = 0
    rep_area: float = 0.0
    rep_crop: Optional[np.ndarray] = None  # largest person crop, for the optional VLM
    prev_foot: Optional[tuple[float, float]] = None
    line_side: Optional[int] = None  # committed side of the entry line (+1/-1) for hysteresis
    last_cross_ts: Optional[datetime] = None
    cur_zone: Optional[str] = None
    zone_enter_ts: Optional[datetime] = None
    zone_enter_qd: Optional[int] = None
    zone_intervals: list[ZoneInterval] = field(default_factory=list)
    crossings: list[Crossing] = field(default_factory=list)


def _frame_stride(native_fps: float, target_fps: int) -> int:
    return max(1, round(native_fps / max(1, target_fps)))


def process_camera(cam: Camera, video_path: Path, layout: StoreLayout, model) -> list[Tracklet]:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise FileNotFoundError(f"cannot open video: {video_path}")
    native_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    stride = _frame_stride(native_fps, cam.fps_sample)
    clip_total_s = (total_frames / native_fps) if native_fps else 0.0
    clip_start = cam.clip_start
    print(f"[{cam.camera_id}] {video_path.name} native={native_fps:.1f}fps "
          f"stride={stride} sample={cam.fps_sample}fps frames={total_frames}", flush=True)

    states: dict[int, _TrackState] = {}
    frame_idx = -1
    try:
        while True:
            ok = cap.grab()
            if not ok:
                break
            frame_idx += 1
            if frame_idx % stride != 0:
                continue
            ok, frame = cap.retrieve()
            if not ok:
                break
            h, w = frame.shape[:2]
            ts = clip_start + timedelta(seconds=frame_idx / native_fps)

            res = model.track(
                frame, persist=True, conf=CONFIG.det_conf, classes=[CONFIG.person_class_id],
                imgsz=CONFIG.imgsz, tracker="bytetrack.yaml", verbose=False,
            )[0]
            if res.boxes is None or res.boxes.id is None:
                continue
            ids = res.boxes.id.cpu().numpy().astype(int)
            xyxys = res.boxes.xyxy.cpu().numpy()
            confs = res.boxes.conf.cpu().numpy()

            feet = [foot_point(b, w, h) for b in xyxys]

            # queue depth = number of tracks standing in the billing queue region NOW
            queue_depth = 0
            if cam.queue_region:
                queue_depth = sum(1 for fp in feet if point_in_polygon(fp, cam.queue_region))

            for tid, box, conf, fp in zip(ids, xyxys, confs, feet):
                st = states.get(tid)
                if st is None:
                    st = _TrackState(track_id=tid, t_start=ts, t_end=ts)
                    states[tid] = st
                st.t_end = ts
                st.n_frames += 1
                st.conf_sum += float(conf)

                # appearance + staff colour signal (every other sampled frame is enough)
                if st.n_frames % 2 == 1:
                    desc = appearance_descriptor(frame, box)
                    if desc is not None:
                        st.desc_sum = desc if st.desc_sum is None else st.desc_sum + desc
                        st.desc_n += 1
                    st.dark_sum += dark_fraction(frame, box)
                    st.dark_n += 1

                if cam.staff_region and point_in_polygon(fp, cam.staff_region):
                    st.behind_counter_n += 1

                # keep the largest person crop — used for the OSNet Re-ID embedding
                # and (optionally) the VLM staff check.
                if CONFIG.use_osnet_reid or CONFIG.use_vlm_staff:
                    x1, y1, x2, y2 = [int(v) for v in box]
                    area = max(0, x2 - x1) * max(0, y2 - y1)
                    if area > st.rep_area and (x2 > x1) and (y2 > y1):
                        st.rep_area = area
                        st.rep_crop = frame[max(0, y1):y2, max(0, x1):x2].copy()

                # entry-line crossings via stateful hysteresis: a track stays committed
                # to one side until its feet clearly reach the other side (> crossing_margin),
                # so a *gradual* walker counts once and on-the-line jitter is ignored. This
                # is the "count only when fully entered" rule. Debounced as a second guard.
                if cam.entry_line and cam.inside_point:
                    cur_side = side_of(cam.entry_line[0], cam.entry_line[1], fp, CONFIG.crossing_margin)
                    if cur_side != 0:
                        if st.line_side is None:
                            st.line_side = cur_side
                        elif cur_side != st.line_side:
                            inside_side = side_of(cam.entry_line[0], cam.entry_line[1], cam.inside_point)
                            direction = "inbound" if cur_side == inside_side else "outbound"
                            recent = (st.last_cross_ts is not None and
                                      (ts - st.last_cross_ts).total_seconds() < CONFIG.crossing_debounce_seconds)
                            if not recent:
                                st.crossings.append(Crossing(t=ts, direction=direction))
                                st.last_cross_ts = ts
                            st.line_side = cur_side
                st.prev_foot = fp

                # zone membership transitions
                zone = layout.zone_at(cam.camera_id, fp)
                zid = zone.zone_id if zone else None
                if zid != st.cur_zone:
                    _close_zone(st, ts, layout, cam.camera_id)
                    st.cur_zone = zid
                    st.zone_enter_ts = ts
                    st.zone_enter_qd = queue_depth if zid == "BILLING" else None
            # end per-track
    finally:
        cap.release()  # always release the capture (CV skill convention)

    # finalise
    tracklets: list[Tracklet] = []
    for st in states.values():
        _close_zone(st, st.t_end, layout, cam.camera_id)
        if st.n_frames == 0:
            continue
        # identity descriptor: OSNet embedding of the best crop, with HSV histogram fallback
        desc = reid.embed_crop(st.rep_crop) if CONFIG.use_osnet_reid else None
        if desc is None and st.desc_sum is not None and st.desc_n:
            desc = (st.desc_sum / st.desc_n).tolist()
        if CONFIG.use_vlm_staff and st.rep_crop is not None:
            crop_dir = Path(__file__).resolve().parent.parent / "data" / "_vlm_crops"
            crop_dir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(crop_dir / f"{cam.camera_id}__{st.track_id}.jpg"), st.rep_crop)
        dur = max(1e-6, (st.t_end - st.t_start).total_seconds())
        # drop sub-threshold ghost crossings
        crossings = st.crossings if dur >= CONFIG.min_track_seconds_for_entry else []
        tracklets.append(Tracklet(
            camera_id=cam.camera_id, role=cam.role, local_track_id=st.track_id,
            t_start=st.t_start, t_end=st.t_end, n_frames=st.n_frames,
            conf_mean=st.conf_sum / st.n_frames, descriptor=desc,
            dark_fraction=(st.dark_sum / st.dark_n) if st.dark_n else 0.0,
            behind_counter_frac=st.behind_counter_n / st.n_frames,
            in_backroom=(cam.role == "backroom"),
            clip_fraction=min(1.0, dur / clip_total_s) if clip_total_s else 0.0,
            zone_intervals=st.zone_intervals, crossings=crossings,
        ))
    print(f"[{cam.camera_id}] tracks={len(tracklets)} "
          f"crossings={sum(len(t.crossings) for t in tracklets)}", flush=True)
    return tracklets


def _close_zone(st: _TrackState, ts: datetime, layout: StoreLayout, camera_id: str) -> None:
    if st.cur_zone and st.zone_enter_ts is not None:
        zone = layout.zones[st.cur_zone]
        st.zone_intervals.append(ZoneInterval(
            zone_id=zone.zone_id, department=zone.department,
            t_enter=st.zone_enter_ts, t_exit=ts, camera_id=camera_id,
            queue_depth_at_join=st.zone_enter_qd,
        ))
    st.zone_enter_ts = None
    st.zone_enter_qd = None


def run(footage_dir: Path, out_path: Path, layout: StoreLayout, raw_pos: Optional[Path]) -> int:
    from ultralytics import YOLO  # local import: keeps cv2-free modules importable without it

    model = YOLO(CONFIG.model_weights)
    pos_times = []
    if raw_pos and raw_pos.exists():
        from pos import txn_times_utc
        pos_times = txn_times_utc(raw_pos)
        print(f"[pos] loaded {len(pos_times)} transaction times for abandonment/conversion", flush=True)

    all_tracklets: list[Tracklet] = []
    for cam in layout.cameras.values():
        vp = footage_dir / cam.source_file
        if not vp.exists():
            print(f"[warn] missing clip for {cam.camera_id}: {vp}", file=sys.stderr)
            continue
        all_tracklets.extend(process_camera(cam, vp, layout, model))

    if CONFIG.use_vlm_staff:
        _apply_vlm_staff(all_tracklets, footage_dir, layout)

    mgr = SessionManager(store_id=layout.store_id, pos_txn_times=pos_times)
    mgr.ingest(all_tracklets)
    events = mgr.build_events()

    with EventWriter(out_path) as w:
        for e in events:
            w.write(e)
    # also emit the official multi-source schema (the provided sample_events.jsonl shape)
    import json
    official = mgr.build_official_events()
    official_path = out_path.with_name(out_path.stem + "_official.jsonl")
    with official_path.open("w", encoding="utf-8") as fh:
        for e in official:
            fh.write(json.dumps(e, separators=(",", ":")) + "\n")

    cust = [s for s in mgr.sessions if not s.is_staff]
    print(f"\n=== wrote {w.count} events -> {out_path}")
    print(f"=== wrote {len(official)} official-schema events -> {official_path}")
    print(f"sessions={len(mgr.sessions)} customers={len(cust)} staff={len(mgr.sessions)-len(cust)}")
    return w.count


def _apply_vlm_staff(tracklets, footage_dir, layout) -> None:
    """Optional: re-classify staff with a VLM (Claude Vision). See staff_vlm.py."""
    try:
        from staff_vlm import classify_tracklets_with_vlm
        classify_tracklets_with_vlm(tracklets, footage_dir, layout)
    except Exception as exc:  # never let the optional path break a run
        print(f"[vlm] skipped ({exc})", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description="Store-intelligence detection pipeline")
    here = Path(__file__).resolve().parent
    ap.add_argument("--footage", default=str(here.parent.parent / "CCTV Footage"),
                    help="directory containing the CAM *.mp4 clips")
    ap.add_argument("--out", default=str(here.parent / "data" / "events.jsonl"))
    ap.add_argument("--layout", default=None, help="path to store_layout.json")
    ap.add_argument("--pos", default=None, help="raw POS CSV (for abandonment/conversion)")
    args = ap.parse_args()
    layout = get_layout(args.layout)
    run(Path(args.footage), Path(args.out), layout, Path(args.pos) if args.pos else None)


if __name__ == "__main__":
    main()

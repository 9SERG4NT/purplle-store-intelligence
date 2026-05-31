"""Visual sanity-check for the hand-calibrated zones in store_layout.json.

Draws every zone polygon, the entry counting line, and billing staff/queue
regions on a representative frame from each camera, then writes annotated
images to data/_zonecheck/. Use this to re-calibrate coordinates if footage
or camera placement changes.

    python scripts/calibrate_zones.py --footage "../CCTV Footage"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from layout import get_layout  # noqa: E402


def _poly_px(poly, w, h):
    return np.array([[int(x * w), int(y * h)] for x, y in poly], dtype=np.int32)


def main() -> None:
    ap = argparse.ArgumentParser()
    here = Path(__file__).resolve().parent
    ap.add_argument("--footage", default=str(here.parent.parent.parent / "CCTV Footage"))
    ap.add_argument("--out", default=str(here.parent / "data" / "_zonecheck"))
    args = ap.parse_args()

    layout = get_layout()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    for cam in layout.cameras.values():
        vp = Path(args.footage) / cam.source_file
        if not vp.exists():
            print(f"[skip] {cam.camera_id}: {vp} missing")
            continue
        cap = cv2.VideoCapture(str(vp))
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * 0.6))
        ok, frame = cap.read()
        cap.release()
        if not ok:
            continue
        h, w = frame.shape[:2]

        for zid in cam.zones:
            z = layout.zones[zid]
            cv2.polylines(frame, [_poly_px(z.polygon, w, h)], True, (0, 255, 0), 2)
            p0 = z.polygon[0]
            cv2.putText(frame, z.zone_id, (int(p0[0] * w), int(p0[1] * h) + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        if cam.staff_region:
            cv2.polylines(frame, [_poly_px(cam.staff_region, w, h)], True, (255, 0, 255), 2)
        if cam.queue_region:
            cv2.polylines(frame, [_poly_px(cam.queue_region, w, h)], True, (255, 128, 0), 2)
        if cam.entry_line and cam.inside_point:
            (ax, ay), (bx, by) = cam.entry_line
            cv2.line(frame, (int(ax * w), int(ay * h)), (int(bx * w), int(by * h)), (0, 0, 255), 3)
            ix, iy = cam.inside_point
            cv2.circle(frame, (int(ix * w), int(iy * h)), 8, (0, 0, 255), -1)
            cv2.putText(frame, "INSIDE", (int(ix * w) + 10, int(iy * h)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

        dst = out_dir / f"{cam.camera_id}_zones.jpg"
        cv2.imwrite(str(dst), frame)
        print(f"[ok] {cam.camera_id} -> {dst}")


if __name__ == "__main__":
    main()

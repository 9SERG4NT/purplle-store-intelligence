"""One-off: run store-1 detection ONCE, cache tracklets, then sweep the Re-ID
appearance threshold instantly to find a value that doesn't over/under-merge.
Not part of the shipped pipeline — a calibration tool."""
import sys, pickle
from pathlib import Path
sys.path.insert(0, "pipeline")

from config import CONFIG
from layout import get_layout
from associate import SessionManager
import detect
from pos import txn_times_utc

HERE = Path(__file__).resolve().parent.parent
layout = get_layout(str(HERE / "data" / "store_layout.json"))
footage = HERE.parent / "CCTV Footage"
cache = Path("/tmp/store1_tracklets.pkl")

if cache.exists():
    tracklets = pickle.loads(cache.read_bytes())
    print(f"loaded {len(tracklets)} cached tracklets")
else:
    from ultralytics import YOLO
    model = YOLO(CONFIG.model_weights)
    tracklets = []
    for cam in layout.cameras.values():
        vp = footage / cam.source_file
        if vp.exists():
            tracklets += detect.process_camera(cam, vp, layout, model)
    cache.write_bytes(pickle.dumps(tracklets))
    print(f"cached {len(tracklets)} tracklets")

pos_path = HERE / "data" / "pos_transactions.csv"
if not pos_path.exists():
    pos_path = HERE / "data" / "demo_pos.csv"
pos_times = txn_times_utc(pos_path) if pos_path.exists() else []

print(f"\nthr  fac   customers  staff")
for thr, fac in [(0.60, 0.72), (0.66, 0.85), (0.70, 0.85), (0.72, 0.88), (0.76, 0.90), (0.80, 0.92)]:
    object.__setattr__(CONFIG, "appearance_match_threshold", thr)
    object.__setattr__(CONFIG, "cross_camera_match_factor", fac)
    mgr = SessionManager(store_id=layout.store_id, pos_txn_times=pos_times)
    mgr.ingest(tracklets)
    cust = [s for s in mgr.sessions if not s.is_staff]
    print(f"{thr:.2f} {fac:.2f}   {len(cust):3d}        {len(mgr.sessions) - len(cust)}")

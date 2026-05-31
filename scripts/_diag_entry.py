"""Throwaway diagnostic: where do feet actually go on the entry camera?"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import cv2, warnings
warnings.filterwarnings("ignore")
from ultralytics import YOLO
from geometry import foot_point

cap = cv2.VideoCapture("../CCTV Footage/CAM 3.mp4")
native = cap.get(cv2.CAP_PROP_FPS); stride = max(1, round(native / 6))
m = YOLO("yolov8n.pt")
tracks = {}  # tid -> list of (x,y)
idx = -1
while True:
    ok = cap.grab()
    if not ok: break
    idx += 1
    if idx % stride: continue
    ok, fr = cap.retrieve()
    if not ok: break
    h, w = fr.shape[:2]
    r = m.track(fr, persist=True, classes=[0], conf=0.15, imgsz=640, tracker="bytetrack.yaml", verbose=False)[0]
    if r.boxes is None or r.boxes.id is None: continue
    for tid, box in zip(r.boxes.id.cpu().numpy().astype(int), r.boxes.xyxy.cpu().numpy()):
        tracks.setdefault(int(tid), []).append(foot_point(box, w, h))
cap.release()

subst = {t: pts for t, pts in tracks.items() if len(pts) >= 4}
print(f"total tracks={len(tracks)} substantial(>=4)={len(subst)}")
# vertical line x~0.43 vs horizontal line y~0.45 — which do tracks span?
span_x = span_y = 0
for t, pts in subst.items():
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    if min(xs) < 0.40 and max(xs) > 0.46: span_x += 1
    if min(ys) < 0.40 and max(ys) > 0.50: span_y += 1
    print(f"  t{t:>3} n={len(pts):>3} x[{min(xs):.2f},{max(xs):.2f}] y[{min(ys):.2f},{max(ys):.2f}]")
print(f"tracks spanning vertical x=0.43 line: {span_x}")
print(f"tracks spanning horizontal y=0.45 line: {span_y}")

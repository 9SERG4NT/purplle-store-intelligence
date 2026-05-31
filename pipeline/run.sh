#!/usr/bin/env bash
# One command to process all clips -> events.jsonl (+ normalised POS CSV).
#
# Usage:
#   bash pipeline/run.sh
# Override paths via env vars (defaults assume the dataset sits one level above the repo):
#   FOOTAGE=/path/to/clips RAW_POS=/path/to/pos.csv bash pipeline/run.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

FOOTAGE="${FOOTAGE:-$REPO/../CCTV Footage}"
RAW_POS="${RAW_POS:-$REPO/../Brigade_Bangalore_10_April_26 (1)bc6219c.csv}"
OUT="${OUT:-$REPO/data/events.jsonl}"
POS_OUT="${POS_OUT:-$REPO/data/pos_transactions.csv}"

echo "footage : $FOOTAGE"
echo "raw pos : $RAW_POS"
echo "events  : $OUT"

# 1) Normalise the raw POS export into the clean schema the API ingests.
if [ -f "$RAW_POS" ]; then
  python "$HERE/pos.py" "$RAW_POS" "$POS_OUT"
else
  echo "[warn] raw POS not found; skipping POS normalisation"
fi

# 2) Run detection + tracking + association -> events.jsonl
python "$HERE/detect.py" --footage "$FOOTAGE" --out "$OUT" \
  $([ -f "$RAW_POS" ] && echo --pos "$RAW_POS")

echo "done -> $OUT"

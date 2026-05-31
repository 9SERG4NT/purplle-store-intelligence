# PROMPT: "Write pytest tests that (1) validate store_layout.json loads into the
#   StoreLayout model and zone_at() maps a foot point to the right zone, and (2)
#   verify the POS loader is idempotent — loading the same clean CSV twice inserts
#   rows once."
# CHANGES MADE: Used a point known to lie inside the SKINCARE polygon on CAM_FLOOR_01
#   and asserted the entry camera exposes an entry_line; kept the POS CSV inline so
#   the test has no external file dependency.
from __future__ import annotations

from sqlalchemy import func, select

from app.models import PosTransaction
from app.services.pos_loader import load_pos_csv
from layout import get_layout


def test_store_layout_loads_and_maps_zone():
    layout = get_layout()
    assert layout.store_id == "STORE_BLR_002"
    assert "SKINCARE" in layout.zones and "BILLING" in layout.zones
    entry = layout.cameras["CAM_ENTRY_01"]
    assert entry.role == "entry" and entry.entry_line and entry.inside_point
    # a point inside the SKINCARE polygon on the floor camera
    zone = layout.zone_at("CAM_FLOOR_01", (0.4, 0.5))
    assert zone is not None and zone.zone_id == "SKINCARE"


def test_pos_loader_is_idempotent(db, tmp_path):
    csv = tmp_path / "pos.csv"
    csv.write_text(
        "store_id,transaction_id,timestamp,basket_value_inr\n"
        "STORE_BLR_002,TXN_1,2026-04-10T14:55:00Z,1240.0\n"
        "STORE_BLR_002,TXN_2,2026-04-10T15:01:00Z,680.0\n",
        encoding="utf-8",
    )
    assert load_pos_csv(db, csv) == 2
    assert load_pos_csv(db, csv) == 0  # second load inserts nothing
    total = db.execute(select(func.count()).select_from(PosTransaction)).scalar_one()
    assert total == 2

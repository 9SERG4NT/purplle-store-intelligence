"""Load the clean POS CSV into the pos_transactions table (idempotent upsert)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import PosTransaction


def load_pos_csv(db: DbSession, csv_path: str | Path) -> int:
    csv_path = Path(csv_path)
    if not csv_path.exists():
        return 0
    df = pd.read_csv(csv_path)
    required = {"store_id", "transaction_id", "timestamp", "basket_value_inr"}
    if not required.issubset(df.columns):
        raise ValueError(f"POS CSV missing columns: {required - set(df.columns)}")

    existing = set(db.execute(select(PosTransaction.transaction_id)).scalars().all())
    added = 0
    for _, row in df.iterrows():
        tid = str(row["transaction_id"])
        if tid in existing:
            continue
        db.add(PosTransaction(
            transaction_id=tid,
            store_id=str(row["store_id"]),
            ts=_parse(str(row["timestamp"])),
            basket_value_inr=float(row["basket_value_inr"]),
        ))
        existing.add(tid)
        added += 1
    db.commit()
    return added


def _parse(ts: str) -> datetime:
    dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
    return dt.replace(tzinfo=timezone.utc)

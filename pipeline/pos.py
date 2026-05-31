"""Normalise the raw Purplle POS export into the clean schema the brief defines.

Raw export is line-level (one row per SKU). The brief's schema is order-level:
    store_id, transaction_id, timestamp, basket_value_inr
A transaction == one order_id; basket_value == sum(total_amount) over its lines;
timestamp == order_date + order_time, IST -> UTC.

The clean CSV is what the API ingests, keeping the API decoupled from the messy
40-column raw file.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))  # India has no DST; fixed offset is exact.


def _parse_ts(order_date: str, order_time: str) -> datetime:
    """order_date is DD-MM-YYYY, order_time is HH:MM:SS, both in IST."""
    dt = datetime.strptime(f"{order_date.strip()} {order_time.strip()}", "%d-%m-%Y %H:%M:%S")
    return dt.replace(tzinfo=IST)


def normalise(raw_csv: Path | str, store_id_out: str = "STORE_BLR_002") -> list[dict]:
    """Collapse SKU lines into order-level transactions in the clean schema."""
    raw_csv = Path(raw_csv)
    orders: dict[str, dict] = {}
    with raw_csv.open(newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            oid = (row.get("order_id") or "").strip()
            if not oid:
                continue
            try:
                amount = float(row.get("total_amount") or 0.0)
            except ValueError:
                amount = 0.0
            if oid not in orders:
                orders[oid] = {
                    "store_id": store_id_out,
                    "transaction_id": f"TXN_{oid}",
                    "_dt": _parse_ts(row["order_date"], row["order_time"]),
                    "basket_value_inr": 0.0,
                }
            orders[oid]["basket_value_inr"] += amount

    out = []
    for o in orders.values():
        out.append({
            "store_id": o["store_id"],
            "transaction_id": o["transaction_id"],
            "timestamp": o["_dt"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "basket_value_inr": round(o["basket_value_inr"], 2),
        })
    out.sort(key=lambda r: r["timestamp"])
    return out


def write_clean_csv(raw_csv: Path | str, out_csv: Path | str, store_id_out: str = "STORE_BLR_002") -> int:
    rows = normalise(raw_csv, store_id_out)
    out_csv = Path(out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["store_id", "transaction_id", "timestamp", "basket_value_inr"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def txn_times_utc(raw_csv: Path | str) -> list[datetime]:
    """Transaction timestamps (UTC, tz-aware) for the pipeline's abandonment logic."""
    return [datetime.strptime(r["timestamp"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            for r in normalise(raw_csv)]


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Normalise raw POS export to clean schema.")
    ap.add_argument("raw_csv")
    ap.add_argument("out_csv", nargs="?", default="data/pos_transactions.csv")
    args = ap.parse_args()
    n = write_clean_csv(args.raw_csv, args.out_csv)
    print(f"wrote {n} transactions -> {args.out_csv}")

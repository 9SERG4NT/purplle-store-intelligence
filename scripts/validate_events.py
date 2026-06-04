"""Validate an event-log JSONL file against the official sample_events.jsonl schema.

The challenge's held-out set (and our submitted event log) uses a multi-source
schema with three event families:

  entry / exit                  -> id_token, store_code, event_timestamp, is_staff, demographics
  zone_entered / zone_exited    -> track_id, store_id, zone_id, event_time, zone_* , hotspots
  queue_completed / queue_abandoned -> queue_event_id, queue_*_ts, wait_seconds, abandoned

Usage:
    python scripts/validate_events.py data/sample_events.jsonl [more.jsonl ...]

Exit code is non-zero if any HARD error is found (bad JSON, unknown event_type,
missing required field, unparseable timestamp). "Recommended" fields that the
official sample always carries (camera_id, demographics, zone hotspots, ...) are
reported as WARNINGS only — they enrich the stream but are not required to parse.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

# Windows consoles default to cp1252 and choke on the ✓/⚠/✗ glyphs below.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

OFFICIAL_TYPES = {
    "entry", "exit", "zone_entered", "zone_exited",
    "queue_completed", "queue_abandoned",
}

# required = must be present & non-null to be a valid event of this family
# recommended = present in the official sample; absence is a warning, not an error
SCHEMA = {
    "entry": {
        "required": {"event_type", "id_token", "store_code", "event_timestamp", "is_staff"},
        "recommended": {"camera_id", "gender_pred", "age_pred", "age_bucket",
                        "is_face_hidden", "group_id", "group_size"},
        "ts": ["event_timestamp"],
    },
    "exit": {
        "required": {"event_type", "id_token", "store_code", "event_timestamp", "is_staff"},
        "recommended": {"camera_id", "gender_pred", "age_pred", "age_bucket",
                        "is_face_hidden", "group_id", "group_size"},
        "ts": ["event_timestamp"],
    },
    "zone_entered": {
        "required": {"event_type", "store_id", "zone_id", "event_time"},
        "id_one_of": {"track_id", "id_token"},
        "recommended": {"camera_id", "zone_name", "zone_type", "is_revenue_zone",
                        "zone_hotspot_x", "zone_hotspot_y", "gender", "age", "age_bucket"},
        "ts": ["event_time"],
    },
    "zone_exited": {
        "required": {"event_type", "store_id", "zone_id", "event_time"},
        "id_one_of": {"track_id", "id_token"},
        "recommended": {"camera_id", "zone_name", "zone_type", "is_revenue_zone",
                        "zone_hotspot_x", "zone_hotspot_y", "gender", "age", "age_bucket"},
        "ts": ["event_time"],
    },
    "queue_completed": {
        "required": {"queue_event_id", "event_type", "store_id", "zone_id",
                     "queue_join_ts", "abandoned"},
        "id_one_of": {"track_id", "id_token"},
        "recommended": {"camera_id", "zone_name", "zone_type", "is_revenue_zone",
                        "queue_served_ts", "queue_exit_ts", "wait_seconds",
                        "queue_position_at_join", "zone_hotspot_x", "zone_hotspot_y",
                        "gender", "age", "age_bucket"},
        "ts": ["queue_join_ts"],
    },
    "queue_abandoned": {
        "required": {"queue_event_id", "event_type", "store_id", "zone_id",
                     "queue_join_ts", "abandoned"},
        "id_one_of": {"track_id", "id_token"},
        "recommended": {"camera_id", "zone_name", "zone_type", "is_revenue_zone",
                        "queue_served_ts", "queue_exit_ts", "wait_seconds",
                        "queue_position_at_join", "zone_hotspot_x", "zone_hotspot_y",
                        "gender", "age", "age_bucket"},
        "ts": ["queue_join_ts"],
    },
}


def _parse_ts(v: object) -> bool:
    if not isinstance(v, str) or not v:
        return False
    try:
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def validate_file(path: Path) -> tuple[int, int, int]:
    """Returns (n_events, n_errors, n_warnings) and prints a report for `path`."""
    errors: list[str] = []
    warnings: list[str] = []
    type_counts: dict[str, int] = {}
    missing_recommended: dict[str, int] = {}
    n = 0

    if not path.exists():
        print(f"  ✗ file not found: {path}")
        return (0, 1, 0)

    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        n += 1
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError as exc:
            errors.append(f"line {lineno}: invalid JSON ({exc})")
            continue
        if not isinstance(ev, dict):
            errors.append(f"line {lineno}: not a JSON object")
            continue

        et = ev.get("event_type")
        type_counts[et] = type_counts.get(et, 0) + 1
        if et not in OFFICIAL_TYPES:
            errors.append(f"line {lineno}: unknown event_type {et!r} "
                          f"(expected one of {sorted(OFFICIAL_TYPES)})")
            continue

        rules = SCHEMA[et]
        for f in rules["required"]:
            if f not in ev or ev[f] is None:
                errors.append(f"line {lineno} [{et}]: missing required field {f!r}")
        if "id_one_of" in rules and not (rules["id_one_of"] & {k for k, v in ev.items() if v is not None}):
            errors.append(f"line {lineno} [{et}]: needs one of "
                          f"{sorted(rules['id_one_of'])} for visitor identity")
        for f in rules["ts"]:
            if f in ev and ev[f] is not None and not _parse_ts(ev[f]):
                errors.append(f"line {lineno} [{et}]: unparseable timestamp {f}={ev[f]!r}")
        for f in rules["recommended"]:
            if f not in ev:
                missing_recommended[f] = missing_recommended.get(f, 0) + 1

    # roll missing-recommended into warnings (one line per field)
    for f, c in sorted(missing_recommended.items()):
        warnings.append(f"recommended field {f!r} absent on {c} event(s)")

    print(f"\n{'='*70}\n{path}")
    print(f"  events: {n}   types: " +
          ", ".join(f"{k}={v}" for k, v in sorted(type_counts.items(), key=lambda x: str(x[0]))))
    if errors:
        print(f"  ✗ {len(errors)} ERROR(S):")
        for e in errors[:25]:
            print(f"      - {e}")
        if len(errors) > 25:
            print(f"      … and {len(errors) - 25} more")
    else:
        print("  ✓ no hard errors — valid JSONL, all events match the official schema")
    if warnings:
        print(f"  ⚠ {len(warnings)} warning(s) (enrichment fields the sample carries):")
        for w in warnings:
            print(f"      - {w}")
    return (n, len(errors), len(warnings))


def main(argv: list[str]) -> int:
    files = argv or ["data/sample_events.jsonl"]
    total_err = 0
    total_events = 0
    for f in files:
        n, e, _w = validate_file(Path(f))
        total_events += n
        total_err += e
    print(f"\n{'='*70}\nTOTAL: {total_events} events across {len(files)} file(s), "
          f"{total_err} hard error(s) → {'PASS ✓' if total_err == 0 else 'FAIL ✗'}")
    return 0 if total_err == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

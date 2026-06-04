"""Global session association + event generation (cv2-free, fully testable).

This turns per-camera tracklets into a coherent store-level event stream:

  * Entry crossings seed visitor sessions; re-entries reuse the same visitor_id.
  * Floor / billing tracklets are absorbed into the best-matching session
    (appearance + temporal gating), which is also how we dedup the overlapping
    floor/entry camera FOVs. Unmatched floor activity becomes a standalone
    "floor_only" session so zone analytics is never silently dropped.
  * Billing presence is correlated with POS transaction times to decide
    conversion vs queue abandonment.

The design choice — anchoring counting on the entry camera and treating
cross-camera appearance Re-ID as best-effort — is argued in CHOICES.md.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import CONFIG
from emit import build_event
from tracklets import Tracklet, ZoneInterval, descriptor_similarity


def _visitor_id() -> str:
    return "VIS_" + uuid.uuid4().hex[:8]


def _zone_kind(department: str) -> tuple[str, bool]:
    """Derive (zone_type, is_revenue) from a zone's department for official events."""
    d = (department or "").lower()
    if d == "billing":
        return "BILLING", True
    if d in ("entry", "backroom"):
        return d.upper(), False
    return "SHELF", True  # product departments (skin/makeup/personal-care/...) are revenue zones


def classify_staff(t: Tracklet) -> bool:
    """Combine signals into an is_staff decision. VLM verdict wins when present."""
    if t.vlm_is_staff is not None:
        return t.vlm_is_staff
    if t.in_backroom:
        return True
    if t.behind_counter_frac >= 0.5:  # stands on the staff side of the billing counter
        return True
    # Weak signal: dark uniform AND present for most of the clip (customers come and go).
    if t.dark_fraction >= CONFIG.staff_dark_fraction and t.clip_fraction >= CONFIG.staff_persistence_fraction:
        return True
    return False


@dataclass
class Session:
    visitor_id: str
    is_staff: bool
    first_ts: datetime
    last_ts: datetime
    origin: str  # "entry" | "pre_existing" | "floor_only"
    descriptors: dict[str, list[float]] = field(default_factory=dict)
    has_exited: bool = False
    exit_ts: Optional[datetime] = None
    zone_intervals: list[ZoneInterval] = field(default_factory=list)
    crossing_events: list[tuple[str, datetime, float, str]] = field(default_factory=list)
    # ^ (event_type, timestamp, confidence, camera_id) for ENTRY/EXIT/REENTRY

    def absorb_descriptor(self, camera_id: str, desc: Optional[list[float]]) -> None:
        if desc is None:
            return
        prev = self.descriptors.get(camera_id)
        if prev is None:
            self.descriptors[camera_id] = list(desc)
        else:  # running mean within the same camera
            self.descriptors[camera_id] = [(p + d) / 2.0 for p, d in zip(prev, desc)]


class SessionManager:
    def __init__(self, store_id: str, pos_txn_times: Optional[list[datetime]] = None):
        self.store_id = store_id
        self.sessions: list[Session] = []
        self.pos_txn_times = sorted(pos_txn_times or [])

    # ----- matching ---------------------------------------------------------
    def _match(self, t: Tracklet, is_staff: bool, only_exited: bool = False) -> tuple[Optional[Session], float]:
        best: Optional[Session] = None
        best_sim = 0.0
        for s in self.sessions:
            if s.is_staff != is_staff:
                continue
            if only_exited and not s.has_exited:
                continue
            gap = (t.t_start - s.last_ts).total_seconds()
            window = CONFIG.reentry_window_seconds if s.has_exited else CONFIG.transit_window_seconds
            if gap < -3.0 or gap > window:
                continue
            same = descriptor_similarity(s.descriptors.get(t.camera_id), t.descriptor)
            cross = max(
                (descriptor_similarity(d, t.descriptor) for cid, d in s.descriptors.items() if cid != t.camera_id),
                default=0.0,
            )
            # Cross-camera appearance is noisier (different angle/lighting) -> relax threshold.
            same_ok = same >= CONFIG.appearance_match_threshold
            cross_ok = cross >= CONFIG.appearance_match_threshold * CONFIG.cross_camera_match_factor
            sim = max(same, cross)
            if (same_ok or cross_ok) and sim > best_sim:
                best_sim = sim
                best = s
        return best, best_sim

    def _new_session(self, t: Tracklet, is_staff: bool, origin: str) -> Session:
        s = Session(
            visitor_id=_visitor_id(),
            is_staff=is_staff,
            first_ts=t.t_start,
            last_ts=t.t_end,
            origin=origin,
        )
        s.absorb_descriptor(t.camera_id, t.descriptor)
        self.sessions.append(s)
        return s

    # ----- ingestion of tracklets ------------------------------------------
    def add_entry_tracklet(self, t: Tracklet) -> None:
        is_staff = classify_staff(t)
        owner: Optional[Session] = None
        for cr in sorted(t.crossings, key=lambda c: c.t):
            if cr.direction == "inbound":
                reentry, sim = self._match(t, is_staff, only_exited=True)
                if reentry is not None:
                    reentry.has_exited = False
                    reentry.last_ts = max(reentry.last_ts, t.t_end)
                    reentry.absorb_descriptor(t.camera_id, t.descriptor)
                    reentry.crossing_events.append(("REENTRY", cr.t, t.conf_mean, t.camera_id))
                    owner = reentry
                else:
                    open_match, _ = self._match(t, is_staff, only_exited=False)
                    if open_match is not None and owner is None:
                        owner = open_match  # already inside; loitering near door, no new ENTRY
                        owner.last_ts = max(owner.last_ts, t.t_end)
                    else:
                        s = owner or self._new_session(t, is_staff, "entry")
                        s.crossing_events.append(("ENTRY", cr.t, t.conf_mean, t.camera_id))
                        owner = s
            else:  # outbound -> EXIT
                target = owner or self._match(t, is_staff, only_exited=False)[0]
                if target is None:
                    target = self._new_session(t, is_staff, "pre_existing")
                target.has_exited = True
                target.exit_ts = cr.t
                target.last_ts = max(target.last_ts, cr.t)
                target.crossing_events.append(("EXIT", cr.t, t.conf_mean, t.camera_id))
                owner = target
        # NOTE: tracks that never cross the line (people lingering at the threshold,
        # or stationary false detections like a face on a standee) do NOT create a
        # session — entries/exits are counted only from real line crossings.

    def add_zone_tracklet(self, t: Tracklet) -> None:
        is_staff = classify_staff(t)
        match, _ = self._match(t, is_staff)
        if match is None and not t.zone_intervals:
            # an unmatched floor/billing track that never settled in a named zone is
            # a tracking fragment / transient detection -> don't inflate visitor count
            return
        session = match or self._new_session(t, is_staff, "floor_only")
        session.last_ts = max(session.last_ts, t.t_end)
        session.absorb_descriptor(t.camera_id, t.descriptor)
        session.zone_intervals.extend(t.zone_intervals)

    def add_tracklet(self, t: Tracklet) -> None:
        if t.role == "entry":
            self.add_entry_tracklet(t)
        else:
            self.add_zone_tracklet(t)

    def ingest(self, tracklets: list[Tracklet]) -> None:
        for t in sorted(tracklets, key=lambda x: x.t_start):
            self.add_tracklet(t)

    # ----- POS correlation --------------------------------------------------
    def _purchase_follows(self, billing_exit: datetime, window_s: float = 300.0) -> bool:
        """A POS txn within `window_s` AFTER leaving billing => purchase (not abandon)."""
        for tt in self.pos_txn_times:
            if billing_exit <= tt <= billing_exit + timedelta(seconds=window_s):
                return True
        return False

    def _converted(self, billing_join: datetime, billing_exit: datetime) -> bool:
        """Per the brief: in the billing zone within the 5-min window BEFORE a txn."""
        for tt in self.pos_txn_times:
            if billing_join <= tt <= billing_exit + timedelta(seconds=300.0):
                return True
            if tt - timedelta(seconds=300.0) <= billing_exit <= tt:
                return True
        return False

    # ----- event generation -------------------------------------------------
    def build_events(self) -> list[dict]:
        events: list[dict] = []

        def emit(visitor_id, etype, ts, is_staff, conf, camera_id, zone_id=None,
                 dwell_ms=0, queue_depth=None, sku_zone=None):
            events.append({
                "_vid": visitor_id, "_ts": ts,
                "kw": dict(store_id=self.store_id, camera_id=camera_id, visitor_id=visitor_id,
                           event_type=etype, timestamp=ts, is_staff=is_staff,
                           confidence=conf, zone_id=zone_id, dwell_ms=dwell_ms,
                           queue_depth=queue_depth, sku_zone=sku_zone, session_seq=0),
            })

        for s in self.sessions:
            for etype, ts, conf, camera_id in s.crossing_events:
                emit(s.visitor_id, etype, ts, s.is_staff, conf, camera_id)
            for zi in s.zone_intervals:
                if zi.dwell_seconds < CONFIG.min_zone_seconds:
                    continue
                if zi.zone_id == "BILLING":
                    self._emit_billing(emit, s, zi)
                else:
                    self._emit_zone(emit, s, zi)

        # session_seq: ordinal within each visitor, by time
        by_vid: dict[str, list[dict]] = {}
        for e in events:
            by_vid.setdefault(e["_vid"], []).append(e)
        for vid, evs in by_vid.items():
            evs.sort(key=lambda e: (e["_ts"], _ORDER.get(e["kw"]["event_type"], 5)))
            for i, e in enumerate(evs, start=1):
                e["kw"]["session_seq"] = i

        events.sort(key=lambda e: (e["_ts"], e["_vid"]))
        return [build_event(**e["kw"]) for e in events]

    # ----- official multi-source schema (matches the provided sample_events.jsonl) ----
    def build_official_events(self) -> list[dict]:
        """Emit events in the provided sample_events.jsonl schema (entry/exit/
        zone_entered/zone_exited/queue_completed/queue_abandoned). We carry
        id_token on every event (= our visitor_id) so the API links a session's
        entry, zone and billing events even though the official zone/queue
        families only key on track_id. Demographics are null and
        is_face_hidden=true — faces are blurred, so we don't fabricate them."""
        groups = self._assign_groups()
        out: list[dict] = []

        def iso(dt: datetime) -> str:
            return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat()

        for s in self.sessions:
            tid = (int(s.visitor_id.split("_")[-1], 16) % 100000) if "_" in s.visitor_id else abs(hash(s.visitor_id)) % 100000
            gid, gsize = groups.get(s.visitor_id, (None, None))
            for etype, ts, _conf, cam in s.crossing_events:
                kind = "exit" if etype == "EXIT" else "entry"
                out.append({
                    "event_type": kind, "id_token": s.visitor_id, "store_code": self.store_id,
                    "camera_id": cam, "event_timestamp": iso(ts), "is_staff": s.is_staff,
                    "gender_pred": None, "age_pred": None, "age_bucket": None,
                    "is_face_hidden": True, "group_id": gid, "group_size": gsize,
                })
            for zi in s.zone_intervals:
                if zi.dwell_seconds < CONFIG.min_zone_seconds:
                    continue
                if zi.zone_id == "BILLING":
                    out.append(self._official_queue(s, zi, tid, iso))
                else:
                    ztype, rev = _zone_kind(zi.department)
                    for kind, t in (("zone_entered", zi.t_enter), ("zone_exited", zi.t_exit)):
                        out.append({
                            "event_type": kind, "id_token": s.visitor_id, "track_id": tid,
                            "store_id": self.store_id, "camera_id": zi.camera_id, "zone_id": zi.zone_id,
                            "zone_name": zi.zone_id.replace("_", " ").title(), "zone_type": ztype,
                            "is_revenue_zone": "Yes" if rev else "No", "event_time": iso(t),
                            "is_staff": s.is_staff, "gender": None, "age": None, "age_bucket": None,
                        })
        out.sort(key=lambda e: e.get("event_timestamp") or e.get("event_time") or e.get("queue_join_ts"))
        return out

    def _official_queue(self, s: Session, zi: ZoneInterval, tid: int, iso) -> dict:
        served = self._purchase_follows(zi.t_exit)
        return {
            "queue_event_id": str(uuid.uuid4()),
            "event_type": "queue_completed" if served else "queue_abandoned",
            "id_token": s.visitor_id, "track_id": tid, "store_id": self.store_id,
            "camera_id": zi.camera_id, "zone_id": "BILLING", "zone_name": "Billing Counter Queue",
            "zone_type": "BILLING", "is_revenue_zone": "Yes",
            "queue_join_ts": iso(zi.t_enter),
            "queue_served_ts": iso(zi.t_exit) if served else None,
            "queue_exit_ts": iso(zi.t_exit),
            "wait_seconds": int(zi.dwell_seconds),
            "queue_position_at_join": zi.queue_depth_at_join,
            "abandoned": not served, "is_staff": s.is_staff,
            "gender": None, "age": None, "age_bucket": None,
        }

    def _assign_groups(self) -> dict[str, tuple]:
        """Visitors entering within GROUP_WINDOW seconds = one group (group entry)."""
        window = 3.0
        arrivals = []
        for s in self.sessions:
            ent = [t for et, t, _c, _cam in s.crossing_events if et in ("ENTRY", "REENTRY")]
            if ent:
                arrivals.append((min(ent), s.visitor_id))
        arrivals.sort()
        groups: dict[str, tuple] = {}
        i = 0
        gnum = 0
        while i < len(arrivals):
            j = i + 1
            while j < len(arrivals) and (arrivals[j][0] - arrivals[i][0]).total_seconds() <= window:
                j += 1
            members = arrivals[i:j]
            if len(members) >= 2:
                gnum += 1
                for _, vid in members:
                    groups[vid] = (f"G_{gnum}", len(members))
            i = j
        return groups

    def _emit_zone(self, emit, s: Session, zi: ZoneInterval) -> None:
        cam = zi.camera_id
        emit(s.visitor_id, "ZONE_ENTER", zi.t_enter, s.is_staff, 1.0, cam,
             zone_id=zi.zone_id, sku_zone=zi.zone_id)
        # ZONE_DWELL every 30s of continuous presence
        step = CONFIG.dwell_emit_seconds
        elapsed = step
        while elapsed <= zi.dwell_seconds:
            emit(s.visitor_id, "ZONE_DWELL",
                 zi.t_enter + timedelta(seconds=elapsed), s.is_staff, 1.0, cam,
                 zone_id=zi.zone_id, dwell_ms=int(elapsed * 1000), sku_zone=zi.zone_id)
            elapsed += step
        emit(s.visitor_id, "ZONE_EXIT", zi.t_exit, s.is_staff, 1.0, cam,
             zone_id=zi.zone_id, dwell_ms=int(zi.dwell_seconds * 1000), sku_zone=zi.zone_id)

    def _emit_billing(self, emit, s: Session, zi: ZoneInterval) -> None:
        cam = zi.camera_id
        qd = zi.queue_depth_at_join
        if qd and qd > 0:
            emit(s.visitor_id, "BILLING_QUEUE_JOIN", zi.t_enter, s.is_staff, 1.0, cam,
                 zone_id="BILLING", queue_depth=qd, sku_zone="BILLING")
        else:
            emit(s.visitor_id, "ZONE_ENTER", zi.t_enter, s.is_staff, 1.0, cam,
                 zone_id="BILLING", sku_zone="BILLING")
        # ZONE_DWELL cadence in billing too (queue dwell matters operationally)
        step = CONFIG.dwell_emit_seconds
        elapsed = step
        while elapsed <= zi.dwell_seconds:
            emit(s.visitor_id, "ZONE_DWELL", zi.t_enter + timedelta(seconds=elapsed),
                 s.is_staff, 1.0, cam, zone_id="BILLING", dwell_ms=int(elapsed * 1000), sku_zone="BILLING")
            elapsed += step
        if not s.is_staff and not self._purchase_follows(zi.t_exit):
            emit(s.visitor_id, "BILLING_QUEUE_ABANDON", zi.t_exit, s.is_staff, 1.0, cam,
                 zone_id="BILLING", queue_depth=qd, sku_zone="BILLING")
        else:
            emit(s.visitor_id, "ZONE_EXIT", zi.t_exit, s.is_staff, 1.0, cam,
                 zone_id="BILLING", dwell_ms=int(zi.dwell_seconds * 1000), sku_zone="BILLING")


_ORDER = {
    "ENTRY": 0, "REENTRY": 0, "ZONE_ENTER": 1, "BILLING_QUEUE_JOIN": 1,
    "ZONE_DWELL": 2, "ZONE_EXIT": 3, "BILLING_QUEUE_ABANDON": 3, "EXIT": 4,
}

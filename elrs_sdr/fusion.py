"""
Sensor fusion — the core of the rewrite.

The predecessor had three separate dashboards (an SDR protocol scanner, an
Open Drone ID web UI, and a DJI DroneID JSON seam) that never shared a view.
This module unifies them: every capture source emits a normalised **event**,
and one `TrackTable` merges events into per-drone **tracks** keyed by identity,
so a single dashboard shows "every drone around me, its protocol, and its
identifiers" regardless of which radio/transport saw it.

Event schema (a plain dict, so sources stay decoupled and JSONL-serialisable):

    {
      "source":    "remoteid" | "sdr" | "dji",   # which sensor
      "ts":        float,                          # epoch seconds
      "kind":      "remote-id" | "control-link" | "dji-droneid",
      "identity":  str | None,     # stable id: serial / operator / MAC / None
      "protocol":  str,            # human label ("Wi-Fi Remote ID", "ELRS LoRa")
      "confirmed": bool,           # True only for positively-decoded identity
      "fields":    {...},          # source-specific detail (lat/lon/freq/rssi…)
    }

A source is anything with an `events()` generator yielding those dicts; it runs
on its own thread and feeds the shared TrackTable. See source classes below.
"""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from .log import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Tracks
# ---------------------------------------------------------------------------
@dataclass
class DroneTrack:
    """One physical thing we're tracking, merged across every source."""
    key: str
    kind: str
    source: str
    protocol: str
    confirmed: bool = False
    identity: str | None = None
    first_seen: float = 0.0
    last_seen: float = 0.0
    count: int = 0
    fields: dict = field(default_factory=dict)

    def row(self, now: float) -> dict:
        """Flatten to a JSON-friendly table row for the web UI."""
        f = self.fields
        return {
            "key": self.key,
            "entity_id": f.get("entity_id"),
            "kind": self.kind,
            "source": self.source,
            "protocol": self.protocol,
            "confirmed": self.confirmed,
            "identity": self.identity or "—",
            # pull the common identifier/telemetry fields up for the table
            "serial": f.get("uas_id") or f.get("drone_id") or f.get("mac") or "—",
            "operator": f.get("operator_id") or "—",
            "lat": f.get("latitude"),
            "lon": f.get("longitude"),
            "altitude_m": f.get("altitude_geo_m"),
            "speed_ms": f.get("speed_h_ms"),
            "operator_lat": f.get("operator_latitude"),
            "operator_lon": f.get("operator_longitude"),
            "freq_mhz": f.get("freq_mhz"),
            "rssi_db": f.get("strength_db") or f.get("rssi_db"),
            "metric": f.get("metric", ""),
            "first_seen": round(self.first_seen, 1),
            "last_seen": round(self.last_seen, 1),
            "age_s": round(max(0.0, self.last_seen - self.first_seen), 1),
            "last_seen_s": round(max(0.0, now - self.last_seen), 1),
            "count": self.count,
        }


class TrackTable:
    """Thread-safe registry merging events into identity-keyed tracks."""

    def __init__(self, forget_s: float = 300.0):
        self._tracks: dict[str, DroneTrack] = {}
        self._lock = threading.Lock()
        self.forget_s = forget_s

    @staticmethod
    def _key(ev: dict) -> str:
        """Stable merge key. A real identity wins; else fall back to a
        source/protocol/frequency signature so unidentified signals still
        accumulate as one row instead of churning."""
        entity_id = ev.get("fields", {}).get("entity_id")
        if entity_id:
            return entity_id
        if ev.get("identity"):
            return f"id:{ev['identity']}"
        f = ev.get("fields", {})
        sig = f.get("track_key") or f"{ev['source']}:{ev.get('protocol')}:" \
                                    f"{f.get('freq_mhz')}"
        return f"sig:{sig}"

    def ingest(self, ev: dict) -> None:
        now = ev.get("ts", time.time())
        key = self._key(ev)
        with self._lock:
            t = self._tracks.get(key)
            if t is None:
                t = DroneTrack(key=key, kind=ev.get("kind", "?"),
                               source=ev["source"],
                               protocol=ev.get("protocol", "?"),
                               first_seen=now)
                self._tracks[key] = t
            t.last_seen = now
            t.count += 1
            t.kind = ev.get("kind", t.kind)
            t.source = ev["source"]
            t.protocol = ev.get("protocol", t.protocol)
            # confirmed is sticky-true: once positively identified, stays so
            t.confirmed = t.confirmed or bool(ev.get("confirmed"))
            if ev.get("identity"):
                t.identity = ev["identity"]
            # merge in any non-null detail fields
            for k, v in ev.get("fields", {}).items():
                if v is not None:
                    t.fields[k] = v

    def rows(self, window_s: float = 0.0) -> list[dict]:
        now = time.time()
        cutoff = now - window_s if window_s > 0 else float("-inf")
        with self._lock:
            # prune very old tracks so the table doesn't grow forever
            dead = [k for k, t in self._tracks.items()
                    if now - t.last_seen > self.forget_s]
            for k in dead:
                del self._tracks[k]
            rows = [t.row(now) for t in self._tracks.values()
                    if t.last_seen >= cutoff]
        # confirmed identity first, then most-recently-seen
        rows.sort(key=lambda r: (0 if r["confirmed"] else 1, r["last_seen_s"]))
        return rows

    def summary(self) -> dict:
        with self._lock:
            tracks = list(self._tracks.values())
        confirmed = [t for t in tracks if t.confirmed]
        by_kind: dict[str, int] = {}
        for t in tracks:
            by_kind[t.kind] = by_kind.get(t.kind, 0) + 1
        return {"total": len(tracks), "confirmed": len(confirmed),
                "by_kind": by_kind}


# ---------------------------------------------------------------------------
# Source base + runner
# ---------------------------------------------------------------------------
class Source:
    """A capture source. Subclasses implement events() as a generator."""

    name = "source"
    kind = "?"

    def events(self):  # pragma: no cover - interface
        raise NotImplementedError
        yield  # noqa

    def run_into(self, table: TrackTable, stop: threading.Event) -> None:
        """Pump events() into the table until `stop` is set."""
        try:
            for ev in self.events():
                if stop.is_set():
                    break
                if ev is not None:
                    table.ingest(ev)
        except Exception as e:  # a bad source must not kill the dashboard
            log.warning("source %s stopped: %r", self.name, e)


def start_sources(sources: list[Source], table: TrackTable) -> threading.Event:
    """Spawn one daemon thread per source feeding `table`. Returns a stop event."""
    stop = threading.Event()
    for s in sources:
        th = threading.Thread(target=s.run_into, args=(table, stop),
                              name=f"src-{s.name}", daemon=True)
        th.start()
        log.info("started source: %s (%s)", s.name, s.kind)
    return stop


# ---------------------------------------------------------------------------
# Open Drone ID helper: pack bytes -> event (shared by replay + live transports)
# ---------------------------------------------------------------------------
def remoteid_event(pack_bytes: bytes, transport: str = "replay",
                   rssi_db: float | None = None,
                   ts: float | None = None) -> dict | None:
    """Decode an ODID message pack and build a fusion event, or None."""
    from . import opendroneid as odid
    dec, _ = odid.decode_message(pack_bytes)
    if not dec or "messages" not in dec:
        return None
    report = odid.summarize(dec["messages"])
    fields = {k: v for k, v in report.__dict__.items() if k != "extra"}
    if rssi_db is not None:
        fields["rssi_db"] = rssi_db
    return {
        "source": "remoteid",
        "ts": ts if ts is not None else time.time(),
        "kind": "remote-id",
        "identity": report.identity(),
        "protocol": f"Open Drone ID ({transport})",
        "confirmed": report.identity() is not None,
        "fields": fields,
    }


def detection_event(detection, *, observed_at: str, source: str,
                    compatibility: dict[str, str | None] | None = None,
                    unique_frequency_hz: int | None = None) -> dict:
    """Adapt a detector result into the live fusion event interface.

    The canonical entity ID is carried in ``fields`` so the live table uses
    the same cross-frequency merge rule as offline track building.  Signals
    without a typed identity require an explicit identity-bearing frequency.
    """
    from .identity import detection_observation, stable_entity_id

    observation = detection_observation(
        detection,
        timestamp=observed_at,
        source=source,
        compatibility=compatibility,
        unique_frequency_hz=unique_frequency_hz,
    )
    entity_id = stable_entity_id(observation)
    identity = observation.get("identity")
    fields = {
        "entity_id": entity_id,
        "identity_type": identity["kind"] if identity else "unique_frequency",
        "freq_mhz": observation["frequency_hz"] / 1e6,
        "evidence_maturity": observation["evidence_maturity"],
    }
    for name in ("protocol_version", "phy", "model", "firmware"):
        if name in observation:
            fields[name] = observation[name]
    return {
        "source": source,
        "ts": datetime.fromisoformat(
            observation["timestamp"].replace("Z", "+00:00")
        ).timestamp(),
        "kind": "control-link",
        "identity": identity["value"] if identity else str(unique_frequency_hz),
        "protocol": observation["protocol"],
        "confirmed": observation["evidence_maturity"] == "identified",
        "fields": fields,
    }


# ---------------------------------------------------------------------------
# Replay source — synthetic Remote ID, so the SDR-only operator can see the
# dashboard working now (no BLE/Wi-Fi monitor hardware required).
# ---------------------------------------------------------------------------
class ReplaySource(Source):
    """Emit a couple of moving synthetic Remote ID drones on an interval."""

    name = "replay"
    kind = "remote-id"

    def __init__(self, interval_s: float = 1.0, origin=(37.7749, -122.4194)):
        self.interval_s = interval_s
        self.origin = origin

    def events(self):
        from . import opendroneid as odid
        lat0, lon0 = self.origin
        fleet = [
            {"serial": "1581F5FMC24210ABCD01", "op": "FAA-OP-4F2A9C",
             "d": (0.0012, 0.0008), "ua": 2},
            {"serial": "SIM-RID-7781-XY", "op": "EU-OP-DE-99KZ",
             "d": (-0.0015, 0.0020), "ua": 2},
        ]
        t = 0
        while True:
            for i, d in enumerate(fleet):
                lat = lat0 + d["d"][0] + 0.00002 * t * (1 if i == 0 else -1)
                lon = lon0 + d["d"][1] + 0.00003 * t * (1 if i == 0 else -1)
                pack = odid.build_pack([
                    odid.build_basic_id(d["serial"], id_type=1, ua_type=d["ua"]),
                    odid.build_location(lat, lon, alt_geo_m=90 + t % 30,
                                        height_m=60 + t % 20, speed_h_ms=8 + i * 4),
                    odid.build_system(lat0, lon0),
                    odid.build_operator_id(d["op"]),
                ])
                ev = remoteid_event(pack, transport="replay",
                                    rssi_db=-55 - 5 * i)
                if ev:
                    yield ev
            t += 1
            time.sleep(self.interval_s)


# ---------------------------------------------------------------------------
# SDR protocol-scan source — wraps the band scanner's detections as events.
# This is the presence+protocol layer (drones that don't broadcast Remote ID).
# ---------------------------------------------------------------------------
class SdrScanSource(Source):
    """Drive a DroneScanner and turn its detection rows into control-link events."""

    name = "sdr"
    kind = "control-link"

    def __init__(self, scanner, poll_s: float = 0.1):
        self.scanner = scanner
        self.poll_s = poll_s

    def events(self):
        while True:
            self.scanner.step()
            for row in self.scanner.detections_table():
                yield {
                    "source": "sdr",
                    "ts": time.time(),
                    "kind": "video" if row.get("signature") == "video"
                            else "control-link",
                    "identity": row.get("mac"),      # only Wi-Fi yields an id
                    "protocol": row.get("protocol", "?"),
                    "confirmed": bool(row.get("confirmed")),
                    "fields": {
                        "track_key": row.get("track_key"),
                        "freq_mhz": row.get("freq_mhz"),
                        "strength_db": row.get("strength_db"),
                        "metric": row.get("metric", ""),
                        "mac": row.get("mac"),
                        "manufacturer": row.get("manufacturer"),
                        "hopping": row.get("hopping"),
                    },
                }
            time.sleep(self.poll_s)


# ---------------------------------------------------------------------------
# DJI DroneID source — the SDR-only identity win. Reads the JSON-line output of
# the DroneSecurity receiver (apps/droneid_json_rx.py) as a subprocess. Tolerant
# when the receiver/radio isn't available (logs and exits quietly).
# ---------------------------------------------------------------------------
class DjiDroneIdSource(Source):
    """Consume one-JSON-line-per-frame output from a DJI DroneID receiver."""

    name = "dji"
    kind = "dji-droneid"

    def __init__(self, cmd: list[str]):
        self.cmd = cmd

    def events(self):
        import json
        try:
            proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, text=True)
        except FileNotFoundError:
            log.warning("DJI DroneID receiver not found: %s", self.cmd[:2])
            return
        for line in proc.stdout:
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            yield {
                "source": "dji",
                "ts": d.get("ts", time.time()),
                "kind": "dji-droneid",
                "identity": d.get("drone_id") or d.get("serial"),
                "protocol": "DJI DroneID (OcuSync)",
                "confirmed": bool(d.get("drone_id") or d.get("serial")),
                "fields": {
                    "drone_id": d.get("drone_id") or d.get("serial"),
                    "operator_id": d.get("operator"),
                    "latitude": d.get("lat"),
                    "longitude": d.get("lon"),
                    "altitude_geo_m": d.get("alt"),
                    "speed_h_ms": d.get("speed"),
                    "operator_latitude": d.get("app_lat") or d.get("pilot_lat"),
                    "operator_longitude": d.get("app_lon") or d.get("pilot_lon"),
                    "freq_mhz": d.get("carrier_mhz"),
                },
            }

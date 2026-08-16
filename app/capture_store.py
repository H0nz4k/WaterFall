from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


class CaptureStore:
    """SQLite-backed capture store for WaterFall.

    The database intentionally keeps a generic event model.  Real decoded
    frames (BLE/Wi-Fi) and inferred RF-energy observations therefore live in
    the same timeline without pretending that an energy hit is a decoded
    packet.
    """

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._active_session_id: int | None = None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def _init_db(self) -> None:
        with self.lock, self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    stopped_at TEXT,
                    label TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    host_time TEXT NOT NULL,
                    source TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    direction TEXT NOT NULL DEFAULT '',
                    freq_mhz REAL,
                    channel TEXT,
                    rssi_dbm REAL,
                    device_id TEXT,
                    peer_id TEXT,
                    summary TEXT NOT NULL DEFAULT '',
                    payload_hex TEXT,
                    raw_text TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(session_id) REFERENCES sessions(id)
                );

                CREATE INDEX IF NOT EXISTS ix_events_session_time
                    ON events(session_id, host_time, id);
                CREATE INDEX IF NOT EXISTS ix_events_filter
                    ON events(protocol, event_type, source, freq_mhz, rssi_dbm);
                CREATE INDEX IF NOT EXISTS ix_events_device
                    ON events(device_id);

                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    address TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL DEFAULT '',
                    vendor TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL DEFAULT '',
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    last_rssi_dbm REAL,
                    last_freq_mhz REAL,
                    last_channel TEXT,
                    seen_count INTEGER NOT NULL DEFAULT 1,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS peaks (
                    freq_mhz INTEGER PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    guess TEXT NOT NULL DEFAULT '',
                    protocol_hint TEXT NOT NULL DEFAULT '',
                    explanation TEXT NOT NULL DEFAULT '',
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    seen_count INTEGER NOT NULL DEFAULT 1,
                    peak_rssi_dbm REAL,
                    last_rssi_dbm REAL,
                    width_mhz INTEGER,
                    burst_count INTEGER NOT NULL DEFAULT 0,
                    notes TEXT NOT NULL DEFAULT ''
                );
                """
            )
            row = con.execute(
                "SELECT id FROM sessions WHERE active=1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row:
                # Crash recovery: previous process cannot still own an active capture.
                con.execute(
                    "UPDATE sessions SET active=0, stopped_at=? WHERE active=1",
                    (now_iso(),),
                )
            self._active_session_id = None

    @property
    def active_session_id(self) -> int | None:
        return self._active_session_id

    def start_session(self, label: str = "", notes: str = "") -> dict[str, Any]:
        with self.lock, self._connect() as con:
            if self._active_session_id is not None:
                row = con.execute(
                    "SELECT * FROM sessions WHERE id=?", (self._active_session_id,)
                ).fetchone()
                return dict(row) if row else {"id": self._active_session_id, "active": 1}
            cur = con.execute(
                "INSERT INTO sessions(started_at,label,notes,active) VALUES(?,?,?,1)",
                (now_iso(), label.strip(), notes.strip()),
            )
            self._active_session_id = int(cur.lastrowid)
            row = con.execute(
                "SELECT * FROM sessions WHERE id=?", (self._active_session_id,)
            ).fetchone()
            return dict(row)

    def stop_session(self) -> dict[str, Any] | None:
        with self.lock, self._connect() as con:
            sid = self._active_session_id
            if sid is None:
                return None
            con.execute(
                "UPDATE sessions SET active=0, stopped_at=? WHERE id=?",
                (now_iso(), sid),
            )
            row = con.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
            self._active_session_id = None
            return dict(row) if row else None

    def add_event(
        self,
        *,
        source: str,
        protocol: str,
        event_type: str,
        host_time: str | None = None,
        direction: str = "",
        freq_mhz: float | None = None,
        channel: str | int | None = None,
        rssi_dbm: float | None = None,
        device_id: str | None = None,
        peer_id: str | None = None,
        summary: str = "",
        payload_hex: str | None = None,
        raw_text: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int | None:
        sid = self._active_session_id
        if sid is None:
            return None
        with self.lock, self._connect() as con:
            cur = con.execute(
                """
                INSERT INTO events(
                    session_id,host_time,source,protocol,event_type,direction,
                    freq_mhz,channel,rssi_dbm,device_id,peer_id,summary,
                    payload_hex,raw_text,metadata_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    sid,
                    host_time or now_iso(),
                    source,
                    protocol,
                    event_type,
                    direction,
                    freq_mhz,
                    str(channel) if channel is not None else None,
                    rssi_dbm,
                    device_id,
                    peer_id,
                    summary,
                    payload_hex,
                    raw_text,
                    json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            return int(cur.lastrowid)

    def upsert_device(
        self,
        *,
        device_id: str,
        source: str,
        protocol: str,
        address: str = "",
        name: str = "",
        vendor: str = "",
        kind: str = "",
        rssi_dbm: float | None = None,
        freq_mhz: float | None = None,
        channel: str | int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        stamp = now_iso()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":"))
        with self.lock, self._connect() as con:
            con.execute(
                """
                INSERT INTO devices(
                    id,source,protocol,address,name,vendor,kind,first_seen,last_seen,
                    last_rssi_dbm,last_freq_mhz,last_channel,seen_count,metadata_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,?)
                ON CONFLICT(id) DO UPDATE SET
                    last_seen=excluded.last_seen,
                    source=excluded.source,
                    protocol=excluded.protocol,
                    address=CASE WHEN excluded.address<>'' THEN excluded.address ELSE devices.address END,
                    name=CASE WHEN excluded.name<>'' THEN excluded.name ELSE devices.name END,
                    vendor=CASE WHEN excluded.vendor<>'' THEN excluded.vendor ELSE devices.vendor END,
                    kind=CASE WHEN excluded.kind<>'' THEN excluded.kind ELSE devices.kind END,
                    last_rssi_dbm=COALESCE(excluded.last_rssi_dbm,devices.last_rssi_dbm),
                    last_freq_mhz=COALESCE(excluded.last_freq_mhz,devices.last_freq_mhz),
                    last_channel=COALESCE(excluded.last_channel,devices.last_channel),
                    seen_count=devices.seen_count+1,
                    metadata_json=excluded.metadata_json
                """,
                (
                    device_id, source, protocol, address, name, vendor, kind,
                    stamp, stamp, rssi_dbm, freq_mhz,
                    str(channel) if channel is not None else None, metadata_json,
                ),
            )

    def list_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock, self._connect() as con:
            rows = con.execute(
                """
                SELECT s.*,
                       (SELECT COUNT(*) FROM events e WHERE e.session_id=s.id) AS event_count
                FROM sessions s ORDER BY id DESC LIMIT ?
                """,
                (max(1, min(limit, 1000)),),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_events(
        self,
        *,
        session_id: int | None = None,
        source: str = "",
        protocol: str = "",
        event_type: str = "",
        device_id: str = "",
        q: str = "",
        min_rssi: float | None = None,
        freq_min: float | None = None,
        freq_max: float | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> dict[str, Any]:
        where = []
        params: list[Any] = []
        if session_id is not None:
            where.append("session_id=?")
            params.append(session_id)
        if source:
            where.append("source=?")
            params.append(source)
        if protocol:
            where.append("protocol=?")
            params.append(protocol)
        if event_type:
            where.append("event_type=?")
            params.append(event_type)
        if device_id:
            where.append("device_id=?")
            params.append(device_id)
        if min_rssi is not None:
            where.append("rssi_dbm>=?")
            params.append(min_rssi)
        if freq_min is not None:
            where.append("freq_mhz>=?")
            params.append(freq_min)
        if freq_max is not None:
            where.append("freq_mhz<=?")
            params.append(freq_max)
        if q:
            where.append("(summary LIKE ? OR raw_text LIKE ? OR device_id LIKE ? OR peer_id LIKE ?)")
            pat = f"%{q}%"
            params.extend([pat, pat, pat, pat])
        clause = " WHERE " + " AND ".join(where) if where else ""
        lim = max(1, min(limit, 5000))
        off = max(0, offset)
        with self.lock, self._connect() as con:
            total = int(con.execute("SELECT COUNT(*) FROM events" + clause, params).fetchone()[0])
            rows = con.execute(
                "SELECT * FROM events" + clause + " ORDER BY id DESC LIMIT ? OFFSET ?",
                params + [lim, off],
            ).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                try:
                    d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
                except Exception:
                    d["metadata"] = {}
                result.append(d)
            return {"total": total, "items": result}

    def get_event(self, event_id: int) -> dict[str, Any] | None:
        with self.lock, self._connect() as con:
            row = con.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
            except Exception:
                d["metadata"] = {}
            return d

    def _peak_row(self, con: sqlite3.Connection, freq_mhz: int) -> dict[str, Any] | None:
        row = con.execute("SELECT * FROM peaks WHERE freq_mhz=?", (int(freq_mhz),)).fetchone()
        return dict(row) if row else None

    def upsert_peak(
        self,
        *,
        freq_mhz: int,
        rssi_dbm: float | None = None,
        guess: str = "",
        protocol_hint: str = "",
        explanation: str = "",
        width_mhz: int | None = None,
        burst: bool = False,
        name: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        freq = int(freq_mhz)
        stamp = now_iso()
        with self.lock, self._connect() as con:
            existing = self._peak_row(con, freq)
            if existing is None:
                con.execute(
                    """
                    INSERT INTO peaks(
                        freq_mhz,name,guess,protocol_hint,explanation,first_seen,last_seen,
                        seen_count,peak_rssi_dbm,last_rssi_dbm,width_mhz,burst_count,notes
                    ) VALUES(?,?,?,?,?,?,?,1,?,?,?,?,?)
                    """,
                    (
                        freq,
                        (name or "").strip(),
                        guess,
                        protocol_hint,
                        explanation,
                        stamp,
                        stamp,
                        rssi_dbm,
                        rssi_dbm,
                        width_mhz,
                        1 if burst else 0,
                        (notes or "").strip(),
                    ),
                )
            else:
                peak_rssi = existing["peak_rssi_dbm"]
                if rssi_dbm is not None and (peak_rssi is None or rssi_dbm > peak_rssi):
                    peak_rssi = rssi_dbm
                con.execute(
                    """
                    UPDATE peaks SET
                        last_seen=?,
                        seen_count=seen_count+1,
                        last_rssi_dbm=COALESCE(?, last_rssi_dbm),
                        peak_rssi_dbm=?,
                        guess=CASE WHEN ?<>'' THEN ? ELSE guess END,
                        protocol_hint=CASE WHEN ?<>'' THEN ? ELSE protocol_hint END,
                        explanation=CASE WHEN ?<>'' THEN ? ELSE explanation END,
                        width_mhz=COALESCE(?, width_mhz),
                        burst_count=burst_count+?,
                        name=CASE WHEN ? IS NOT NULL AND ?<>'' THEN ? ELSE name END,
                        notes=CASE WHEN ? IS NOT NULL THEN ? ELSE notes END
                    WHERE freq_mhz=?
                    """,
                    (
                        stamp,
                        rssi_dbm,
                        peak_rssi,
                        guess, guess,
                        protocol_hint, protocol_hint,
                        explanation, explanation,
                        width_mhz,
                        1 if burst else 0,
                        name, (name or "").strip(), (name or "").strip(),
                        notes, (notes or "").strip() if notes is not None else None,
                        freq,
                    ),
                )
            row = self._peak_row(con, freq)
            return row or {"freq_mhz": freq}

    def update_peak(self, freq_mhz: int, *, name: str | None = None, notes: str | None = None) -> dict[str, Any] | None:
        with self.lock, self._connect() as con:
            if self._peak_row(con, freq_mhz) is None:
                return None
            if name is not None:
                con.execute("UPDATE peaks SET name=? WHERE freq_mhz=?", (name.strip(), int(freq_mhz)))
            if notes is not None:
                con.execute("UPDATE peaks SET notes=? WHERE freq_mhz=?", (notes.strip(), int(freq_mhz)))
            return self._peak_row(con, freq_mhz)

    def delete_peak(self, freq_mhz: int) -> bool:
        with self.lock, self._connect() as con:
            cur = con.execute("DELETE FROM peaks WHERE freq_mhz=?", (int(freq_mhz),))
            return cur.rowcount > 0

    def clear_peaks(self) -> int:
        with self.lock, self._connect() as con:
            cur = con.execute("DELETE FROM peaks")
            return int(cur.rowcount)

    def peak_count(self) -> int:
        with self.lock, self._connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM peaks").fetchone()[0])

    def list_peaks(self, q: str = "", limit: int = 500) -> list[dict[str, Any]]:
        where = ""
        params: list[Any] = []
        if q:
            where = " WHERE (CAST(freq_mhz AS TEXT) LIKE ? OR name LIKE ? OR guess LIKE ? OR notes LIKE ?)"
            pat = f"%{q}%"
            params.extend([pat, pat, pat, pat])
        with self.lock, self._connect() as con:
            rows = con.execute(
                "SELECT * FROM peaks" + where + " ORDER BY last_seen DESC LIMIT ?",
                params + [max(1, min(limit, 5000))],
            ).fetchall()
            peaks = [dict(r) for r in rows]
            devices = con.execute(
                "SELECT id,protocol,address,name,last_freq_mhz,last_rssi_dbm FROM devices "
                "WHERE last_freq_mhz IS NOT NULL"
            ).fetchall()
        for peak in peaks:
            freq = float(peak["freq_mhz"])
            linked = []
            for dev in devices:
                last = dev["last_freq_mhz"]
                if last is None:
                    continue
                if abs(float(last) - freq) <= 2:
                    linked.append({
                        "id": dev["id"],
                        "protocol": dev["protocol"],
                        "address": dev["address"],
                        "name": dev["name"],
                        "last_freq_mhz": last,
                        "last_rssi_dbm": dev["last_rssi_dbm"],
                    })
            peak["maybe_devices"] = linked
        return peaks

    def list_devices(self, protocol: str = "", q: str = "", limit: int = 500) -> list[dict[str, Any]]:
        where = []
        params: list[Any] = []
        if protocol:
            where.append("protocol=?")
            params.append(protocol)
        if q:
            where.append("(id LIKE ? OR address LIKE ? OR name LIKE ? OR vendor LIKE ? OR kind LIKE ?)")
            pat = f"%{q}%"
            params.extend([pat] * 5)
        clause = " WHERE " + " AND ".join(where) if where else ""
        with self.lock, self._connect() as con:
            rows = con.execute(
                "SELECT * FROM devices" + clause + " ORDER BY last_seen DESC LIMIT ?",
                params + [max(1, min(limit, 5000))],
            ).fetchall()
            out = []
            for row in rows:
                d = dict(row)
                try:
                    d["metadata"] = json.loads(d.pop("metadata_json") or "{}")
                except Exception:
                    d["metadata"] = {}
                out.append(d)
            return out

    def export_jsonl(self, session_id: int, target: str | Path) -> Path:
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as fp:
            offset = 0
            while True:
                page = self.list_events(session_id=session_id, limit=1000, offset=offset)
                for item in reversed(page["items"]):
                    fp.write(json.dumps(item, ensure_ascii=False) + "\n")
                offset += len(page["items"])
                if offset >= page["total"] or not page["items"]:
                    break
        return target

    def snapshot_db(self, target: str | Path) -> Path:
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            src = self._connect()
            dst = sqlite3.connect(target)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
        return target

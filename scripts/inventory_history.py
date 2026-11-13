"""Write-once stock and movement archives, one atomic SQLite file per China day."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time as clock
import zlib
from datetime import date, datetime, time, timedelta, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CHINA = timezone(timedelta(hours=8))
FREEZE_TIME = time(3, 30)
MAX_EVENTS = 2_000_000
DEFAULT_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local/share") / "RoyalTigerInventory" / "history"


def archive_path(day: str, directory: Path | None = None) -> Path:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError("invalid archive date")
    date.fromisoformat(day)
    return (directory or DEFAULT_DIR) / f"{day}.sqlite3"


def _pack(value: Any) -> tuple[bytes, str]:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return zlib.compress(raw), hashlib.sha256(raw).hexdigest()


def _unpack(blob: bytes, digest: str) -> Any:
    raw = zlib.decompress(blob)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise RuntimeError("inventory archive checksum mismatch")
    return json.loads(raw)


@contextmanager
def _open(day: str, directory: Path | None = None):
    db = sqlite3.connect(archive_path(day, directory).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        yield db
    finally:
        db.close()


def read_metadata(day: str, directory: Path | None = None) -> dict[str, Any]:
    with _open(day, directory) as db:
        record = db.execute("SELECT payload FROM metadata").fetchone()
        if record is None:
            raise RuntimeError("inventory archive is incomplete")
        metadata = json.loads(record[0])
        if metadata.get("stock_date") != day or metadata.get("complete") is not True:
            raise RuntimeError("inventory archive is incomplete")
        return metadata


def read_stock(day: str, parameters: dict[str, Any], directory: Path | None = None) -> dict[str, Any]:
    metadata = read_metadata(day, directory)
    with _open(day, directory) as db:
        record = db.execute("SELECT payload, sha256 FROM stock").fetchone()
        rows = _unpack(*record)
    warehouses = set(parameters.get("warehouse_codes", [])) - {""}
    skus = set(parameters.get("sku_codes", [])) - {""}
    rows = [row for row in rows if (not warehouses or row["仓库编码"] in warehouses) and (not skus or row["SKU编码"] in skus)]
    legacy = metadata.get("stock_formula") != "realtime-minus-pending-out-plus-pending-in"
    corrections = {}
    correction_path = archive_path(day, directory).with_suffix(".corrections.json")
    if legacy and correction_path.exists():
        audit = json.loads(correction_path.read_text(encoding="utf-8"))
        if audit["original_stock_sha256"] != metadata["stock_sha256"]:
            raise RuntimeError("historical correction does not match original archive")
        corrections = {(r["warehouse"], r["sku"]): r for r in audit["rows"]}
    unavailable = 0
    corrected = 0
    corrected_at = None
    for row in rows:
        if not legacy:
            continue
        correction = corrections.get((row["仓库编码"], row["SKU编码"]))
        if correction:
            row.update({"当日库存": correction["daily_stock"], "实时库存": correction["realtime_stock"], "待出库": correction["pending_out"], "待入库": correction["pending_in"], "库存更正时间": correction["corrected_at"]})
            corrected += 1
            corrected_at = max(corrected_at or "", correction["corrected_at"])
        else:
            # Never present the old raw in-stock value as adjusted daily stock.
            row["当日库存"] = None
            unavailable += 1
    note = "固定当日库存＝实时库存－待出库＋待入库"
    if legacy:
        note = f"已按业务凭证更正 {corrected} 条" if corrected else "旧存档缺少当时待出、待入"
        if unavailable:
            note += f" · {unavailable} 条当日库存暂无法还原（显示 —）"
    return {
        "ok": True, "rows": rows, "row_count": len(rows), "parameters": parameters,
        "queried_at": datetime.now(CHINA).isoformat(timespec="seconds"), "query_ms": 0,
        "stock_date": day, "stock_basis": "daily_snapshot",
        "stock_note": note,
        "sql_sha256": metadata["sql_sha256"], "frozen_at": metadata["frozen_at"],
        "freeze_kind": metadata["freeze_kind"], "archive_sha256": metadata["stock_sha256"],
        "movements_frozen": True,
        "stock_formula": metadata.get("stock_formula"), "unavailable_stock_count": unavailable,
        "corrected_stock_count": corrected,
        "corrected_at": corrected_at,
    }


def read_movements(day: str, warehouse: str, sku: str, start: str, directory: Path | None = None) -> list[dict[str, Any]]:
    metadata = read_metadata(day, directory)
    if start < metadata["movement_start_date"]:
        raise ValueError("requested range predates frozen movement window")
    with _open(day, directory) as db:
        record = db.execute("SELECT payload,sha256 FROM movements WHERE warehouse=? AND sku=?", (warehouse, sku)).fetchone()
    return [row for row in (_unpack(*record) if record else []) if start <= str(row["movement_time"])[:10] <= day]


def _capture_movements(db, day: str, keys: set[tuple[str, str]]) -> tuple[int, str]:
    from scripts import query_inventory_web as stock
    from scripts import query_inventory_movements as movements
    import pymysql.cursors

    template, digest = movements.read_sql()
    # Expand the fixed per-SKU template for a complete archive. No user SQL is
    # accepted, no source filters are removed, and no truncated result is saved.
    for field, marker in (("sku_code", "sku_code"), ("wms_code", "warehouse_code")):
        pattern = r"(\w+\." + field + r")=\[DYNAMIC_PARAMS\." + marker + r"\]"
        template, count = re.subn(pattern, lambda m: f"({m[1]}=[DYNAMIC_PARAMS.{marker}] OR ''=[DYNAMIC_PARAMS.{marker}])", template)
        if count != 5:
            raise RuntimeError("movement archive template contract changed")
    template = template.replace("LIMIT 5001", f"ORDER BY warehouse_code,sku_code LIMIT {MAX_EVENTS + 1}")
    start = (date.fromisoformat(day) - timedelta(days=119)).isoformat()
    sql, bindings = movements.bind_sql(template, warehouse_code="", sku_code="", start_date=start, end_date=day)
    connection = stock._connect()
    count = 0
    saved = 0
    group: list[dict[str, Any]] = []
    previous = None

    def flush():
        nonlocal saved
        if previous in keys and group:
            blob, sha = _pack(group)
            db.execute("INSERT INTO movements VALUES (?,?,?,?)", (*previous, blob, sha))
            saved += len(group)

    try:
        with connection.cursor(pymysql.cursors.SSDictCursor) as cursor:
            cursor.execute(sql, bindings)
            while batch := cursor.fetchmany(1000):
                for row in batch:
                    count += 1
                    if count > MAX_EVENTS:
                        raise RuntimeError("movement archive exceeds complete-result limit")
                    if any(column not in row for column in movements.REQUIRED_COLUMNS):
                        raise RuntimeError("movement archive columns changed")
                    key = (str(row["warehouse_code"]), str(row["sku_code"]))
                    if key != previous:
                        flush()
                        previous, group = key, []
                    if key in keys:
                        group.append({k: stock._json_value(v) for k, v in row.items()})
            flush()
    finally:
        connection.close()
    return saved, digest


@contextmanager
def _date_lock(day: str, directory: Path | None = None):
    target = archive_path(day, directory)
    target.parent.mkdir(parents=True, exist_ok=True)
    # OS-held locks are released on process exit, including crashes. A persistent
    # lock file is harmless and cannot strand tomorrow's task after an outage.
    with target.with_suffix(".lock").open("a+b") as lock:
        acquired = False
        deadline = clock.monotonic() + 120
        try:
            while not acquired:
                lock.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError:
                    if clock.monotonic() >= deadline:
                        raise TimeoutError("another inventory archive capture is still running")
                    clock.sleep(.1)
            # Windows permits locking beyond EOF. Initialize only after owning
            # the region, so a second first-time writer cannot flush into it.
            if lock.seek(0, os.SEEK_END) == 0:
                lock.write(b"0")
                lock.flush()
            yield
        finally:
            if acquired:
                lock.seek(0)
                if os.name == "nt":
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def ensure_archive(day: str, directory: Path | None = None, now: datetime | None = None, *, dashboard: bool = False, stock_parameters: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if archive_path(day, directory).exists():
        return read_metadata(day, directory)
    with _date_lock(day, directory):
        return _capture_archive(day, directory, now, dashboard=dashboard, stock_parameters=stock_parameters)


def _capture_archive(day: str, directory: Path | None = None, now: datetime | None = None, *, dashboard: bool = False, stock_parameters: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Publish a whole date once. Concurrent writers can never replace it."""
    from scripts import query_inventory_web as stock
    final = archive_path(day, directory)
    if final.exists():
        return read_metadata(day, directory)
    now = (now or datetime.now(CHINA)).astimezone(CHINA)
    if day > now.date().isoformat():
        raise ValueError("cannot freeze future inventory")
    if not dashboard and day == now.date().isoformat() and now.time() < FREEZE_TIME:
        return None
    parameters = stock.validate_input(stock_parameters or {"stock_date": day})
    if parameters.get('stock_date') != day:
        raise ValueError('archive date does not match stock parameters')
    # Past adjusted stock cannot be rebuilt from today's pending transfer state.
    if day < now.date().isoformat():
        return None
    result = stock._query_source(parameters)
    if not result["rows"]:
        return None  # A delayed/missing upstream snapshot must be retried.
    final.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{day}-", suffix=".tmp", dir=final.parent)
    os.close(fd)
    try:
        db = sqlite3.connect(temp)
        try:
            db.executescript("CREATE TABLE metadata(payload TEXT NOT NULL); CREATE TABLE stock(payload BLOB NOT NULL,sha256 TEXT NOT NULL); CREATE TABLE movements(warehouse TEXT,sku TEXT,payload BLOB NOT NULL,sha256 TEXT NOT NULL,PRIMARY KEY(warehouse,sku));")
            blob, sha = _pack(result["rows"])
            db.execute("INSERT INTO stock VALUES (?,?)", (blob, sha))
            keys = {(str(row["仓库编码"]), str(row["SKU编码"])) for row in result["rows"]}
            event_count, event_hash = _capture_movements(db, day, keys)
            captured = datetime.now(CHINA)
            metadata = {
                "complete": True, "stock_date": day, "frozen_at": captured.isoformat(timespec="seconds"),
                "freeze_kind": "dashboard" if dashboard else "scheduled" if day == now.date().isoformat() and FREEZE_TIME <= now.time() < time(3, 45) else "backfilled",
                "row_count": len(result["rows"]), "movement_count": event_count,
                "movement_start_date": (date.fromisoformat(day) - timedelta(days=119)).isoformat(),
                "sql_sha256": result["sql_sha256"], "movement_sql_sha256": event_hash, "stock_sha256": sha,
                "schedule": "03:30 Asia/Shanghai",
                "stock_formula": "realtime-minus-pending-out-plus-pending-in",
                "stock_queried_at": result.get('queried_at', captured.isoformat(timespec='seconds')),
            }
            db.execute("INSERT INTO metadata VALUES (?)", (json.dumps(metadata, ensure_ascii=False),))
            db.commit()
        finally:
            db.close()
        with open(temp, "rb+") as source:
            os.fsync(source.fileno())
        try:
            os.link(temp, final)  # Atomic publish with no overwrite on Windows or POSIX.
        except FileExistsError:
            pass
        return read_metadata(day, directory)
    finally:
        Path(temp).unlink(missing_ok=True)


def due_dates(start: date, now: datetime, directory: Path | None = None) -> list[str]:
    now = now.astimezone(CHINA)
    last = now.date() if now.time() >= FREEZE_TIME else now.date() - timedelta(days=1)
    return [(start + timedelta(days=offset)).isoformat() for offset in range(max(0, (last - start).days + 1))
            if not archive_path((start + timedelta(days=offset)).isoformat(), directory).exists()]

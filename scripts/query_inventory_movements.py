"""Read currently visible cloud-warehouse inventory movements for one warehouse/SKU.

The returned balances are reconstructed from the current stock baseline and
request records. They are intentionally labelled as reconstructed, not a
historical stock ledger.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "sql" / "inventory_movements.sql"
MAX_ROWS = 5000
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MARKER_RE = re.compile(r"\[DYNAMIC_PARAMS\.(warehouse_code|sku_code|t1|t2)\]")
REQUIRED_COLUMNS = ("movement_time", "direction", "warehouse_code", "sku_code", "quantity", "reference", "order_status", "source_kind", "source_label")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


class InputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DataError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def read_sql() -> tuple[str, str]:
    raw_bytes = SQL_PATH.read_bytes()
    raw = raw_bytes.decode("utf-8").strip()
    if raw.endswith(";"):
        raw = raw[:-1].rstrip()
    if not re.match(r"^SELECT\b", raw, re.IGNORECASE) or re.search(r";", raw):
        raise RuntimeError("fixed movement SQL must be a single SELECT")
    if re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|SET|CALL)\b", raw, re.IGNORECASE):
        raise RuntimeError("fixed movement SQL is not read-only")
    return raw, hashlib.sha256(raw_bytes).hexdigest()


def parse_date(value: Any, field: str) -> date:
    if not isinstance(value, str) or not DATE_RE.match(value.strip()):
        raise InputError("INVALID_DATE_RANGE", f"{field} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise InputError("INVALID_DATE_RANGE", f"invalid {field}") from exc


def validate_input(payload: Any, today: date | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise InputError("INVALID_JSON", "input must be a JSON object")
    allowed = {"warehouse_code", "sku_code", "start_date", "end_date"}
    if set(payload) - allowed:
        raise InputError("INVALID_PARAMS", "unknown input field")
    warehouse = payload.get("warehouse_code")
    sku = payload.get("sku_code")
    if not isinstance(warehouse, str) or not warehouse.strip() or len(warehouse.strip()) > 100:
        raise InputError("INVALID_PARAMS", "warehouse_code is required")
    if not isinstance(sku, str) or not sku.strip() or len(sku.strip()) > 100:
        raise InputError("INVALID_PARAMS", "sku_code is required")
    today = today or datetime.now(timezone(timedelta(hours=8))).date()
    start_raw, end_raw = payload.get("start_date"), payload.get("end_date")
    if start_raw in (None, "") and end_raw in (None, ""):
        end = today
        start = today - timedelta(days=119)
    else:
        start = parse_date(start_raw, "start_date")
        end = parse_date(end_raw, "end_date")
    if start > end:
        raise InputError("INVALID_DATE_RANGE", "start_date must not be after end_date")
    if end > today:
        raise InputError("INVALID_DATE_RANGE", "end_date must not be after today")
    return {
        "warehouse_code": warehouse.strip(),
        "sku_code": sku.strip(),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }


def bind_sql(template: str, *, warehouse_code: str, sku_code: str, start_date: str, end_date: str) -> tuple[str, list[str]]:
    values = {"warehouse_code": warehouse_code, "sku_code": sku_code, "t1": start_date, "t2": end_date}
    bindings: list[str] = []

    def replace(match: re.Match[str]) -> str:
        bindings.append(values[match.group(1)])
        return "__MOVEMENT_PARAM__"

    rendered = MARKER_RE.sub(replace, template)
    return rendered.replace("%", "%%").replace("__MOVEMENT_PARAM__", "%s"), bindings


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return str(value)


def execute_range(connection: Any, template: str, parameters: dict[str, Any], start_date: str, end_date: str) -> list[dict[str, Any]]:
    query, bindings = bind_sql(template, warehouse_code=parameters["warehouse_code"], sku_code=parameters["sku_code"], start_date=start_date, end_date=end_date)
    with connection.cursor() as cursor:
        cursor.execute(query, bindings)
        return list(cursor.fetchall())


def _number(value: Any, field: str = "quantity") -> Decimal:
    if value is None:
        raise DataError("INVALID_QUANTITY" if field == "quantity" else "INVALID_CURRENT_STOCK", f"{field} is null")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DataError("INVALID_QUANTITY" if field == "quantity" else "INVALID_CURRENT_STOCK", f"{field} is invalid") from exc
    if not number.is_finite():
        raise DataError("INVALID_QUANTITY" if field == "quantity" else "INVALID_CURRENT_STOCK", f"{field} is non-finite")
    return number


def reconstruct_balance(current_stock: Any, rows: list[dict[str, Any]], requested_end: str, later_rows: list[dict[str, Any]] | None = None) -> tuple[Decimal, Decimal, list[dict[str, Any]]]:
    current = _number(current_stock, "current_stock")
    later_signed = sum((_number(row.get("quantity")) * (Decimal("1") if row.get("direction") == "in" else Decimal("-1")) for row in (later_rows or [])), Decimal("0"))
    window_signed = sum((_number(row.get("quantity")) * (Decimal("1") if row.get("direction") == "in" else Decimal("-1")) for row in rows), Decimal("0"))
    closing = current - later_signed
    opening = closing - window_signed
    running = opening
    output: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: (str(item.get("movement_time") or ""), str(item.get("reference") or ""), str(item.get("direction") or ""))):
        quantity = _number(row.get("quantity"))
        signed = quantity if row.get("direction") == "in" else -quantity
        before = running
        running += signed
        output.append({
            "time": _json_value(row.get("movement_time")),
            "direction": row.get("direction"),
            "quantity": float(quantity),
            "signed_quantity": float(signed),
            "reference": row.get("reference"),
            "order_status": row.get("order_status"),
            "source_kind": row.get("source_kind"),
            "source_label": row.get("source_label"),
            "before_stock": float(before),
            "after_stock": float(running),
        })
    return opening, closing, output


def validate_events(rows: list[dict[str, Any]]) -> None:
    seen: set[tuple[str, str]] = set()
    for row in rows:
        event_key = (str(row.get("direction") or ""), str(row.get("reference") or ""))
        if event_key in seen:
            raise DataError("DUPLICATE_EVENT", "duplicate movement event")
        seen.add(event_key)


def cached_stock_matches(stock: Any, parameters: dict[str, Any]) -> bool:
    if not isinstance(stock, dict) or stock.get("ok") is not True or stock.get("stock_basis") not in ("live_adjusted", "dashboard_snapshot") or stock.get("stock_date") != parameters["end_date"]:
        return False
    rows = stock.get("rows")
    return isinstance(rows, list) and len(rows) == 1 and rows[0].get("仓库编码") == parameters["warehouse_code"] and rows[0].get("SKU编码") == parameters["sku_code"] and rows[0].get("当日库存") is not None


def run(payload: Any, stock_result: dict[str, Any] | None = None) -> dict[str, Any]:
    parameters = validate_input(payload)
    template, sql_hash = read_sql()
    import importlib.util
    spec = importlib.util.spec_from_file_location("query_inventory_web", ROOT / "scripts" / "query_inventory_web.py")
    assert spec and spec.loader
    stock_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stock_module)
    stock_module._load_env_file(stock_module.AGENT_ENV_PATH)
    stock_params = stock_module.validate_input({"sku_codes": [parameters["sku_code"]], "warehouse_codes": [parameters["warehouse_code"]], "stock_date": parameters["end_date"]})
    if not cached_stock_matches(stock_result, parameters):
        stock_result = stock_module.run_query(stock_params)
    stock_rows = stock_result.get("rows", [])
    if len(stock_rows) > 1:
        raise RuntimeError("current stock returned duplicate warehouse and SKU rows")
    if not stock_rows:
        raise DataError("STOCK_NOT_FOUND", "current stock was not found")
    stock_row = stock_rows[0]
    if stock_row.get("当日库存") is None:
        raise DataError("HISTORICAL_STOCK_UNAVAILABLE", "historical adjusted daily stock was not captured")

    if stock_result.get("movements_frozen"):
        from scripts import inventory_history
        directory = Path(stock_result['_archive_directory']) if stock_result.get('stock_basis') == 'dashboard_snapshot' else None
        window_rows = inventory_history.read_movements(parameters["end_date"], parameters["warehouse_code"], parameters["sku_code"], parameters["start_date"], directory)
        sql_hash = inventory_history.read_metadata(parameters["end_date"], directory)["movement_sql_sha256"]
    else:
        connection = stock_module._connect()
        try:
            window_rows = execute_range(connection, template, parameters, parameters["start_date"], parameters["end_date"])
        finally:
            connection.close()
    if len(window_rows) > MAX_ROWS:
        raise DataError("TOO_MANY_ROWS", "movement result exceeds row limit")
    for row in window_rows:
        missing = [column for column in REQUIRED_COLUMNS if column not in row]
        if missing:
            raise RuntimeError("movement result columns do not match contract")
    validate_events(window_rows)
    opening, closing, movements = reconstruct_balance(stock_row.get("当日库存"), window_rows, parameters["end_date"])
    total_in = sum((_number(row.get("quantity")) for row in window_rows if row.get("direction") == "in"), Decimal("0"))
    total_out = sum((_number(row.get("quantity")) for row in window_rows if row.get("direction") == "out"), Decimal("0"))
    return {
        "ok": True,
        "rows": movements,
        "current_stock": stock_row.get("当日库存"),
        "realtime_stock": stock_row.get("实时库存"),
        "stock_changed_at": stock_row.get("库存变动时间"),
        "stock_queried_at": stock_result.get("queried_at"),
        "opening_stock": float(opening),
        "closing_stock": float(closing),
        "later_signed_quantity": 0,
        "start_date": parameters["start_date"],
        "end_date": parameters["end_date"],
        "total_in": float(total_in),
        "total_out": float(total_out),
        "queried_at": stock_result.get('queried_at') if stock_result.get('stock_basis') == 'dashboard_snapshot' else datetime.now().astimezone().isoformat(timespec="seconds"),
        "parameters": parameters,
        "row_count": len(movements),
        "source_kind": "all_visible_sources",
        "balance_kind": "reconstructed",
        "stock_date": stock_result["stock_date"],
        "stock_basis": stock_result["stock_basis"],
        "frozen_at": stock_result.get("frozen_at"),
        "freeze_kind": stock_result.get("freeze_kind"),
        "corrected_at": stock_result.get("corrected_at"),
        "note": ("按同批看板库存与流水快照倒推结存；只读取本地快照，非原始结存日志。" if stock_result['stock_basis'] == 'dashboard_snapshot' else "以固定当日库存（实时库存－待出库＋待入库）为基准倒推；历史出入库记录保持存档，不代表逐笔历史日志。" if stock_result["stock_basis"] == "daily_snapshot" else "结存按当前库存及当前可见业务记录倒推，非原始结存日志。") + "调拨和销售出库优先使用计划出库时间，其余记录使用业务时间。",
        "sql_sha256": sql_hash,
    }


def error_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, InputError):
        return {"ok": False, "code": exc.code, "error": str(exc)}
    if isinstance(exc, json.JSONDecodeError):
        return {"ok": False, "code": "INVALID_JSON", "error": "input is not valid JSON"}
    if isinstance(exc, DataError):
        return {"ok": False, "code": exc.code, "error": str(exc)}
    return {"ok": False, "code": "DB_QUERY_FAILED", "error": "inventory movement query failed"}


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        # This envelope is created by the local Node server, never accepted as
        # HTTP query parameters. Direct CLI callers retain the original shape.
        if isinstance(payload, dict) and set(payload) == {"parameters", "stock_result"}:
            result = run(payload["parameters"], payload["stock_result"])
        else:
            result = run(payload)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(json.dumps(error_payload(exc), ensure_ascii=False, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

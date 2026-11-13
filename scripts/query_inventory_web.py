"""Read-only inventory query adapter for the web page.

The SQL text is intentionally kept in ``sql/inventory_web_user.sql``.  This
module only validates JSON input and expands the documented dynamic markers
into PyMySQL positional bindings; callers cannot provide SQL text.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SQL_PATH = ROOT / "sql" / "inventory_web_user.sql"
SNAPSHOT_SQL_PATH = ROOT / "sql" / "inventory_web_snapshot.sql"
AGENT_ENV_PATH = Path(r"D:\取数agent\.env")
MAX_FILTER_ITEMS = 100
MAX_ITEM_LENGTH = 100
MARKER_RE = re.compile(r"\[DYNAMIC_PARAMS\.(SKU编码|仓库编码|t1|t2|stock_date)\]")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REQUIRED_COLUMNS = (
    "仓库编码", "仓库名称", "SKU编码", "SKU名称", "当日库存", "期间出库数量",
    "期间退货数量", "净数量", "统计天数", "每日消耗", "预计天数", "实时库存",
    "库存变动时间", "待出库", "待入库",
)

# Node's child-process reader expects UTF-8 even when the parent Windows
# console uses a legacy code page.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


class QueryInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _load_env_file(path: Path) -> None:
    """Load managed values without overriding values supplied by the server."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key.startswith("DORIS_") and key not in os.environ:
            os.environ[key] = value


def _env(name: str, fallback: str = "") -> str:
    # DORIS_* is the managed contract; PICKUP_DB_* remains supported for the
    # existing server runner and takes precedence when explicitly provided.
    aliases = {
        "HOST": ("PICKUP_DB_HOST", "DORIS_HOST"),
        "PORT": ("PICKUP_DB_PORT", "DORIS_PORT"),
        "USER": ("PICKUP_DB_USER", "DORIS_USER"),
        "PASSWORD": ("PICKUP_DB_PASSWORD", "DORIS_PASSWORD"),
        "DATABASE": ("PICKUP_DB_NAME", "DORIS_DATABASE", "DORIS_DB"),
    }
    for key in aliases[name]:
        value = os.environ.get(key)
        if value is not None and value != "":
            return value.strip() if key != "PICKUP_DB_PASSWORD" else value
    return fallback


def china_today() -> date:
    return datetime.now(timezone(timedelta(hours=8))).date()


def _read_template(path: Path | None = None) -> tuple[str, str]:
    raw_bytes = (path or SQL_PATH).read_bytes()
    raw = raw_bytes.decode("utf-8")
    sql = raw.strip()
    if sql.endswith(";"):
        sql = sql[:-1].rstrip()
    if not re.match(r"^SELECT\b", sql, re.IGNORECASE) or ";" in sql:
        raise RuntimeError("fixed SQL must contain one SELECT statement")
    if re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|SET|CALL)\b", sql, re.IGNORECASE):
        raise RuntimeError("fixed SQL contains a write or session statement")
    if not MARKER_RE.search(sql):
        raise RuntimeError("fixed SQL has no supported dynamic markers")
    return sql, hashlib.sha256(raw_bytes).hexdigest()


def _list_param(value: Any, field: str) -> list[str]:
    if value is None:
        values: list[Any] = []
    elif isinstance(value, list):
        values = value
    else:
        raise QueryInputError("INVALID_PARAMS", f"{field} must be an array")
    if len(values) > MAX_FILTER_ITEMS:
        raise QueryInputError("INVALID_PARAMS", f"{field} has too many values")
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, str):
            raise QueryInputError("INVALID_PARAMS", f"{field} values must be strings")
        item = item.strip()
        if not item or len(item) > MAX_ITEM_LENGTH or "\x00" in item:
            raise QueryInputError("INVALID_PARAMS", f"invalid {field} value")
        if item in seen:
            raise QueryInputError("DUPLICATE_FILTER", f"duplicate {field} value")
        seen.add(item)
        result.append(item)
    # An empty IN list would be invalid SQL.  The original template uses an
    # empty-string sentinel to make its OR condition true for an unfiltered run.
    return result or [""]


def _date_param(value: Any, field: str) -> str:
    if value is None:
        raise QueryInputError("INVALID_DATE_RANGE", f"{field} must be YYYY-MM-DD or empty")
    if not isinstance(value, str):
        raise QueryInputError("INVALID_DATE_RANGE", f"{field} must be YYYY-MM-DD")
    value = value.strip()
    if value and (not DATE_RE.match(value)):
        raise QueryInputError("INVALID_DATE_RANGE", f"{field} must be YYYY-MM-DD")
    if value:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise QueryInputError("INVALID_DATE_RANGE", f"invalid {field}") from exc
    return value


def validate_input(payload: Any, today: date | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise QueryInputError("INVALID_JSON", "input must be a JSON object")
    allowed = {"sku_codes", "warehouse_codes", "start_date", "end_date", "stock_date"}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise QueryInputError("INVALID_PARAMS", "unknown input field")
    sku_codes = _list_param(payload.get("sku_codes", []), "sku_codes")
    warehouse_codes = _list_param(payload.get("warehouse_codes", []), "warehouse_codes")
    start_date = _date_param(payload.get("start_date", ""), "start_date")
    end_date = _date_param(payload.get("end_date", ""), "end_date")
    if bool(start_date) != bool(end_date):
        raise QueryInputError("INVALID_DATE_RANGE", "start_date and end_date must both be set or empty")
    if start_date and start_date > end_date:
        raise QueryInputError("INVALID_DATE_RANGE", "start_date must not be after end_date")
    result = {
        "sku_codes": sku_codes,
        "warehouse_codes": warehouse_codes,
        "start_date": start_date,
        "end_date": end_date,
    }
    if "stock_date" in payload:
        stock_date = _date_param(payload["stock_date"], "stock_date")
        if not stock_date or stock_date > (today or china_today()).isoformat():
            raise QueryInputError("INVALID_DATE_RANGE", "stock_date must not be empty or after today")
        result["stock_date"] = stock_date
    return result


def build_bound_query(parameters: dict[str, Any], template: str | None = None) -> tuple[str, list[str]]:
    sql = template if template is not None else _read_template()[0]
    bound: list[str] = []

    def replace(match: re.Match[str]) -> str:
        marker = match.group(1)
        if marker == "SKU编码":
            values = parameters["sku_codes"]
        elif marker == "仓库编码":
            values = parameters["warehouse_codes"]
        elif marker == "t1":
            values = [parameters["start_date"]]
        elif marker == "stock_date":
            values = [parameters["stock_date"]]
        else:
            values = [parameters["end_date"]]
        bound.extend(values)
        # Keep a private sentinel until all literal percent signs in the
        # original SQL have been escaped (DATE_FORMAT contains ``%s`` too).
        return ", ".join("__INVENTORY_PARAM__" for _ in values)

    rendered = MARKER_RE.sub(replace, sql)
    # PyMySQL's execute() applies Python's %-formatting.  Escape DATE_FORMAT's
    # literal percent signs while retaining the newly generated %s markers.
    rendered = rendered.replace("%", "%%").replace("__INVENTORY_PARAM__", "%s")
    return rendered, bound


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, (str, int, bool)):
        return value
    return str(value)


def _connect():
    try:
        import pymysql
    except ImportError as exc:
        raise RuntimeError("PyMySQL dependency is unavailable") from exc
    host = _env("HOST")
    user = _env("USER")
    password = _env("PASSWORD")
    database = _env("DATABASE", "ywzt_ods") or "ywzt_ods"
    port_raw = _env("PORT", "9030")
    if not host or not user or not password:
        raise RuntimeError("database configuration is incomplete")
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise RuntimeError("database port is invalid") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("database port is invalid")
    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=20,
        read_timeout=120,
        write_timeout=60,
        autocommit=True,
    )


def _query_source(parameters: dict[str, Any], historical: bool = False) -> dict[str, Any]:
    stock_date = parameters.get("stock_date") or china_today().isoformat()
    template, sql_hash = _read_template(SNAPSHOT_SQL_PATH if historical else SQL_PATH)
    query, bindings = build_bound_query(parameters, template)
    started = time.perf_counter()
    connection = _connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, bindings)
            raw_rows = list(cursor.fetchall())
    finally:
        connection.close()
    rows = [{str(key): _json_value(value) for key, value in row.items()} for row in raw_rows]
    if rows:
        missing = [column for column in REQUIRED_COLUMNS if column not in rows[0]]
        if missing:
            raise RuntimeError("query result columns do not match the fixed contract")
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (str(row.get("仓库编码") or ""), str(row.get("SKU编码") or ""))
        if key in seen:
            raise RuntimeError("query returned duplicate warehouse and SKU rows")
        seen.add(key)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "ok": True,
        "rows": rows,
        "queried_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "parameters": parameters,
        "row_count": len(rows),
        "query_ms": elapsed_ms,
        "sql_sha256": sql_hash,
        "stock_date": stock_date,
        "stock_basis": "daily_snapshot" if historical else "live_adjusted",
        "stock_note": "在库日快照 · 不含待出、待入" if historical else "实时在库－待出＋待入",
    }


def run_query(parameters: dict[str, Any]) -> dict[str, Any]:
    stock_date = parameters.get("stock_date") or china_today().isoformat()
    if stock_date < china_today().isoformat():
        from scripts import inventory_history
        if inventory_history.ensure_archive(stock_date) is not None:
            return inventory_history.read_stock(stock_date, parameters)
        return {
            "ok": True, "rows": [], "row_count": 0, "parameters": parameters,
            "queried_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "query_ms": 0, "stock_date": stock_date, "stock_basis": "daily_snapshot",
            "stock_note": "所选日期未保存当日库存，无法用今天的待出、待入还原", "frozen_at": None,
        }
    return _query_source(parameters)


def _error_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, json.JSONDecodeError):
        return {"ok": False, "code": "INVALID_JSON", "error": "input is not valid JSON"}
    if isinstance(exc, QueryInputError):
        return {"ok": False, "code": exc.code, "error": str(exc)}
    if isinstance(exc, RuntimeError) and str(exc) in {"PyMySQL dependency is unavailable", "database configuration is incomplete", "database port is invalid"}:
        return {"ok": False, "code": "CONFIG_ERROR", "error": str(exc)}
    return {"ok": False, "code": "DB_QUERY_FAILED", "error": "inventory query failed"}


def main() -> int:
    try:
        _load_env_file(AGENT_ENV_PATH)
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        parameters = validate_input(payload)
        result = run_query(parameters)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except Exception as exc:  # stdout remains a stable machine-readable contract
        print(json.dumps(_error_payload(exc), ensure_ascii=False, separators=(",", ":")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

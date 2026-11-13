"""Daily 03:30 China-time archive; delayed runs fill gaps without rewriting dates."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import inventory_history as history
from scripts import query_inventory_web as stock


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Explicit historical date to import once")
    parser.add_argument("--due", action="store_true", help="Capture every missing date since scheduling began")
    args = parser.parse_args()
    stock._load_env_file(stock.AGENT_ENV_PATH)
    now = datetime.now(history.CHINA)
    history.DEFAULT_DIR.mkdir(parents=True, exist_ok=True)
    state = history.DEFAULT_DIR / "schedule-start.json"
    try:
        with state.open("x", encoding="utf-8") as output:
            json.dump({"start_date": now.date().isoformat(), "schedule": "03:30 Asia/Shanghai"}, output)
    except FileExistsError:
        pass
    start = date.fromisoformat(json.loads(state.read_text(encoding="utf-8"))["start_date"])
    days = [args.date] if args.date else history.due_dates(start, now)
    pending = []
    unavailable_history = []
    for day in days:
        record = history.ensure_archive(day)
        if record is None:
            (unavailable_history if day < now.date().isoformat() else pending).append(day)
        else:
            print(json.dumps({"ok": True, **record}, ensure_ascii=False), flush=True)
    print(json.dumps({"ok": not pending, "checked_dates": days, "pending_dates": pending, "unavailable_historical_dates": unavailable_history, "schedule": "03:30 Asia/Shanghai"}), flush=True)
    return 1 if pending else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Do not leak connection settings or upstream SQL errors into logs.
        print(json.dumps({"ok": False, "code": "ARCHIVE_FAILED", "exception_type": type(exc).__name__}), flush=True)
        raise SystemExit(1)

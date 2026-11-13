"""Refresh cloud stock/alerts from a saved sales batch; never query PostgreSQL."""
from __future__ import annotations

import json
import sys
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.daily_sales_alerts import enrich_alerts
from scripts.query_daily_sales_web import CHINA, encode


def run(snapshot):
    if not isinstance(snapshot, dict) or not snapshot.get('ok') or not isinstance(snapshot.get('rows'), list):
        raise ValueError('INVALID_SNAPSHOT')
    day = date.fromisoformat(snapshot['date'])
    if day > datetime.now(CHINA).date() or len(snapshot['rows']) > 20000:
        raise ValueError('INVALID_SNAPSHOT')
    # Preserve every sales field, source date, mapping ratio and reference window.
    result = deepcopy(snapshot)
    result['alerts'] = enrich_alerts(result['rows'], day)
    result['queried_at'] = datetime.now(CHINA).isoformat(timespec='seconds')
    return result


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    try:
        result = run(json.loads(sys.stdin.read(), parse_float=Decimal))
        print(json.dumps(result, ensure_ascii=False, default=encode, separators=(',', ':')))
    except Exception:
        print(json.dumps({'ok': False, 'code': 'STOCK_REFRESH_FAILED',
                          'error': '云仓库存更新失败，继续保留上次成功数据。'}, ensure_ascii=False))
        raise SystemExit(1)

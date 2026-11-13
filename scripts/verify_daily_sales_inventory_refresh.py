"""A stock refresh must retain the frozen PG sales batch and never open PG."""
import sys
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.refresh_daily_sales_inventory import run

source = {'ok': True, 'date': '2026-11-11', 'queried_at': '2026-11-11T08:40:00+08:00',
          'sales_queried_at': '2026-11-11T08:40:00+08:00', 'unmapped': [],
          'start_date': '2026-11-04', 'end_date': '2026-11-10',
          'rows': [{'warehouse_name': '佛山 / 顺德', 'sku_code': 'S',
                    'estimated_sales': Decimal('100.1'), 'reference_sales': Decimal('30.2'),
                    'valid_days': 7, 'total_sales': Decimal('211.4'), 'reference_kind': 'average',
                    'source_warehouses': '佛山1仓、顺德1仓', 'cloud_stock': 80, 'alert_state': 'warning'}]}
original = deepcopy(source)


def stock_only(rows, day):
    assert day.isoformat() == source['date']
    rows[0].update(cloud_stock=150, alert_state='tight')
    return {'transit_status': 'available', 'transit_queried_at': '2026-11-11T13:00:00+08:00'}


with patch('scripts.refresh_daily_sales_inventory.enrich_alerts', side_effect=stock_only), \
     patch('psycopg2.connect', side_effect=AssertionError('STOCK_REFRESH_MUST_NOT_OPEN_PG')) as pg:
    result = run(source)
    pg.assert_not_called()
assert source == original
assert result['sales_queried_at'] == original['sales_queried_at']
assert result['start_date'] == original['start_date'] and result['end_date'] == original['end_date']
for key, value in original['rows'][0].items():
    if key not in {'cloud_stock', 'alert_state'}:
        assert result['rows'][0][key] == value, key
assert result['rows'][0]['cloud_stock'] == 150 and result['rows'][0]['alert_state'] == 'tight'
print('Stock-only refresh passed: no PG connection; all sales fields, dates and source batch unchanged.')

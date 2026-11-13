"""Three alert states, strict boundaries and rolling current-stock use."""
import sys
from pathlib import Path
from datetime import date
from decimal import Decimal as D
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.daily_sales_alerts import assess, enrich_alerts
from scripts.daily_sales_cities import warehouse_city, cloud_city, policy

assert assess(100, 100, 100 / 3)[0] == 'normal'
assert assess(101, 100, 40)[0] == 'warning'
assert assess(121, 500, 40) == ('tight', ['预估超过参考销量3倍，仍低于库存（含在途）'])
assert assess(121, 100, 40) == ('warning', ['预估超过库存（含在途）'])
assert assess(1, None, 0)[0] == 'unknown'
assert assess(120, 500, 40)[0] == 'normal'  # Equal to 3x is not a warning.
assert assess(121, 121, 40)[0] == 'normal'  # Yellow requires strictly below stock.
assert assess(119, 100, 40)[0] == 'warning'  # Red wins even below the sales threshold.
assert assess(101, 100, None)[0] == 'warning'  # Known shortage does not need history.
assert assess(99, 100, None)[0] == 'unknown'
assert assess(None, None, None)[0] == 'unknown'
assert assess(D('120.01'), D('500'), D('40'))[0] == 'tight'
assert assess(D('100.01'), D('100'), D('40'))[0] == 'warning'
assert assess(0, 0, 0)[0] == 'normal'
assert assess(0, None, 0)[0] == 'unknown'
assert assess(None, 100, 40)[0] == 'unknown'

current_rows = [{'wms_code': 'WMS000115', 'sku_code': 'S', 'rolling_stock': D(400),
                 'realtime_stock': D(536), 'pending_out': D(150), 'pending_in': D(14),
                 'stock_changed_at': '2026-11-11 13:00:00'}]


class Cursor:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, sql, *args): self.sql = sql
    def fetchall(self):
        if 'WITH po' in self.sql:
            return [{'wms_code': 'WMS000115', 'sku_code': 'S', 'purchase_transit': D(20), 'transfer_transit': D(10)}]
        if 'WITH stock' in self.sql:
            return current_rows
        return [{'wms_code': 'WMS000115', 'wms_desc': '多多买菜-揭阳云仓'}, {'wms_code': 'C2', 'wms_desc': '多多买菜-新揭阳云仓'}, {'wms_code': 'M', 'wms_desc': '多多买菜-揭阳云仓（美团）'}]

class Connection:
    def cursor(self): return Cursor()
    def close(self): pass

rows = [{'warehouse_name': '揭阳', 'sku_code': 'S', 'estimated_sales': D(431), 'reference_sales': D(200)}]
with patch('scripts.inventory_history.read_stock', side_effect=AssertionError('NO_ARCHIVE_READ')) as read_stock, patch('scripts.query_inventory_web._connect', return_value=Connection()):
    metadata = enrich_alerts(rows, date(2026, 11, 11))
    read_stock.assert_not_called()
assert rows[0]['cloud_stock'] == 430  # Current 536 - 150 + 14 + 20 + 10; no archived 317.
assert rows[0]['rolling_stock'] == 400 and rows[0]['fixed_stock'] is None
assert rows[0]['alert_state'] == 'warning'
assert metadata['snapshot_date'] is None and metadata['frozen_at'] is None
assert metadata['stock_basis'] == 'rolling-adjusted-with-transit-v1'
assert metadata['stock_status'] == 'available'
assert rows[0]['cloud_warehouse_codes'] == ['WMS000115']
assert 'M' not in rows[0]['cloud_warehouse_codes']
assert warehouse_city('揭阳中心1仓') == warehouse_city('揭阳中心2仓') == '揭阳'
assert warehouse_city('武汉3仓') == '武汉'
assert warehouse_city('武汉4仓') == '武汉4仓（未配置）'
assert warehouse_city('上海浦东物流1仓') == '上海'
assert warehouse_city('成都青白江2仓') == '成都'
assert cloud_city('多多买菜-贵州云仓', {'贵阳'}) == '贵阳'
assert cloud_city('多多买菜-新揭阳云仓', {'揭阳'}) is None
assert warehouse_city('顺德1仓') == warehouse_city('佛山1仓') == '佛山 / 顺德'
assert warehouse_city('苏州1仓') == warehouse_city('无锡1仓') == '无锡 / 苏州'
# A similarly named, non-approved old/shared cloud must never inflate stock.
current_rows.append({'wms_code': 'C2', 'sku_code': 'S', 'rolling_stock': None})
with patch('scripts.query_inventory_web._connect', return_value=Connection()):
    enrich_alerts(rows, date(2026,11,11))
assert rows[0]['rolling_stock'] == 400 and rows[0]['cloud_stock'] == 430
assert rows[0]['alert_state'] == 'warning'
current_rows[0]['rolling_stock'] = D(500)
with patch('scripts.query_inventory_web._connect', return_value=Connection()):
    enrich_alerts(rows, date(2026,11,11))
assert rows[0]['rolling_stock'] == 500 and rows[0]['cloud_stock'] == 530
assert rows[0]['alert_state'] == 'normal'  # A new current-stock batch changes the warning.
current_rows[0]['rolling_stock'] = None
with patch('scripts.query_inventory_web._connect', return_value=Connection()):
    enrich_alerts(rows, date(2026,11,11))
assert rows[0]['rolling_stock'] == 0 and rows[0]['cloud_stock'] == 30
assert rows[0]['alert_state'] == 'warning' and not rows[0]['alert_missing']
current_rows[0]['rolling_stock'] = D(0)
with patch('scripts.query_inventory_web._connect', return_value=Connection()):
    enrich_alerts(rows, date(2026,11,11))
assert rows[0]['rolling_stock'] == 0 and rows[0]['cloud_stock'] == 30
current_rows[0]['rolling_stock'] = D(-5)
with patch('scripts.query_inventory_web._connect', return_value=Connection()):
    enrich_alerts(rows, date(2026,11,11))
assert rows[0]['rolling_stock'] == -5 and rows[0]['cloud_stock'] == 25
current_rows[0]['sku_code'] = 'OTHER'
with patch('scripts.query_inventory_web._connect', return_value=Connection()):
    enrich_alerts(rows, date(2026,11,11))
assert rows[0]['rolling_stock'] == 0 and rows[0]['cloud_stock'] == 30
assert rows[0]['alert_state'] == 'warning' and not rows[0]['alert_missing']
unmapped_rows = [{'warehouse_name': '未配置仓', 'sku_code': 'S', 'estimated_sales': D(431), 'reference_sales': D(200)}]
with patch('scripts.query_inventory_web._connect', return_value=Connection()):
    enrich_alerts(unmapped_rows, date(2026,11,11))
assert unmapped_rows[0]['rolling_stock'] is None and unmapped_rows[0]['alert_state'] == 'unknown'
current_rows.clear()
with patch('scripts.query_inventory_web._connect', return_value=Connection()):
    metadata = enrich_alerts(rows, date(2026,11,11))
assert metadata['stock_status'] == 'failed'  # Empty source is unavailable, never business zero.
assert rows[0]['rolling_stock'] is None and rows[0]['cloud_stock'] is None
assert len(policy()['entries']) == 84
print('Alert checks passed: rolling adjusted stock, no archive reads, refreshed warning, explicit clouds, empty/missing source and all boundaries.')

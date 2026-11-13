"""Compare actual before/after snapshots without querying either source database."""
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
folder = ROOT / 'output/daily-sales'
before = json.loads((folder / (sys.argv[1] if len(sys.argv) > 1 else 'split-refresh-before.json')).read_text(encoding='utf-8-sig'), parse_float=Decimal)
after = json.loads((folder / (sys.argv[2] if len(sys.argv) > 2 else 'split-refresh-after.json')).read_text(encoding='utf-8-sig'), parse_float=Decimal)
stock_fields = {'cloud_warehouse_code', 'cloud_warehouse_codes', 'cloud_warehouse_name', 'cloud_warehouse_names',
                'fixed_stock', 'purchase_transit', 'transfer_transit', 'transit_stock', 'cloud_stock',
                'alert_state', 'alert_reasons', 'alert_missing', 'rolling_stock', 'realtime_stock',
                'pending_out', 'pending_in', 'stock_changed_at'}
assert before['ok'] and after['ok'] and not after['snapshot']['last_error']
assert after['refresh_mode'] == 'stock-only'
assert after['sales_queried_at'] == before.get('sales_queried_at', before['queried_at'])
assert after['inventory_queried_at'] > before.get('inventory_queried_at', before['queried_at'])
assert len(before['rows']) == len(after['rows'])
for old, new in zip(before['rows'], after['rows']):
    assert {k: v for k, v in old.items() if k not in stock_fields} == {k: v for k, v in new.items() if k not in stock_fields}
    if after.get('alerts', {}).get('stock_basis') == 'rolling-adjusted-with-transit-v1':
        assert new['fixed_stock'] is None
        expected = new['rolling_stock'] + new['transit_stock'] if new['rolling_stock'] is not None and new['transit_stock'] is not None else None
        assert new['cloud_stock'] == expected
        from scripts.daily_sales_alerts import assess
        assert new['alert_state'] == assess(new['estimated_sales'], new['cloud_stock'], new['reference_sales'])[0]
    else:
        assert old['fixed_stock'] == new['fixed_stock']
for field in ('date', 'start_date', 'end_date', 'capture_date', 'unmapped', 'warehouse_groups', 'warehouse_mapping_source'):
    assert before.get(field) == after.get(field), field
report = {'ok': True, 'rows_verified': len(after['rows']), 'refresh_mode': after['refresh_mode'],
          'sales_batch_unchanged': True, 'sales_updated_at': after['sales_queried_at'],
          'inventory_updated_at': after['inventory_queried_at'], 'stock_basis': after.get('alerts', {}).get('stock_basis', 'fixed'),
          'source_dates_mappings_and_sales_fields_unchanged': True,
          'refresh_policy': after['refresh_policy'], 'snapshot_id': after['snapshot']['id']}
(folder / (sys.argv[3] if len(sys.argv) > 3 else 'split-refresh-verification.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False))

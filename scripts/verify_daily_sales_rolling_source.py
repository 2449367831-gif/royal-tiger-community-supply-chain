"""Read-only parity check against the user's full current-inventory SQL."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import query_inventory_web as inventory
from scripts.daily_sales_cities import policy
from scripts.query_daily_sales_web import encode

codes = sorted({row['cloud_code'] for row in policy()['entries']})
parameters = inventory.validate_input({'warehouse_codes': codes, 'sku_codes': []})
query, bindings = inventory.build_bound_query(parameters)
inventory._load_env_file(inventory.AGENT_ENV_PATH)
connection = inventory._connect()
connection._read_timeout = 45
try:
    with connection.cursor() as cursor:
        cursor.execute(query, bindings)
        original = {(r['仓库编码'], r['SKU编码']): r for r in cursor.fetchall()}
        cursor.execute((ROOT / 'sql/daily_sales_stock.sql').read_text(encoding='utf-8'), {'warehouse_codes': tuple(codes)})
        rolling = {(r['wms_code'], r['sku_code']): r for r in cursor.fetchall()}
    assert original and set(original) == set(rolling)
    mismatches = []
    for key, old in original.items():
        for supplied, current in [('当日库存', 'rolling_stock'), ('实时库存', 'realtime_stock'), ('待出库', 'pending_out'), ('待入库', 'pending_in')]:
            if old[supplied] != rolling[key][current]:
                mismatches.append({'warehouse': key[0], 'sku': key[1], 'field': supplied, 'original': old[supplied], 'current': rolling[key][current]})
    report = {'ok': not mismatches, 'clouds': len(codes), 'warehouse_sku_rows': len(original),
              'fields_compared': ['当日库存', '实时库存', '待出库', '待入库'], 'mismatches': mismatches,
              'inventory_sql': 'sql/inventory_web_user.sql', 'rolling_projection': 'sql/daily_sales_stock.sql',
              'transit_sql_unchanged': 'sql/daily_sales_transit.sql'}
    (ROOT / 'output/daily-sales/rolling-stock-source-crosscheck.json').write_text(json.dumps(report, ensure_ascii=False, default=encode, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'mismatches'} | {'mismatch_count': len(mismatches)}, ensure_ascii=False))
    assert not mismatches, 'Current stock differs from user SQL; inspect saved comparison.'
finally:
    connection.close()

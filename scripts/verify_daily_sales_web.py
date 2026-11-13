"""Read-only acceptance: execute the actual SQL on SELECT fixtures and live rows."""
import json
import time
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.query_daily_sales_web import required, sales_sql, mapping_sql
from scripts.daily_sales_cities import warehouse_city
import psycopg2

connection = psycopg2.connect(host=required('PGHOST'), port=int(required('PGPORT')),
    dbname=required('PGDATABASE'), user=required('PGUSER'), password=required('PGPASSWORD'),
    connect_timeout=10, options='-c statement_timeout=30000')
connection.set_session(readonly=True, isolation_level='REPEATABLE READ')
try:
    with connection.cursor() as cursor:
        cursor.execute('SHOW transaction_read_only')
        assert cursor.fetchone()[0] == 'on'
        template = sales_sql()
        fixture = '''WITH fixture_mapping_raw("platform_skuId",sku_code,sku_name,ratio) AS (VALUES
            ('A','S','Internal S',3),('B','S','Internal S',2),('C','T','Internal T',1),
            ('Z','Z','Zero',1),('N','N','No history',1),('BAD','BAD','Bad ratio',0),
            ('CONFLICT','S','Conflict',1),('CONFLICT','','Invalid latest',1)),
        fixture_mapping AS (SELECT *, NULL::timestamp AS updated_date, ROW_NUMBER() OVER () AS id FROM fixture_mapping_raw),
        fixture_today(account_name,warehouse_name,warehouse_id,product_id,product_name,sell_unit_name,estimated_total_sales,estimated_updated_at,captured_date,id,sale_date) AS (
            SELECT 'account','W','wid',p,'Product','bag',qty::numeric,'2026-11-10 18:00:00+08'::timestamptz,date '2026-11-10',id,date '2026-11-10'
            FROM (VALUES ('A',10,1),('A',10,2),('B',5,3),('C',4,4),('N',6,5),('UNKNOWN',99,6),('BAD',99,7),('CONFLICT',99,8)) v(p,qty,id)
            UNION ALL SELECT 'account','W','wid','A','Product','bag',999::numeric,
                '2026-11-10 19:00:00+08'::timestamptz,date '2026-11-09',99,date '2026-11-10'
            UNION ALL SELECT 'account','W2','wid2','A','Product','bag',4::numeric,
                '2026-11-10 18:00:00+08'::timestamptz,date '2026-11-10',100,date '2026-11-10'),
        fixture_sales(warehouse_name,product_id,product_name,sell_unit_name,sale_date,sell_unit_total,error_message) AS (
            SELECT 'W','A','Product','bag',date '2026-11-10'-n,2::numeric,'' FROM generate_series(1,7) n
            UNION ALL SELECT 'W','B','Product','bag',date '2026-11-10'-n,1::numeric,'' FROM generate_series(1,7) n
            UNION ALL SELECT 'W','C','Product','bag',date '2026-11-09',2::numeric,''
            UNION ALL SELECT 'W','C','Product','bag',date '2026-11-08',9::numeric,''
            UNION ALL SELECT 'W','C','Product','bag',date '2026-11-10',999::numeric,''
            UNION ALL SELECT 'W','C','Product','bag',date '2026-11-01',999::numeric,''
            UNION ALL SELECT 'W','C','Product','bag',date '2026-11-07',999::numeric,'capture failed'
            UNION ALL SELECT 'W','Z','Product','bag',date '2026-11-09',0::numeric,''
            UNION ALL SELECT 'W2','A','Product','bag',date '2026-11-10'-n,1::numeric,'' FROM generate_series(1,7) n
            UNION ALL SELECT 'W2','C','Product','bag',date '2026-11-09',10::numeric,''
            UNION ALL SELECT 'W2','C','Product','bag',date '2026-11-08',1::numeric,''
        ), '''
        sql = template.replace('public.community_plan_sku_mapping', 'fixture_mapping').replace('public.pdd_order_today', 'fixture_today').replace('public.pdd_sales_data', 'fixture_sales')
        # Distinct cities sharing a cloud: combine daily totals before peak/average.
        shared_group = warehouse_city('佛山1仓')
        assert shared_group == warehouse_city('顺德1仓')
        cursor.execute(fixture + sql.removeprefix('WITH '), {'day': date(2026, 11, 10), 'capture_day': date(2026, 11, 10),
            'warehouse_cities': json.dumps({'W': shared_group, 'W2': warehouse_city('顺德1仓')})})
        samples = {row['sku_code']: row for row in [dict(zip([c[0] for c in cursor.description], r)) for r in cursor.fetchall()]}
        assert set(samples) == {'S', 'T', 'N', 'Z'}, samples
        assert samples['S']['estimated_sales'] == 52 and samples['S']['platform_sku_count'] == 2
        assert samples['S']['platform_warehouse_count'] == 2 and samples['S']['warehouse_name'] == shared_group
        assert samples['S']['valid_days'] == 7 and samples['S']['reference_sales'] == 11
        assert samples['T']['valid_days'] == 2 and samples['T']['reference_sales'] == 12  # max(2+10,9+1), not 9+10.
        assert samples['Z']['valid_days'] == 1 and samples['Z']['reference_sales'] == 0
        assert samples['N']['valid_days'] == 0 and samples['N']['reference_sales'] is None
        print('SELECT fixtures passed: original-warehouse dedup, ratios, city/day sum BEFORE average/peak, zero/missing, capture date.', flush=True)

        started = time.perf_counter()
        with urllib.request.urlopen('http://127.0.0.1:8791/api/daily-sales?refresh=1', timeout=50) as response:
            payload = json.load(response)
        assert payload['ok'], payload
        day = date.fromisoformat(payload['date'])
        warehouse_cities = payload['warehouse_groups']
        cursor.execute('''SELECT DISTINCT ON ("platform_skuId") "platform_skuId",sku_code,COALESCE(ratio,1),sku_name
            FROM public.community_plan_sku_mapping
            ORDER BY "platform_skuId",updated_date DESC NULLS LAST,id DESC''')
        latest = {product:(sku,ratio,name) for product,sku,ratio,name in cursor.fetchall()}
        mapping = {product:[(sku,ratio)] for product,(sku,ratio,name) in latest.items() if sku and sku.strip() and ratio>0 and product!='1013779478701'}
        components=[('867646793186','QY-HJKC508G'),('867646598972','QY-YWKC508G')]
        if all(p in latest and latest[p][0]==sku and latest[p][1]==1 and latest[p][2] and latest[p][2].strip() for p,sku in components):
            mapping['1013779478701']=[(sku,Decimal(1)) for _,sku in components]
        cursor.execute('''SELECT account_name,warehouse_name,product_id,estimated_total_sales
            FROM public.pdd_order_today WHERE sale_date=%s AND (%s::date IS NULL OR captured_date=%s::date)
            ORDER BY estimated_updated_at DESC NULLS LAST,captured_date DESC NULLS LAST,id DESC''', [day, payload['capture_date'], payload['capture_date']])
        seen = set()
        forecasts = defaultdict(list)
        converted_products = set()
        for account, warehouse, product, quantity in cursor.fetchall():
            key = (account, warehouse, product)
            if key in seen or product not in mapping:
                continue
            seen.add(key)
            for sku, ratio in mapping[product]:
                if ratio != 1:
                    converted_products.add(product)
                forecasts[(warehouse_cities[warehouse], sku)].append(quantity * ratio if quantity is not None else None)
        cursor.execute('''SELECT warehouse_name,product_id,sale_date,sell_unit_total FROM public.pdd_sales_data
            WHERE sale_date>=%s AND sale_date<%s AND COALESCE(error_message,'')='' AND sell_unit_total IS NOT NULL''', [day - timedelta(days=7), day])
        daily = defaultdict(lambda: defaultdict(Decimal))
        for warehouse, product, sales_day, quantity in cursor.fetchall():
            if product in mapping:
                for sku, ratio in mapping[product]:
                    daily[(warehouse_cities[warehouse], sku)][sales_day] += quantity * ratio
        keys = set(forecasts) | set(daily)
        assert len(keys) == len(payload['rows'])
        actual_keys = set()
        for row in payload['rows']:
            key = (row['warehouse_name'], row['sku_code'])
            assert key not in actual_keys
            actual_keys.add(key)
            values = list(daily[key].values())
            reference = sum(values) / 7 if len(values) == 7 else max(values) if values else None
            quantities = forecasts.get(key, [])
            expected_forecast = sum(quantities) if quantities and all(v is not None for v in quantities) else None
            assert row['valid_days'] == len(values)
            for field, expected in [('reference_sales', reference), ('estimated_sales', expected_forecast)]:
                assert (row[field] is None and expected is None) or (expected is not None and abs(row[field] - float(expected)) < 1e-7), (key, field, row[field], expected)
        print(json.dumps({'live_rows_verified': len(payload['rows']), 'date': payload['date'], 'unmapped': len(payload['unmapped']), 'non_one_ratio_products': len(converted_products), 'source_query_ms': payload['query_ms'], 'http_seconds': round(time.perf_counter()-started, 2)}, ensure_ascii=False), flush=True)
finally:
    connection.close()

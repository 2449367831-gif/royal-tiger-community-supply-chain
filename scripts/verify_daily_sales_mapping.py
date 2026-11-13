"""Execute production mapping/aggregation SQL on read-only PostgreSQL VALUES fixtures."""
import json
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.query_daily_sales_web import required, mapping_sql, sales_sql
import psycopg2

c=psycopg2.connect(host=required('PGHOST'),port=required('PGPORT'),dbname=required('PGDATABASE'),
    user=required('PGUSER'),password=required('PGPASSWORD'),connect_timeout=10)
c.set_session(readonly=True)
fixture='''WITH fixture_mapping("platform_skuId",sku_code,sku_name,ratio,updated_date,id) AS (VALUES
    ('LATEST','OLD','Old',1::numeric,timestamp '2026-01-01',1),
    ('LATEST','NEW','New',3,timestamp '2026-02-01',2),
    ('TIE','OLD','Old',1,timestamp '2026-02-01',3),
    ('TIE','TNEW','New',2,timestamp '2026-02-01',4),
    ('NULLDATE','OLD','Old',1,NULL,99),
    ('NULLDATE','DATED','New',1,timestamp '2026-01-01',1),
    ('INVALID','OLD','Old',1,timestamp '2026-01-01',1),
    ('INVALID','','Invalid',1,timestamp '2026-02-01',2),
    ('NULLRATIO','ONE','One',NULL,timestamp '2026-01-01',1),
    ('BADRATIO','BAD','Bad',0,timestamp '2026-01-01',1),
    ('1013779478701','SYNTHETIC','Bundle',1,timestamp '2026-01-01',1),
    ('867646793186','QY-HJKC508G','Black pepper',1,timestamp '2026-01-01',1),
    ('867646598972','QY-YWKC508G','Original',1,timestamp '2026-01-01',1)
), '''
try:
 with c.cursor() as q:
    fragment=mapping_sql().replace('public.community_plan_sku_mapping','fixture_mapping')
    q.execute(fixture+fragment+' SELECT product_id,sku_code,ratio FROM mapping')
    rows=q.fetchall()
    mapped={p:[] for p,_,_ in rows}
    for p,sku,ratio in rows:mapped[p].append((sku,ratio))
    assert mapped['LATEST']==[('NEW',3)] and mapped['TIE']==[('TNEW',2)]
    assert mapped['NULLDATE']==[('DATED',1)] and mapped['NULLRATIO']==[('ONE',1)]
    assert 'INVALID' not in mapped and 'BADRATIO' not in mapped
    assert set(mapped['1013779478701'])=={('QY-HJKC508G',1),('QY-YWKC508G',1)}
    broken=fixture.replace("('867646598972','QY-YWKC508G','Original',1,", "('867646598972','QY-YWKC508G','Original',2,")
    q.execute(broken+fragment+" SELECT COUNT(*) FROM mapping WHERE product_id='1013779478701'")
    assert q.fetchone()[0]==0, 'Incomplete bundle must not become partial or synthetic demand'
    sources='''fixture_today AS (
        SELECT 'a'::text account_name,'W'::text warehouse_name,'wid'::text warehouse_id,
        '1013779478701'::text product_id,'Bundle'::text product_name,'bag'::text sell_unit_name,
        10::numeric estimated_total_sales,timestamp '2026-11-10' estimated_updated_at,
        date '2026-11-10' captured_date,1 id,date '2026-11-10' sale_date
        UNION ALL SELECT 'a','W','wid','867646793186','Single','bag',2,timestamp '2026-11-10',date '2026-11-10',2,date '2026-11-10'
    ), fixture_sales AS (
        SELECT 'W'::text warehouse_name,'1013779478701'::text product_id,date '2026-11-10'-n sale_date,
        4::numeric sell_unit_total,''::text error_message FROM generate_series(1,7) n
        UNION ALL SELECT 'W','867646793186',date '2026-11-10'-n,1,'' FROM generate_series(1,7) n
    ), '''
    sql=sales_sql().replace('public.community_plan_sku_mapping','fixture_mapping').replace('public.pdd_order_today','fixture_today').replace('public.pdd_sales_data','fixture_sales')
    q.execute(fixture+sources+sql.removeprefix('WITH '),{'day':date(2026,11,10),'capture_day':date(2026,11,10),'warehouse_cities':json.dumps({'W':'City'})})
    result={row['sku_code']:row for row in [dict(zip([col[0] for col in q.description],r)) for r in q.fetchall()]}
    assert set(result)=={'QY-HJKC508G','QY-YWKC508G'}
    assert result['QY-HJKC508G']['estimated_sales']==12 and result['QY-HJKC508G']['reference_sales']==5
    assert result['QY-YWKC508G']['estimated_sales']==10 and result['QY-YWKC508G']['reference_sales']==4
    assert all(row['valid_days']==7 for row in result.values())
 print('Mapping SELECT fixtures passed: latest date/id, null ratio, invalid latest, atomic bundle split, forecast/history expansion before average.')
finally:c.close()

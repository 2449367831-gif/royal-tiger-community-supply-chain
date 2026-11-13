"""Read-only PG forecasts and previous-seven-calendar-day warehouse/SKU sales."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

CHINA = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def required(name):
    value = os.environ.get(name)
    if not value and os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                value, _ = winreg.QueryValueEx(key, name)
        except OSError:
            pass
    if not value:
        raise RuntimeError('CONFIG_ERROR')
    return value


def encode(value):
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError('unsupported query value')


def default_forecast_day(today, available):
    """Only use today's capture or yesterday's; never silently use older data."""
    if today in available:
        return today, 'today'
    yesterday = today - timedelta(days=1)
    if yesterday in available:
        return yesterday, 'yesterday'
    return today, 'missing'


def mapping_sql():
    return (ROOT / 'sql/daily_sales_mapping.sql').read_text(encoding='utf-8').strip()


def sales_sql():
    return (ROOT / 'sql/daily_sales_web.sql').read_text(encoding='utf-8').replace('__SKU_MAPPING_CTES__', mapping_sql())


def run(payload):
    started = time.perf_counter()
    if not isinstance(payload, dict) or set(payload) - {'date'}:
        raise ValueError('INVALID_PARAMS')
    selected = payload.get('date')
    today = datetime.now(CHINA).date()
    if selected and (date.fromisoformat(selected).isoformat() != selected or date.fromisoformat(selected) > today):
        raise ValueError('INVALID_PARAMS')
    import psycopg2
    connection = psycopg2.connect(host=required('PGHOST'), port=int(required('PGPORT')),
                                  dbname=required('PGDATABASE'), user=required('PGUSER'),
                                  password=required('PGPASSWORD'), connect_timeout=10,
                                  options='-c timezone=Asia/Shanghai -c statement_timeout=30000')
    try:
        connection.set_session(readonly=True, isolation_level='REPEATABLE READ')
        with connection.cursor() as cursor:
            # The source's stale row estimates otherwise choose an 18k x 7k
            # nested loop. Transaction-local only; no shared database settings.
            cursor.execute('SET LOCAL enable_nestloop = off')
            cursor.execute('SELECT current_database()')
            if cursor.fetchone()[0] != 'scrape_data':
                raise RuntimeError('CONFIG_ERROR')
            cursor.execute('''SELECT sale_date FROM public.pdd_order_today
                WHERE sale_date BETWEEN %s AND %s AND captured_date=sale_date
                  AND estimated_total_sales IS NOT NULL GROUP BY sale_date''', [today - timedelta(days=1), today])
            available = {r[0] for r in cursor.fetchall()}
            cursor.execute('SELECT MAX(sale_date) FROM public.pdd_order_today WHERE sale_date <= %s', [today])
            latest = cursor.fetchone()[0]
            day, freshness = (date.fromisoformat(selected), 'selected') if selected else default_forecast_day(today, available)
            capture_day = None if selected else day
            # Excel assigns each platform warehouse to one cloud-code group.
            # Merge daily quantities BEFORE computing the average/maximum.
            from scripts.daily_sales_cities import warehouse_city, policy
            rules = policy()
            cursor.execute('''SELECT warehouse_name FROM public.pdd_order_today WHERE sale_date=%(day)s
                UNION SELECT warehouse_name FROM public.pdd_sales_data
                WHERE sale_date>=%(day)s::date-7 AND sale_date<%(day)s::date''', {'day': day})
            warehouse_cities = {r[0]: warehouse_city(r[0], rules) for r in cursor.fetchall() if r[0]}
            cursor.execute(sales_sql(), {
                'day': day, 'capture_day': capture_day,
                'warehouse_cities': json.dumps(warehouse_cities, ensure_ascii=False)})
            columns = [column[0] for column in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
            if len(rows) > 20000:
                raise RuntimeError('TOO_MANY_ROWS')
            cursor.execute('WITH ' + mapping_sql() + ''', source AS (
                SELECT '预估' AS source, warehouse_name, product_id, product_name FROM public.pdd_order_today WHERE sale_date=%(day)s
                    AND (%(capture_day)s::date IS NULL OR captured_date=%(capture_day)s::date)
                UNION
                SELECT '历史' AS source, warehouse_name, product_id, product_name FROM public.pdd_sales_data
                WHERE sale_date>=%(day)s::date-7 AND sale_date<%(day)s::date AND COALESCE(error_message,'')='' AND sell_unit_total IS NOT NULL
            ) SELECT source,warehouse_name,product_id,MAX(product_name) AS product_name
            FROM source WHERE NOT EXISTS(SELECT 1 FROM mapping m WHERE m.product_id=source.product_id)
            GROUP BY source,warehouse_name,product_id ORDER BY source,warehouse_name,product_id LIMIT 20001''', {'day': day, 'capture_day': capture_day})
            unmapped = [dict(zip([col[0] for col in cursor.description], row)) for row in cursor.fetchall()]
    finally:
        connection.close()
    from scripts.daily_sales_alerts import enrich_alerts
    alert_metadata = enrich_alerts(rows, day)
    return {'ok': True, 'rows': rows, 'row_count': len(rows), 'date': day.isoformat(),
            'alerts': alert_metadata,
            'latest_date': latest.isoformat() if latest else None, 'today': today.isoformat(),
            'forecast_freshness': freshness, 'capture_date': capture_day.isoformat() if capture_day else None,
            'start_date': (day - timedelta(days=7)).isoformat(),
            'end_date': (day - timedelta(days=1)).isoformat(),
            'queried_at': datetime.now(CHINA).isoformat(timespec='seconds'),
            'unmapped': unmapped, 'query_ms': round((time.perf_counter() - started) * 1000, 2),
            'warehouse_groups': warehouse_cities,
            'mapping_policy': 'community-plan-independent-mapping-bundle-split-v2',
            'warehouse_mapping_policy': rules['mode'],
            'warehouse_mapping_source': rules['source'],
            'mapping_table': 'public.community_plan_sku_mapping',
            'note': '按Excel指定云仓＋内部SKU汇总，共用同一云仓的平台仓先合并预估及每日历史销量，再计算7日参考；未列入Excel的新仓待核验，不按名称猜测。SKU映射读取社团计划独立表，初始复制既有映射，后续由n8n补全，不回写生产映射。按updated_date、id取最新记录，空ratio按1；黑椒＋原味烤肠组合商品1013779478701按已核验组件各1份拆分。预估及历史均先换算、拆分再逐日合计；满7天取日均，不足7天取单日最高，无数据展示 —。缺失或无效映射不计入；工作流标记bomType的其他组套未取得BOM明细，不猜测拆分。'}


def main():
    try:
        result = run(json.loads(sys.stdin.read() or '{}'))
        print(json.dumps(result, ensure_ascii=False, default=encode, separators=(',', ':')))
        return 0
    except Exception as exc:
        code = str(exc) if str(exc) in {'CONFIG_ERROR', 'NO_SOURCE_DATA', 'TOO_MANY_ROWS', 'INVALID_PARAMS'} else 'QUERY_FAILED'
        print(json.dumps({'ok': False, 'code': code, 'error': '销量查询失败，请检查数据库连接或稍后重试。'}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())

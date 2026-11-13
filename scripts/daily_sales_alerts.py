"""Join only verified warehouses; never treat absent stock as business zero."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHINA = timezone(timedelta(hours=8))


def assess(estimate, stock, reference):
    if estimate is not None and stock is not None and estimate > stock:
        return 'warning', ['预估超过库存（含在途）']
    if estimate is None or stock is None or reference is None:
        return 'unknown', []
    if estimate > reference * 3 and estimate < stock:
        return 'tight', ['预估超过参考销量3倍，仍低于库存（含在途）']
    return 'normal', []


def enrich_alerts(rows, day):
    from scripts import query_inventory_web as inventory
    from scripts.daily_sales_cities import configured_clouds, policy
    rules = policy()
    stocks = {}
    stock_status = 'failed'
    stock_queried_at = None
    inventory._load_env_file(inventory.AGENT_ENV_PATH)
    transit = {}
    transit_status = 'failed'
    warehouses = {}
    cities = {row['warehouse_name'] for row in rows}
    groups = {city: [] for city in cities}
    try:
        connection = inventory._connect()
        connection._read_timeout = 25
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT wms_code,wms_desc FROM ywzt_ods.master_wms WHERE wms_type=12 AND wms_status<>12')
                warehouses = {r['wms_code']: r['wms_desc'] for r in cursor.fetchall()}
                for group, configured in configured_clouds(cities, rules).items():
                    if configured:
                        code, name = configured
                        if warehouses.get(code) == name:
                            groups[group].append(code)
                if any(groups.values()):
                    parameters = {'warehouse_codes': tuple(sorted({code for codes in groups.values() for code in codes}))}
                    cursor.execute((ROOT / 'sql/daily_sales_stock.sql').read_text(encoding='utf-8'), parameters)
                    stock_rows = cursor.fetchall()
                    if not stock_rows:
                        raise RuntimeError('EMPTY_STOCK_SOURCE')
                    stocks = {(str(r['wms_code']), str(r['sku_code'])): r for r in stock_rows}
                    stock_status = 'available'
                    stock_queried_at = datetime.now(CHINA).isoformat(timespec='seconds')
                    cursor.execute((ROOT / 'sql/daily_sales_transit.sql').read_text(encoding='utf-8'),
                                   parameters)
                    transit = {(r['wms_code'], r['sku_code']): r for r in cursor.fetchall()}
                    transit_status = 'available'
                else:
                    stock_status = 'not_enabled'
                    transit_status = 'not_enabled'
        finally:
            connection.close()
    except Exception:
        pass  # Missing transit must never become zero/normal.
    unmapped = set()
    for row in rows:
        city = row['warehouse_name']
        codes = sorted(set(groups.get(city, [])))
        if not codes:
            unmapped.add(city)
        # A successful stock query treats an absent SKU or NULL rolling stock as
        # zero. Source failures and unconfigured warehouses remain unavailable.
        values = [stocks[(code, row['sku_code'])] for code in codes if (code, row['sku_code']) in stocks]
        def quantity(field):
            return sum(Decimal(str(v[field])) for v in values) if values and all(v.get(field) is not None for v in values) else None
        rolling = (sum((Decimal(str(v.get('rolling_stock') or 0)) for v in values), Decimal(0))
                   if codes and stock_status == 'available' else None)
        purchase = transfer = None
        if codes and transit_status == 'available':
            purchase = sum((transit.get((code, row['sku_code']), {}).get('purchase_transit', 0) for code in codes), Decimal(0))
            transfer = sum((transit.get((code, row['sku_code']), {}).get('transfer_transit', 0) for code in codes), Decimal(0))
        total_transit = purchase + transfer if purchase is not None and transfer is not None else None
        stock = rolling + total_transit if rolling is not None and total_transit is not None else None
        state, reasons = assess(row['estimated_sales'], stock, row['reference_sales'])
        missing = []
        if not codes:
            missing.append('未配置云仓或指定云仓不可用')
        elif rolling is None:
            missing.append('当前滚动库存缺失' if stock_status == 'available' else '滚动库存读取失败')
        if transit_status == 'failed':
            missing.append('在途读取失败')
        if row['estimated_sales'] is None:
            missing.append('预估销量缺失')
        if row['reference_sales'] is None:
            missing.append('历史销量缺失')
        row.update(cloud_warehouse_code='、'.join(codes) or None,
                   cloud_warehouse_codes=codes,
                   cloud_warehouse_name='、'.join(warehouses[code] for code in codes) or None,
                   cloud_warehouse_names=[warehouses[code] for code in codes],
                   fixed_stock=None, rolling_stock=rolling, realtime_stock=quantity('realtime_stock'),
                   pending_out=quantity('pending_out'), pending_in=quantity('pending_in'),
                   stock_changed_at=max((v.get('stock_changed_at') or '' for v in values), default='') or None,
                   purchase_transit=purchase, transfer_transit=transfer,
                   transit_stock=total_transit, cloud_stock=stock, alert_state=state,
                   alert_reasons=reasons, alert_missing=missing)
    return {'stock_basis': 'rolling-adjusted-with-transit-v1', 'stock_date': datetime.now(CHINA).date().isoformat(),
            'stock_status': stock_status, 'stock_queried_at': stock_queried_at,
            'snapshot_date': None, 'frozen_at': None, 'snapshot_status': 'not_used',
            'transit_status': transit_status,
            'transit_queried_at': datetime.now(CHINA).isoformat(timespec='seconds') if transit_status == 'available' else None,
            'unmapped_warehouses': sorted(unmapped),
            'city_cloud_mapping': {city: [{'code': c, 'name': warehouses[c]} for c in sorted(set(codes))] for city, codes in sorted(groups.items())},
            'warehouse_mapping_version': rules['version'],
            'warehouse_mapping_source_sha256': rules['source']['sha256'],
            'rule_version': 'stock-first-three-states-v2',
            'note': '仓库对应以业务确认Excel为准，按指定云仓编码＋内部SKU汇总。佛山与顺德、无锡与苏州分别合并销量，共用库存只计一次，不额外合并旧仓或其他共享仓。云仓库存＝本次滚动当日库存（实时库存－待出库＋待入库，按原SQL取整）＋本次采购在途＋调拨在途。库存源查询成功时，商品滚动库存为NULL、0或无对应库存行均按0计算，再加在途；库存源查询失败或整体为空不作零库存。每4小时同时更新当前库存和在途，不读取昨日固定档案；所选业务日期用于销量批次，库存为本次刷新时点。预估超过库存优先红色库存不足；超过参考销量3倍且低于库存为黄色库存紧张；数据齐全且未触发以上条件为绿色正常销售。等于阈值不触发预警；必要数据缺失且无已知缺货则待核验。不足7天仍取已有单日最高销量作为参考。'}

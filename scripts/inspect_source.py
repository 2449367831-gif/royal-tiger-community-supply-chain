import sys,json
from pathlib import Path
from datetime import date,datetime,timedelta,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.query_daily_sales_web import required
import psycopg2
conn=psycopg2.connect(host=required('PGHOST'),port=int(required('PGPORT')),dbname=required('PGDATABASE'),user=required('PGUSER'),password=required('PGPASSWORD'),connect_timeout=10,options='-c timezone=Asia/Shanghai -c statement_timeout=15000')
conn.set_session(readonly=True)
today=datetime.now(timezone(timedelta(hours=8))).date()
try:
    with conn.cursor() as cur:
        cur.execute('''SELECT MIN(sale_date),MAX(sale_date),COUNT(DISTINCT sale_date),COUNT(*) FROM public.pdd_sales_data WHERE sale_date >= %s AND sale_date < %s AND COALESCE(error_message,'')='' AND sell_unit_total IS NOT NULL''',[today-timedelta(days=366),today])
        row=cur.fetchone()
        result={'source':'public.pdd_sales_data','window_days':366,'earliest_date':str(row[0]),'latest_date':str(row[1]),'calendar_days_observed':row[2],'valid_rows':row[3],'checked_at':datetime.now(timezone(timedelta(hours=8))).isoformat(),'note':'Read-only source availability check; not validated per-SKU continuous history. No missing records filled with zero.'}
        p=(Path(__file__).resolve().parents[1]/'sales_forecasting/artifacts/production/source_availability.json');p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
        latest=p.parent/'latest.json'
        if not latest.exists() or json.loads(latest.read_text(encoding='utf-8')).get('status')!='ready':
            latest.write_text(json.dumps({'ok':True,'status':'insufficient_history','synthetic':False,'rows':[],'evaluation':[],'procurement':[],
                'source_availability':result,'message':f'已检查真实销量来源：{result["earliest_date"]} 至 {result["latest_date"]}，共 {row[2]} 个有数据日期；不足默认180天训练要求，尚未生成正式预测。各SKU连续性仍需校验。'},ensure_ascii=False),encoding='utf-8')
finally:conn.close()

"""Build a new stock + all-SKU movement batch, outside immutable daily archives."""
import json
import os
import sys
import uuid
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import inventory_history as history
from scripts import query_inventory_web as stock

def main():
    directory = None
    try:
        stock._load_env_file(stock.AGENT_ENV_PATH)
        raw_parameters = json.loads(sys.stdin.read() or '{}')
        parameters = stock.validate_input(raw_parameters)
        day = stock.china_today().isoformat()
        if parameters.get('stock_date') != day or parameters['sku_codes'] not in ([], ['']) or parameters['warehouse_codes'] not in ([], ['']):
            raise ValueError('full current-day snapshot required')
        root = Path(os.environ['INVENTORY_SNAPSHOT_DIR'])
        batch_id = uuid.uuid4().hex
        directory = root / 'batches' / batch_id
        metadata = history.ensure_archive(day, directory, dashboard=True, stock_parameters=raw_parameters)
        if not metadata:
            raise RuntimeError('empty source')
        result = history.read_stock(day, parameters, directory)
        result.update(snapshot_batch=batch_id, stock_basis='dashboard_snapshot', freeze_kind='dashboard',
            stock_note='看板数据快照 · 当日库存＝实时库存－待出库＋待入库',
            queried_at=metadata['stock_queried_at'])
        print(json.dumps(result, ensure_ascii=False, separators=(',', ':')))
    except Exception:
        if directory:
            shutil.rmtree(directory, ignore_errors=True)
        print(json.dumps({'ok': False, 'code': 'SNAPSHOT_UPDATE_FAILED', 'error': '库存快照更新失败，原快照保留。'}, ensure_ascii=False))
        return 1
    return 0

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())

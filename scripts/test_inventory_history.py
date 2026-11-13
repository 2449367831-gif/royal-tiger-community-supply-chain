from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from scripts import inventory_history as history
from scripts import query_inventory_web as stock
from scripts import query_inventory_movements as movements


DAY = "2026-10-25"
NOW = datetime(2026, 10, 25, 3, 35, tzinfo=history.CHINA)


def source_result(quantity=80):
    row = {key: None for key in stock.REQUIRED_COLUMNS}
    row.update({"仓库编码": "W", "仓库名称": "冻结仓名", "SKU编码": "S", "SKU名称": "冻结商品名", "当日库存": quantity, "实时库存": quantity + 219, "待出库": 219, "待入库": 0})
    return {"rows": [row], "sql_sha256": "frozen-source"}


def capture_events(db, day, keys):
    rows = [{"movement_time": day + " 10:00:00", "direction": "in", "warehouse_code": "W", "sku_code": "S", "quantity": 5, "reference": "T1", "order_status": "16", "source_kind": "transfer_request", "source_label": "调拨申请入库"}]
    blob, sha = history._pack(rows)
    db.execute("INSERT INTO movements VALUES (?,?,?,?)", ("W", "S", blob, sha))
    return 1, "event-source"


class ImmutableHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def seed(self):
        with patch.object(stock, "_query_source", return_value=source_result()), patch.object(history, "_capture_movements", side_effect=capture_events):
            return history.ensure_archive(DAY, self.directory, NOW)

    def test_dashboard_batch_is_separate_and_reads_stock_and_movements_without_database(self):
        batch = self.directory / 'dashboard-batch'
        early = datetime(2026, 10, 25, 1, 0, tzinfo=history.CHINA)
        with patch.object(stock, '_query_source', return_value=source_result()), patch.object(history, '_capture_movements', side_effect=capture_events):
            metadata = history.ensure_archive(DAY, batch, early, dashboard=True,
                stock_parameters={'stock_date': DAY, 'sku_codes': [], 'warehouse_codes': []})
        self.assertEqual(metadata['freeze_kind'], 'dashboard')
        self.assertFalse(history.archive_path(DAY, self.directory).exists())
        baseline = history.read_stock(DAY, {}, batch)
        baseline.update(stock_basis='dashboard_snapshot', _archive_directory=str(batch), queried_at='original-source-time')
        with patch.object(movements, 'execute_range', side_effect=AssertionError('database must not be queried')):
            result = movements.run({'warehouse_code': 'W', 'sku_code': 'S', 'start_date': DAY, 'end_date': DAY}, baseline)
        self.assertEqual(result['current_stock'], 80)
        self.assertEqual(result['total_in'], 5)
        self.assertEqual(result['queried_at'], 'original-source-time')

    def test_repeat_capture_and_query_do_not_read_mutated_upstream_or_change_file(self):
        original = self.seed()
        path = history.archive_path(DAY, self.directory)
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        with patch.object(history, "DEFAULT_DIR", self.directory), patch.object(stock, "_query_source", side_effect=AssertionError("upstream must not be read")), patch.object(movements, "execute_range", side_effect=AssertionError("events must not be re-queried")):
            self.assertEqual(history.ensure_archive(DAY), original)
            params = stock.validate_input({"stock_date": DAY, "warehouse_codes": ["W"], "sku_codes": ["S"]})
            result = stock.run_query(params)
            self.assertEqual(result["rows"][0]["当日库存"], 80)
            self.assertEqual(result["rows"][0]["仓库名称"], "冻结仓名")
            detail = movements.run({"warehouse_code": "W", "sku_code": "S", "start_date": "2026-10-19", "end_date": DAY})
            self.assertEqual(detail["current_stock"], 80)
            self.assertEqual(detail["opening_stock"], 75)
            self.assertEqual(detail["rows"][0]["after_stock"], 80)
            self.assertEqual(detail["frozen_at"], original["frozen_at"])
            empty = stock.run_query(stock.validate_input({"stock_date": DAY, "warehouse_codes": ["unknown"]}))
            self.assertEqual(empty["rows"], [])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), file_hash)

    def test_failed_or_empty_source_never_publishes_partial_archive_and_retry_works(self):
        with patch.object(stock, "_query_source", return_value=source_result()), patch.object(history, "_capture_movements", side_effect=RuntimeError("source interrupted")):
            with self.assertRaises(RuntimeError):
                history.ensure_archive(DAY, self.directory, NOW)
        self.assertEqual(list(self.directory.glob("*.sqlite3")), [])
        self.assertEqual(list(self.directory.glob("*.tmp")), [])
        with patch.object(stock, "_query_source", return_value={"rows": []}):
            self.assertIsNone(history.ensure_archive(DAY, self.directory, NOW))
        self.assertFalse(history.archive_path(DAY, self.directory).exists())
        self.assertTrue(self.seed()["complete"])

    def test_two_simultaneous_writers_publish_exactly_one_complete_version(self):
        def load_source(*args, **kwargs):
            time.sleep(.1)
            return source_result(threading.get_ident())
        with patch.object(stock, "_query_source", side_effect=load_source), patch.object(history, "_capture_movements", side_effect=capture_events):
            with ThreadPoolExecutor(max_workers=2) as executor:
                a, b = list(executor.map(lambda _: history.ensure_archive(DAY, self.directory, NOW), range(2)))
        self.assertEqual(a, b)
        self.assertEqual(len(list(self.directory.glob("*.sqlite3"))), 1)
        self.assertEqual(len(list(self.directory.glob("*.tmp"))), 0)

    def test_schedule_waits_for_0330_and_catches_up_missing_days(self):
        before = datetime(2026, 11, 2, 3, 29, tzinfo=history.CHINA)
        after = datetime(2026, 11, 2, 3, 30, tzinfo=history.CHINA)
        with patch.object(stock, "_query_source", side_effect=AssertionError("too early")):
            self.assertIsNone(history.ensure_archive("2026-11-02", self.directory, before))
        self.assertEqual(history.due_dates(date(2026, 11, 1), before, self.directory), ["2026-11-01"])
        self.assertEqual(history.due_dates(date(2026, 11, 1), after, self.directory), ["2026-11-01", "2026-11-02"])

    def test_new_archive_freezes_adjusted_317_not_realtime_536(self):
        with patch.object(stock, "_query_source", return_value=source_result(317)) as query, patch.object(history, "_capture_movements", side_effect=capture_events):
            history.ensure_archive(DAY, self.directory, NOW)
        self.assertNotIn("historical", query.call_args.kwargs)
        result = history.read_stock(DAY, {}, self.directory)
        self.assertEqual(result["rows"][0]["当日库存"], 317)
        self.assertEqual(result["rows"][0]["实时库存"], 536)
        self.assertEqual(result["rows"][0]["待出库"], 219)
        self.assertEqual(result["stock_formula"], "realtime-minus-pending-out-plus-pending-in")

    def test_missing_past_day_is_not_fabricated_from_current_pending_orders(self):
        with patch.object(stock, "_query_source", side_effect=AssertionError("no historical pending state")):
            self.assertIsNone(history.ensure_archive(DAY, self.directory, datetime(2026,11,3,10,tzinfo=history.CHINA)))
        self.assertFalse(history.archive_path(DAY, self.directory).exists())

    def test_legacy_raw_inventory_is_not_presented_as_daily_and_correction_is_audited(self):
        import json
        self.seed()
        path = history.archive_path(DAY, self.directory)
        with sqlite3.connect(path) as db:
            metadata = json.loads(db.execute('SELECT payload FROM metadata').fetchone()[0])
            metadata.pop('stock_formula')
            db.execute('UPDATE metadata SET payload=?',(json.dumps(metadata),))
        db.close()
        legacy = history.read_stock(DAY, {}, self.directory)
        self.assertIsNone(legacy['rows'][0]['当日库存'])
        audit = {'original_stock_sha256':metadata['stock_sha256'],'rows':[{'warehouse':'W','sku':'S','daily_stock':317,'realtime_stock':536,'pending_out':219,'pending_in':0,'corrected_at':'2026-11-03T10:00:00+08:00'}]}
        path.with_suffix('.corrections.json').write_text(json.dumps(audit),encoding='utf-8')
        corrected = history.read_stock(DAY, {}, self.directory)
        self.assertEqual(corrected['rows'][0]['当日库存'],317)
        self.assertEqual(corrected['corrected_stock_count'],1)

    def test_archive_connections_are_read_only_and_checksums_are_enforced(self):
        self.seed()
        with history._open(DAY, self.directory) as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM stock")
        blob, sha = history._pack({"stock": 80})
        with self.assertRaises(RuntimeError):
            history._unpack(blob, "0" * len(sha))


if __name__ == "__main__":
    unittest.main()

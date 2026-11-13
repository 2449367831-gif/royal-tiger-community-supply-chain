from __future__ import annotations

import importlib.util
import hashlib
import json
import unittest
from datetime import date
from unittest.mock import MagicMock, patch
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("query_inventory_web.py")
SPEC = importlib.util.spec_from_file_location("query_inventory_web", MODULE_PATH)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class InventoryQueryContractTests(unittest.TestCase):
    def test_stock_date_rejects_future_empty_and_impossible_dates(self):
        for value in ["", None, "2026-02-30", "2026-10-27"]:
            with self.subTest(value=value), self.assertRaises(module.QueryInputError):
                module.validate_input({"stock_date": value}, today=date(2026, 10, 26))
        self.assertEqual(module.validate_input({"stock_date": "2026-10-25"}, today=date(2026, 10, 26))["stock_date"], "2026-10-25")

    def test_historical_date_uses_only_exact_day_snapshot_and_keeps_stock_contract(self):
        parameters = module.validate_input({"stock_date": "2026-10-24", "warehouse_codes": ["W"], "sku_codes": ["S"]}, today=date(2026, 10, 26))
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [{key: None for key in module.REQUIRED_COLUMNS} | {"仓库编码": "W", "SKU编码": "S", "当日库存": 445}]
        with patch.object(module, "china_today", return_value=date(2026, 10, 26)), patch.object(module, "_connect", return_value=connection):
            result = module._query_source(parameters, historical=True)
        sql, bindings = cursor.execute.call_args.args
        self.assertIn("FROM scm_warehous_manage_backups", sql)
        self.assertIn("backups_date = %s", sql)
        self.assertNotIn("scm_transfer_order", sql)
        self.assertEqual(bindings, ["2026-10-24", "S", "S", "W", "W"])
        self.assertEqual(result["stock_basis"], "daily_snapshot")
        self.assertEqual(result["stock_date"], "2026-10-24")
        self.assertEqual(result["rows"][0]["当日库存"], 445)
        self.assertEqual(result["sql_sha256"], hashlib.sha256(module.SNAPSHOT_SQL_PATH.read_bytes()).hexdigest())

    def test_today_keeps_original_sql_and_empty_history_does_not_fall_back(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        with patch.object(module, "china_today", return_value=date(2026, 10, 26)), patch.object(module, "_connect", return_value=connection):
            today_result = module.run_query(module.validate_input({"stock_date": "2026-10-26"}))
            self.assertEqual(today_result["sql_sha256"], hashlib.sha256(module.SQL_PATH.read_bytes()).hexdigest())
            self.assertEqual(today_result["stock_basis"], "live_adjusted")
            historical = module._query_source(module.validate_input({"stock_date": "2026-10-25"}), historical=True)
            self.assertEqual(historical["rows"], [])
            self.assertEqual(historical["stock_basis"], "daily_snapshot")
        self.assertEqual(cursor.execute.call_count, 2)

    def test_empty_filters_use_sentinel_and_keep_empty_date_semantics(self):
        params = module.validate_input({})
        sql, bindings = module.build_bound_query(params, "SELECT DATE_FORMAT(x, '%Y-%m-%d') WHERE a IN ([DYNAMIC_PARAMS.SKU编码]) AND '' = [DYNAMIC_PARAMS.t1]")
        self.assertEqual(params["sku_codes"], [""])
        self.assertEqual(params["warehouse_codes"], [""])
        self.assertEqual(bindings, ["", ""])
        self.assertIn("DATE_FORMAT(x, '%%Y-%%m-%%d')", sql)
        self.assertEqual(sql.count("%s"), 2)

    def test_each_marker_occurrence_is_bound_and_values_are_not_interpolated(self):
        params = module.validate_input({
            "sku_codes": ["SKU-A", "SKU-B"],
            "warehouse_codes": ["W-1"],
            "start_date": "2026-09-01",
            "end_date": "2026-09-03",
        })
        sql, bindings = module.build_bound_query(
            params,
            "SELECT * FROM t WHERE s IN ([DYNAMIC_PARAMS.SKU编码]) AND w IN ([DYNAMIC_PARAMS.仓库编码]) AND d BETWEEN [DYNAMIC_PARAMS.t1] AND [DYNAMIC_PARAMS.t2]",
        )
        self.assertNotIn("DYNAMIC_PARAMS", sql)
        self.assertEqual(bindings, ["SKU-A", "SKU-B", "W-1", "2026-09-01", "2026-09-03"])
        self.assertEqual(sql.count("%s"), len(bindings))
        self.assertNotIn("SKU-A", sql)

    def test_rejects_partial_or_reversed_date_and_duplicate_filters(self):
        with self.assertRaises(module.QueryInputError) as partial:
            module.validate_input({"start_date": "2026-09-01"})
        self.assertEqual(partial.exception.code, "INVALID_DATE_RANGE")
        with self.assertRaises(module.QueryInputError):
            module.validate_input({"start_date": "2026-09-03", "end_date": "2026-09-01"})
        with self.assertRaises(module.QueryInputError) as duplicate:
            module.validate_input({"sku_codes": ["SKU-A", "SKU-A"]})
        self.assertEqual(duplicate.exception.code, "DUPLICATE_FILTER")

    def test_rejects_unknown_fields_null_dates_and_impossible_calendar_dates(self):
        with self.assertRaises(module.QueryInputError) as unknown:
            module.validate_input({"sql": "SELECT 1"})
        self.assertEqual(unknown.exception.code, "INVALID_PARAMS")
        with self.assertRaises(module.QueryInputError) as null_date:
            module.validate_input({"start_date": None, "end_date": ""})
        self.assertEqual(null_date.exception.code, "INVALID_DATE_RANGE")
        with self.assertRaises(module.QueryInputError):
            module.validate_input({"start_date": "2026-02-30", "end_date": "2026-03-01"})

    def test_template_is_single_read_only_select(self):
        template, digest = module._read_template()
        self.assertTrue(template.lstrip().upper().startswith("SELECT"))
        self.assertNotIn(";", template)
        self.assertEqual(digest, hashlib.sha256(module.SQL_PATH.read_bytes()).hexdigest())

    def test_invalid_json_has_machine_readable_error_code(self):
        payload = module._error_payload(json.JSONDecodeError("bad", "{", 1))
        self.assertEqual(payload, {"ok": False, "code": "INVALID_JSON", "error": "input is not valid JSON"})


if __name__ == "__main__":
    unittest.main()

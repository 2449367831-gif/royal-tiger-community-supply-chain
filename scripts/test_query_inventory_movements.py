from __future__ import annotations

import importlib.util
import unittest
from datetime import date
from pathlib import Path


path = Path(__file__).with_name("query_inventory_movements.py")
spec = importlib.util.spec_from_file_location("query_inventory_movements", path)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MovementContractTests(unittest.TestCase):
    def test_cached_baseline_requires_same_live_date_warehouse_and_sku(self):
        params = {"end_date": "2026-11-05", "warehouse_code": "W", "sku_code": "S"}
        stock = {"ok": True, "stock_date": "2026-11-05", "stock_basis": "live_adjusted", "rows": [{"仓库编码": "W", "SKU编码": "S", "当日库存": 317}]}
        self.assertTrue(module.cached_stock_matches(stock, params))
        for field, value in [("end_date", "2026-11-04"), ("warehouse_code", "OTHER"), ("sku_code", "OTHER")]:
            self.assertFalse(module.cached_stock_matches(stock, {**params, field: value}))
        self.assertFalse(module.cached_stock_matches({**stock, "stock_basis": "daily_snapshot"}, params))
        self.assertFalse(module.cached_stock_matches({**stock, "rows": []}, params))

    def test_bind_sql_is_positional_and_contains_no_dynamic_markers(self):
        sql, params = module.bind_sql(
            "SELECT * FROM t WHERE w=[DYNAMIC_PARAMS.warehouse_code] AND s=[DYNAMIC_PARAMS.sku_code] AND d >= [DYNAMIC_PARAMS.t1] AND d < DATE_ADD([DYNAMIC_PARAMS.t2], INTERVAL 1 DAY)",
            warehouse_code="W-1", sku_code="S-1", start_date="2026-09-01", end_date="2026-09-30",
        )
        self.assertNotIn("DYNAMIC_PARAMS", sql)
        self.assertEqual(sql.count("%s"), 4)
        self.assertEqual(params, ["W-1", "S-1", "2026-09-01", "2026-09-30"])

    def test_validation_defaults_to_last_120_days_and_rejects_bad_input(self):
        result = module.validate_input({"warehouse_code": "W", "sku_code": "S"}, today=date(2026, 9, 11))
        self.assertEqual(result["start_date"], "2026-05-15")
        self.assertEqual(result["end_date"], "2026-09-11")
        with self.assertRaises(module.InputError):
            module.validate_input({"warehouse_code": "W", "sku_code": "S", "end_date": "2026-02-30"})
        with self.assertRaises(module.InputError):
            module.validate_input({"warehouse_code": "W", "sku_code": "S", "extra": 1})

    def test_default_window_handles_cross_year_and_leap_year_dates(self):
        self.assertEqual(
            module.validate_input({"warehouse_code": "W", "sku_code": "S"}, today=date(2024, 2, 29))["start_date"],
            "2023-11-02",
        )
        self.assertEqual(
            module.validate_input({"warehouse_code": "W", "sku_code": "S"}, today=date(2026, 1, 1))["start_date"],
            "2025-09-04",
        )

    def test_reconstruct_balance_accounts_for_requests_after_window(self):
        rows = [
            {"movement_time": "2026-09-02 10:00:00", "direction": "out", "quantity": 30, "reference": "T1", "order_status": "10"},
            {"movement_time": "2026-09-03 10:00:00", "direction": "in", "quantity": 10, "reference": "T2", "order_status": "16"},
        ]
        later = [{"movement_time": "2026-09-10 10:00:00", "direction": "out", "quantity": 5, "reference": "T3", "order_status": "16"}]
        opening, closing, output = module.reconstruct_balance(100, rows, "2026-09-05", later)
        self.assertEqual(opening, 125)
        self.assertEqual(closing, 105)
        self.assertEqual([r["before_stock"] for r in output], [125.0, 95.0])
        self.assertEqual(output[-1]["after_stock"], 105.0)

    def test_sql_file_is_read_only_and_hashed_from_actual_bytes(self):
        sql, digest = module.read_sql()
        self.assertTrue(sql.startswith("SELECT"))
        self.assertNotIn(";", sql)
        self.assertEqual(sql.upper().count("LIMIT 5001"), 1)
        self.assertTrue(sql.rstrip().upper().endswith("LIMIT 5001"))
        self.assertEqual(len(digest), 64)

    def test_invalid_quantity_current_stock_and_duplicate_event_are_rejected(self):
        with self.assertRaises(module.DataError) as quantity:
            module.reconstruct_balance(100, [{"direction": "out", "quantity": None, "reference": "T"}], "2026-09-01")
        self.assertEqual(quantity.exception.code, "INVALID_QUANTITY")
        with self.assertRaises(module.DataError) as current:
            module.reconstruct_balance(None, [], "2026-09-01")
        self.assertEqual(current.exception.code, "INVALID_CURRENT_STOCK")
        with self.assertRaises(module.DataError) as duplicate:
            module.validate_events([
                {"direction": "out", "reference": "T"},
                {"direction": "out", "reference": "T"},
            ])
        self.assertEqual(duplicate.exception.code, "DUPLICATE_EVENT")


if __name__ == "__main__":
    unittest.main()

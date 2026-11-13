const { test } = require("node:test");
const assert = require("node:assert/strict");
const { parseParameters, parseMovementParameters, selectStockRows } = require("../inventory_api");

test("filtering a complete live result preserves daily formula fields, zeroes, and query time", () => {
  const daily = { "仓库编码": "W", "SKU编码": "S", "当日库存": 317, "实时库存": 536, "待出库": 219, "待入库": 0 };
  const source = { ok: true, rows: [daily, { ...daily, "仓库编码": "OTHER" }], queried_at: "original" };
  const params = { sku_codes: ["S"], warehouse_codes: ["W"], start_date: "", end_date: "", stock_date: "2026-11-05" };
  const selected = selectStockRows(source, params);
  assert.deepEqual(selected.rows, [daily]);
  assert.equal(selected.row_count, 1);
  assert.equal(selected.queried_at, "original");
  assert.equal(source.rows.length, 2);
  assert.equal(selectStockRows(source, { ...params, sku_codes: ["MISSING"] }).row_count, 0);
});

test("stock date uses China day, rejects invalid or future dates, and preserves code filters", () => {
  const now = new Date("2026-10-25T16:01:00Z");
  const result = parseParameters("/api/inventory?stock_date=2026-10-26&warehouse=W&sku=S", now);
  assert.equal(result.stock_date, "2026-10-26");
  assert.deepEqual(result.warehouse_codes, ["W"]);
  assert.deepEqual(result.sku_codes, ["S"]);
  for (const value of ["", "2026-10-27", "2026-02-30", "2026-10-24&stock_date=2026-10-25"]) {
    assert.throws(() => parseParameters(`/api/inventory?stock_date=${value}`, now));
  }
  assert.deepEqual(parseMovementParameters("/api/inventory/movements?warehouse=W&sku=S&stock_date=2026-10-20&days=7", now), {
    warehouse_code: "W", sku_code: "S", start_date: "2026-10-14", end_date: "2026-10-20",
  });
  assert.throws(() => parseMovementParameters("/api/inventory/movements?warehouse=W&sku=S&stock_date=2026-10-27", now));
});

test("empty filters preserve ALL and multi-select values remain separate strings", () => {
  assert.deepEqual(parseParameters("/api/inventory"), { sku_codes: [], warehouse_codes: [], start_date: "", end_date: "" });
  const malicious = "x') OR 1=1 --";
  const params = new URLSearchParams();
  params.append("sku", malicious);
  params.append("sku", "normal");
  params.append("sku", "normal");
  assert.deepEqual(parseParameters(`/api/inventory?${params}`).sku_codes, [malicious, "normal"]);
});

test("calendar dates and complete ascending periods are required", () => {
  for (const query of ["start_date=2026-09-01", "end_date=2026-09-01", "start_date=2026-09-10&end_date=2026-09-01", "start_date=2026-02-30&end_date=2026-03-01", "start_date=2026-02-29&end_date=2026-03-01", "start_date=2026-09-01&start_date=2026-09-02&end_date=2026-09-03"]) {
    assert.throws(() => parseParameters(`/api/inventory?${query}`));
  }
  assert.equal(parseParameters("/api/inventory?start_date=2024-02-29&end_date=2024-02-29").start_date, "2024-02-29");
});

test("unknown, control-character, and excessive parameters are rejected", () => {
  assert.throws(() => parseParameters("/api/inventory?sql=DELETE"));
  assert.throws(() => parseParameters("/api/inventory?warehouse=%00"));
  assert.throws(() => parseParameters(`/api/inventory?sku=${"a".repeat(101)}`));
  const params = new URLSearchParams();
  for (let index = 0; index < 101; index += 1) params.append("sku", String(index));
  assert.throws(() => parseParameters(`/api/inventory?${params}`));
});

test("movement windows default to 120 China calendar days and require one warehouse SKU", () => {
  const now = new Date("2026-09-10T18:00:00Z");
  assert.deepEqual(parseMovementParameters("/api/inventory/movements?warehouse=W&sku=S", now), {
    warehouse_code: "W", sku_code: "S", start_date: "2026-05-15", end_date: "2026-09-11",
  });
  assert.deepEqual(parseMovementParameters("/api/inventory/movements?warehouse=W&sku=S&days=7", now), {
    warehouse_code: "W", sku_code: "S", start_date: "2026-09-05", end_date: "2026-09-11",
  });
  assert.deepEqual(parseMovementParameters("/api/inventory/movements?warehouse=W&sku=S&days=90", now).start_date, "2026-06-14");
  assert.deepEqual(parseMovementParameters("/api/inventory/movements?warehouse=W&sku=S&days=120", now).start_date, "2026-05-15");
  for (const params of ["sku=S", "warehouse=W", "warehouse=W&sku=S&days=8", "warehouse=W&sku=S&days=30&days=120", "warehouse=W&sku=S&sku=S", "warehouse=W&sku=S&stock=100"]) {
    assert.throws(() => parseMovementParameters(`/api/inventory/movements?${params}`, now));
  }
});

test("movement windows handle China midnight and leap-year boundaries", () => {
  const atChinaMidnight = new Date("2026-09-13T16:00:00Z");
  assert.equal(parseMovementParameters("/api/inventory/movements?warehouse=W&sku=S&days=120", atChinaMidnight).end_date, "2026-09-14");
  const leapDay = new Date("2024-02-29T03:00:00Z");
  assert.deepEqual(parseMovementParameters("/api/inventory/movements?warehouse=W&sku=S", leapDay), {
    warehouse_code: "W", sku_code: "S", start_date: "2023-11-02", end_date: "2024-02-29",
  });
  const crossYear = new Date("2026-01-01T03:00:00Z");
  assert.equal(parseMovementParameters("/api/inventory/movements?warehouse=W&sku=S&days=7", crossYear).start_date, "2025-12-26");
});

test('movement snapshot version accepts only a checksum, never a path', () => {
  const base = '/api/inventory/movements?warehouse=W&sku=S&snapshot_id=';
  assert.equal(parseMovementParameters(base + 'a'.repeat(64)).snapshot_id, 'a'.repeat(64));
  for (const value of ['../secret', 'a'.repeat(63), 'g'.repeat(64)]) assert.throws(() => parseMovementParameters(base + value));
});

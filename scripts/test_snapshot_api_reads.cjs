const { test, mock } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');

test('ordinary stock/sales reads and code filters never launch a source query, even for missing snapshots', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'snapshot-api-test-'));
  process.env.INVENTORY_SNAPSHOT_DIR = directory;
  const spy = mock.method(require('node:child_process'), 'execFile', () => { throw new Error('READ_MUST_NOT_LAUNCH_PYTHON'); });
  const { snapshotStore } = require('../dashboard_snapshot_store');
  const { fullStockParameters, readStockSnapshot } = require('../inventory_api');
  const { getSalesSnapshot } = require('../daily_sales_api');
  try {
    const params = fullStockParameters();
    await snapshotStore.save('stock', params, { ok: true, rows: [
      { '仓库编码': 'W', 'SKU编码': 'S', '当日库存': 317 }, { '仓库编码': 'W2', 'SKU编码': 'S', '当日库存': 5 }],
      stock_date: params.stock_date, queried_at: 'original', snapshot_batch: 'a'.repeat(32) });
    await snapshotStore.save('sales', { latest: true }, { ok: true, rows: [{ estimated_sales: 10 }], date: params.stock_date, queried_at: 'original' });
    for (let i = 0; i < 10; i++) {
      const result = await readStockSnapshot({ ...params, warehouse_codes: ['W'], sku_codes: ['S'] });
      assert.equal(result.row_count, 1); assert.equal(result.rows[0]['当日库存'], 317); assert.equal(result.queried_at, 'original');
      assert.equal((await getSalesSnapshot({})).rows[0].estimated_sales, 10);
    }
    assert.equal((await getSalesSnapshot({ date: '2000-01-01' })).code, 'SNAPSHOT_NOT_READY');
    assert.equal((await readStockSnapshot({ ...params, start_date: '2000-01-01', end_date: '2000-01-02' })).code, 'SNAPSHOT_NOT_READY');
    assert.equal(spy.mock.callCount(), 0);
  } finally { mock.restoreAll(); await fs.rm(directory, { recursive: true, force: true }); delete process.env.INVENTORY_SNAPSHOT_DIR; }
});

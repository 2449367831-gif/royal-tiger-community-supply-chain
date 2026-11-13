const { test, mock } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { Writable } = require('node:stream');
const { salesNeedsRefresh, salesRefreshReady } = require('../daily_sales_refresh_policy');

test('daily sales source follows the Shanghai calendar, independently of stock snapshot time', () => {
  const payload = { ok: true, sales_queried_at: '2026-11-11T09:40:00+08:00', queried_at: '2026-11-12T01:00:00+08:00' };
  assert.equal(salesNeedsRefresh(payload, Date.parse('2026-11-11T23:59:59+08:00')), false);
  assert.equal(salesNeedsRefresh(payload, Date.parse('2026-11-12T00:00:00+08:00')), true);
  assert.equal(salesNeedsRefresh(null), true);
  assert.equal(salesNeedsRefresh({ ok: true, queried_at: 'bad' }), true);
  assert.equal(salesRefreshReady(Date.parse('2026-11-12T09:29:59+08:00')), false);
  assert.equal(salesRefreshReady(Date.parse('2026-11-12T09:30:00+08:00')), true);
  assert.equal(salesNeedsRefresh({ ok: true, sales_queried_at: '2026-11-12T08:33:00+08:00' }, Date.parse('2026-11-12T09:30:00+08:00')), true);
});

test('full sales reads run once per day; later, concurrent and manual updates only refresh cloud stock', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'sales-split-refresh-'));
  process.env.INVENTORY_SNAPSHOT_DIR = directory;
  let current = Date.parse('2026-11-11T09:00:00+08:00');
  mock.method(Date, 'now', () => current);
  let failStock = false;
  const executions = [];
  const spy = mock.method(require('node:child_process'), 'execFile', (python, args, options, callback) => {
    const script = path.basename(args[2]);
    const chunks = [];
    const stdin = new Writable({ write(chunk, encoding, done) { chunks.push(chunk); done(); } });
    stdin.on('finish', () => {
      const input = JSON.parse(Buffer.concat(chunks).toString());
      executions.push({ script, input, env: options.env });
      const timestamp = new Date(current).toISOString();
      const payload = script === 'query_daily_sales_web.py' ? {
        ok: true, date: timestamp.slice(0, 10), queried_at: timestamp,
        alerts: { transit_status: 'available', transit_queried_at: timestamp, stock_basis: 'rolling-adjusted-with-transit-v1', stock_status: 'available' },
        start_date: '2026-11-04', end_date: '2026-11-10', unmapped: [],
        rows: [{ sku_code: 'S', estimated_sales: 100, reference_sales: 30, valid_days: 7, cloud_stock: 80, alert_state: 'warning' }],
      } : failStock === true ? { ok: false } : {
        ...input, queried_at: timestamp,
        alerts: { transit_status: 'available', transit_queried_at: timestamp, stock_basis: 'rolling-adjusted-with-transit-v1', stock_status: failStock === 'empty' ? 'failed' : 'available' },
        rows: input.rows.map(row => ({ ...row, cloud_stock: 150, alert_state: 'tight' })),
      };
      callback(null, JSON.stringify(payload));
    });
    return { stdin };
  });
  const { snapshotStore } = require('../dashboard_snapshot_store');
  const { refreshDailySalesSnapshot, getSalesSnapshot } = require('../daily_sales_api');
  try {
    // Old deployments have queried_at but no split timestamps. Upgrade safely.
    await snapshotStore.save('sales', { latest: true }, { ok: true, date: '2026-11-10', queried_at: '2026-11-10T08:40:00+08:00',
      alerts: { transit_status: 'available' }, rows: [{ estimated_sales: 10 }] });
    await refreshDailySalesSnapshot();
    current = Date.parse('2026-11-11T09:29:59+08:00');
    await refreshDailySalesSnapshot();
    assert.ok(executions.every(item => item.script === 'refresh_daily_sales_inventory.py'));
    executions.length = 0;
    current = Date.parse('2026-11-11T09:30:00+08:00');
    const daily = await Promise.all(Array.from({ length: 10 }, () => refreshDailySalesSnapshot()));
    assert.equal(executions.length, 1);
    assert.equal(executions[0].script, 'query_daily_sales_web.py');
    const sourceTime = daily[0].sales_queried_at;
    current = Date.parse('2026-11-11T13:00:00+08:00');
    const updates = await Promise.all(Array.from({ length: 10 }, () => refreshDailySalesSnapshot()));
    assert.equal(executions.length, 2);
    assert.equal(executions[1].script, 'refresh_daily_sales_inventory.py');
    assert.ok(!Object.keys(executions[1].env).some(key => /^PG[A-Z_]*$/.test(key)));
    assert.ok(updates.every(result => result.sales_queried_at === sourceTime && result.rows[0].estimated_sales === 100 && result.rows[0].reference_sales === 30));
    assert.equal(updates[0].rows[0].cloud_stock, 150);
    assert.equal(updates[0].rows[0].alert_state, 'tight');
    assert.equal(updates[0].inventory_queried_at, new Date(current).toISOString());
    assert.equal(updates[0].refresh_mode, 'stock-only');
    assert.equal((await getSalesSnapshot({ date: '2026-11-11' })).rows[0].cloud_stock, 150);
    const before = await getSalesSnapshot({});
    failStock = true;
    current = Date.parse('2026-11-11T17:00:00+08:00');
    const failed = await refreshDailySalesSnapshot({}, { inventoryOnly: true });
    assert.equal(failed.snapshot.id, before.snapshot.id);
    assert.equal(failed.sales_queried_at, sourceTime);
    assert.ok(failed.snapshot.last_error);
    failStock = 'empty';
    const empty = await refreshDailySalesSnapshot({}, { inventoryOnly: true });
    assert.equal(empty.snapshot.id, before.snapshot.id); // An empty live source must not replace valid stock.
    assert.ok(empty.snapshot.last_error);
    failStock = false;
    current = Date.parse('2026-11-12T08:40:00+08:00');
    await refreshDailySalesSnapshot();
    assert.equal(executions.filter(row => row.script === 'query_daily_sales_web.py').length, 1);
    current = Date.parse('2026-11-12T09:30:00+08:00');
    await refreshDailySalesSnapshot();
    assert.equal(executions.filter(row => row.script === 'query_daily_sales_web.py').length, 2);
    const count = spy.mock.callCount();
    await getSalesSnapshot({});
    assert.equal(spy.mock.callCount(), count); // Page reads remain disk-only.
  } finally {
    mock.restoreAll();
    await fs.rm(directory, { recursive: true, force: true });
    delete process.env.INVENTORY_SNAPSHOT_DIR;
  }
});

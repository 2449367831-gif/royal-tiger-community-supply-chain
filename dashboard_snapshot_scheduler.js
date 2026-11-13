const { snapshotStore } = require('./dashboard_snapshot_store');
const { nextRefreshTime } = require('./refresh-schedule');
const { salesNeedsRefresh, salesRefreshReady } = require('./daily_sales_refresh_policy');
const HOUR = 3600000, DAY = 24 * HOUR, OFFSET = 8 * HOUR;
function latestRefreshTime(now) {
  const day = Math.floor((now + OFFSET) / DAY) * DAY - OFFSET;
  for (const hour of [21,17,13,9]) if (day + hour * HOUR <= now) return day + hour * HOUR;
  return day - DAY + 21 * HOUR;
}
function startDashboardSnapshots() {
  console.log(`[dashboard-snapshot] directory=${snapshotStore.directory}`);
  const { refreshInventorySnapshot, fullStockParameters } = require('./inventory_api');
  const { refreshDailySalesSnapshot } = require('./daily_sales_api');
  let running = false;
  let timer;
  async function check() {
    if (running) return;
    const current = Date.now();
    const hour = Math.floor(((current + OFFSET) % DAY) / HOUR);
    if (hour < 9 || hour > 21) return;
    running = true;
    const keys = [snapshotStore.key('sales', { latest: true }), snapshotStore.key('stock', fullStockParameters())];
    keys.forEach(key => snapshotStore.queued.add(key));
    try {
      const due = latestRefreshTime(current);
      const sales = await snapshotStore.read('sales', { latest: true });
      const stock = await snapshotStore.read('stock', fullStockParameters());
      if (salesRefreshReady(current) && salesNeedsRefresh(sales, current)) {
        // n8n refreshes sales after its 09:30 mapping run. The 09:00 stock
        // slot must not read sales before mapping; later slots can catch up.
        const result = await refreshDailySalesSnapshot();
        console.log(`[dashboard-snapshot] daily sales ${result.ok && !result.snapshot.last_error ? 'saved' : 'retained/missing'}`);
      } else if (sales && Date.parse(sales.inventory_queried_at || sales.snapshot.generated_at) < due) {
        const result = await refreshDailySalesSnapshot({}, { inventoryOnly: true });
        console.log(`[dashboard-snapshot] cloud stock ${result.ok && !result.snapshot.last_error ? 'saved' : 'retained/missing'}`);
      }
      if (!stock || Date.parse(stock.snapshot.generated_at) < due) {
        const result = await refreshInventorySnapshot();
        console.log(`[dashboard-snapshot] stock ${result.ok && !result.snapshot.last_error ? 'saved' : 'retained/missing'}`);
      }
    } catch { console.error('[dashboard-snapshot] Update incomplete; last successful snapshots retained.'); }
    finally { keys.forEach(key => snapshotStore.queued.delete(key)); running = false; }
  }
  function arm() {
    clearTimeout(timer);
    timer = setTimeout(() => { arm(); void check(); }, Math.max(1, nextRefreshTime(Date.now()) - Date.now()));
    timer.unref();
  }
  arm();
  void check(); // Catch up a missed daytime slot after a restart, once.
  return { stop: () => clearTimeout(timer) };
}
module.exports = { startDashboardSnapshots, latestRefreshTime };

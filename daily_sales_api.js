const path = require('path');
const { execFile } = require('child_process');
const { parseParameters } = require('./inventory_api');
const { snapshotStore } = require('./dashboard_snapshot_store');
const { salesNeedsRefresh, salesRefreshReady, refreshPolicy, withRefreshMetadata } = require('./daily_sales_refresh_policy');
let active = 0;
const refreshJobs = new Map();

function queryDailySales(request, response, sendJson) {
  let parameters;
  let force;
  try {
    const search = new URL(request.url, 'http://localhost').searchParams;
    for (const key of search.keys()) {
      if (!['date', 'refresh'].includes(key) || search.getAll(key).length !== 1) throw new Error('invalid');
    }
    if (search.has('refresh') && search.get('refresh') !== '1') throw new Error('invalid');
    parameters = {};
    if (search.has('date')) parameters.date = parseParameters(`/api/inventory?stock_date=${encodeURIComponent(search.get('date'))}`).stock_date;
    force = search.get('refresh') === '1';
  } catch {
    sendJson(response, 400, { ok: false, error: '请选择有效的业务日期，不能晚于今天。' });
    return;
  }
  getSalesSnapshot(parameters, force).then(payload => {
    if (!response.destroyed && !response.writableEnded) sendJson(response, payload.ok ? 200 : 503, payload);
  }).catch(() => {
    if (!response.destroyed && !response.writableEnded) sendJson(response, 503, { ok: false, error: '销量快照暂不可用，请稍后重试。' });
  });
}

const salesKey = parameters => parameters.date ? { date: parameters.date } : { latest: true };
const chinaToday = () => new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
async function refreshDailySalesSnapshot(parameters = {}, { inventoryOnly = false } = {}) {
  // Coalesce the date-alias save as well as the source query. Otherwise each
  // concurrent caller rewrites the same alias after the store lock releases.
  const jobKey = snapshotStore.key('sales', salesKey(parameters));
  if (refreshJobs.has(jobKey)) return refreshJobs.get(jobKey);
  const job = performDailySalesRefresh(parameters, inventoryOnly).finally(() => refreshJobs.delete(jobKey));
  refreshJobs.set(jobKey, job);
  return job;
}
async function performDailySalesRefresh(parameters, inventoryOnly) {
  const key = salesKey(parameters);
  const result = await snapshotStore.refresh('sales', key, previous => {
    const waitingForMapping = (!parameters.date || parameters.date === chinaToday()) && !salesRefreshReady();
    if (previous && (inventoryOnly || waitingForMapping || !salesNeedsRefresh(previous))) return executeDailySales(parameters, previous);
    if (inventoryOnly || waitingForMapping) return { ok: false, code: 'SNAPSHOT_NOT_READY' };
    return executeDailySales(parameters);
  },
    (payload, previous) =>
      !(previous?.alerts?.transit_status === 'available' && payload.alerts?.transit_status !== 'available') &&
      !(payload.alerts?.stock_basis === 'rolling-adjusted-with-transit-v1' && payload.alerts.stock_status !== 'available'));
  if (result.ok && !parameters.date && !result.snapshot.last_error) {
    await snapshotStore.save('sales', { date: result.date }, { ...result, forecast_freshness: 'selected' }, result.snapshot.generated_at);
  }
  return result;
}
async function getSalesSnapshot(parameters, force = false) {
  const key = salesKey(parameters);
  let payload = force ? await refreshDailySalesSnapshot(parameters) : await snapshotStore.read('sales', key) || snapshotStore.missing('sales', key);
  if (payload.ok) payload = { ...payload, refresh_policy: refreshPolicy() };
  if (!payload.ok || parameters.date) return payload;
  const today = chinaToday();
  const yesterday = new Date(Date.parse(today + 'T00:00:00Z') - 86400000).toISOString().slice(0, 10);
  const freshness = payload.date === today ? 'today' : payload.date === yesterday ? 'yesterday' : 'outdated_snapshot';
  return { ...payload, today, forecast_freshness: freshness,
    rows: freshness === 'outdated_snapshot' ? payload.rows.map(row => ({ ...row, estimated_sales: null,
      alert_state: 'unknown', alert_reasons: [], alert_missing: ['快照早于昨日，请更新数据'] })) : payload.rows };
}
function executeDailySales(parameters, previous = null) {
  return new Promise(resolve => {
    if (active >= 2) { resolve({ ok: false, code: 'BUSY', error: '销量查询繁忙，请稍后重试。' }); return; }
    active += 1;
    const python = process.env.INVENTORY_PYTHON || (process.platform === 'win32' ? 'py' : 'python3');
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => /^(PG[A-Z_]*$|DORIS_|PICKUP_DB_|PYTHON|PATH$|PATHEXT$|SYSTEMROOT$|WINDIR$|TEMP$|TMP$|USERPROFILE$|LOCALAPPDATA$|APPDATA$)/i.test(key)));
    if (previous) for (const key of Object.keys(env)) if (/^PG[A-Z_]*$/i.test(key)) delete env[key];
    const script = previous ? 'refresh_daily_sales_inventory.py' : 'query_daily_sales_web.py';
    const child = execFile(python, ['-X', 'utf8', path.join(__dirname, 'scripts', script)], {
      cwd: __dirname, windowsHide: true, timeout: 45000, maxBuffer: 16 * 1024 * 1024,
      encoding: 'utf8', env: { ...env, PYTHONIOENCODING: 'utf-8' },
    }, (error, stdout) => {
      active -= 1;
      try { resolve(withRefreshMetadata(JSON.parse(stdout), previous)); }
      catch { resolve({ ok: false, code: error?.killed ? 'TIMEOUT' : 'QUERY_FAILED', error: '销量查询失败，请稍后刷新重试。' }); }
    });
    child.stdin.on('error', () => {});
    child.stdin.end(JSON.stringify(previous || parameters));
  });
}
module.exports = { queryDailySales, refreshDailySalesSnapshot, getSalesSnapshot };

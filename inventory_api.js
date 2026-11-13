const path = require("path");
const { execFile } = require("child_process");
const { InventoryQueryCache } = require("./inventory_query_cache");
const queryCache = new InventoryQueryCache();
const { snapshotStore } = require("./dashboard_snapshot_store");

let activeQueries = 0;
const MAX_QUERIES = 2;

const chinaDay = now => new Date(now.getTime() + 8 * 60 * 60 * 1000).toISOString().slice(0, 10);
function validDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.getUTCFullYear() > 0 && date.toISOString().slice(0, 10) === value;
}

function parseParameters(url, now = new Date()) {
  const search = new URL(url, "http://localhost").searchParams;
  const allowed = new Set(["sku", "warehouse", "start_date", "end_date", "stock_date"]);
  for (const key of search.keys()) {
    if (!allowed.has(key)) throw new Error("不支持的查询参数。");
  }
  function codes(key) {
    const values = [...new Set(search.getAll(key).map(value => value.trim()).filter(Boolean))];
    if (values.length > 100 || values.some(value => value.length > 100 || /[\u0000-\u001f]/u.test(value))) {
      throw new Error("仓库或 SKU 参数过长，单次最多查询 100 个编码。");
    }
    return values;
  }
  for (const key of ["start_date", "end_date", "stock_date"]) {
    if (search.getAll(key).length > 1) throw new Error("日期参数不能重复。");
  }
  const start = search.get("start_date") || "";
  const end = search.get("end_date") || "";
  if (Boolean(start) !== Boolean(end)) throw new Error("开始日期和结束日期需同时填写或同时留空。");
  if (start && (!validDate(start) || !validDate(end) || start > end)) {
    throw new Error("请填写有效的日期范围，开始日期不能晚于结束日期。");
  }
  const result = { sku_codes: codes("sku"), warehouse_codes: codes("warehouse"), start_date: start, end_date: end };
  if (search.has("stock_date")) {
    const stockDate = search.get("stock_date");
    if (!validDate(stockDate) || stockDate > chinaDay(now)) throw new Error("请选择有效的库存日期，不能晚于今天。");
    result.stock_date = stockDate;
  }
  return result;
}

function parseMovementParameters(url, now = new Date()) {
  const search = new URL(url, "http://localhost").searchParams;
  for (const key of search.keys()) {
    if (!["sku", "warehouse", "days", "stock_date", "snapshot_id"].includes(key) || search.getAll(key).length !== 1) throw new Error("出入库详情仅支持单个仓库和商品。");
  }
  // The UI defaults to the latest 120 calendar days. Keep 90-day links
  // readable for bookmarks created before the default changed.
  const daysText = search.get("days") || "120";
  if (!["7", "30", "90", "120"].includes(daysText)) throw new Error("请选择近 7、30 或 120 天。");
  const codes = new URLSearchParams({ sku: search.get("sku") || "", warehouse: search.get("warehouse") || "" });
  if (search.has("stock_date")) codes.set("stock_date", search.get("stock_date"));
  const filters = parseParameters(`/api/inventory?${codes}`, now);
  if (filters.sku_codes.length !== 1 || filters.warehouse_codes.length !== 1) throw new Error("请指定仓库编号和商品编号。");
  const localDay = filters.stock_date || chinaDay(now);
  const start = new Date(`${localDay}T00:00:00Z`);
  start.setUTCDate(start.getUTCDate() - Number(daysText) + 1);
  const result = { warehouse_code: filters.warehouse_codes[0], sku_code: filters.sku_codes[0], start_date: start.toISOString().slice(0, 10), end_date: localDay };
  if (search.has('snapshot_id')) {
    if (!/^[a-f0-9]{64}$/.test(search.get('snapshot_id'))) throw new Error('快照版本无效。');
    result.snapshot_id = search.get('snapshot_id');
  }
  return result;
}

function selectStockRows(payload, parameters) {
  // The live SQL groups and calculates each warehouse/SKU independently. A
  // complete result for the same period can safely supply exact code filters.
  const warehouses = new Set(parameters.warehouse_codes);
  const skus = new Set(parameters.sku_codes);
  const rows = payload.rows.filter(row => (!warehouses.size || warehouses.has(row["仓库编码"])) && (!skus.size || skus.has(row["SKU编码"])));
  return { ...payload, rows, row_count: rows.length, parameters: { ...parameters, sku_codes: parameters.sku_codes.length ? parameters.sku_codes : [""], warehouse_codes: parameters.warehouse_codes.length ? parameters.warehouse_codes : [""] } };
}

function queryInventory(request, response, sendJson) {
  let parameters;
  let force;
  const movements = new URL(request.url, "http://localhost").pathname === "/api/inventory/movements";
  try {
    const url = new URL(request.url, "http://localhost");
    if (url.searchParams.has("refresh") && (url.searchParams.getAll("refresh").length !== 1 || url.searchParams.get("refresh") !== "1")) throw new Error("刷新参数无效。");
    force = url.searchParams.get("refresh") === "1";
    url.searchParams.delete("refresh");
    parameters = movements ? parseMovementParameters(url.href) : parseParameters(url.href);
    if (!movements) {
      parameters.stock_date ||= chinaDay(new Date());
      parameters.sku_codes.sort();
      parameters.warehouse_codes.sort();
    }
  } catch (error) {
    sendJson(response, 400, { ok: false, code: "invalid_parameters", error: error.message });
    return;
  }
  const work = movements ? readMovementSnapshot(parameters, force) : readStockSnapshot(parameters, force);
  work.then(payload => {
    if (response.destroyed || response.writableEnded) return;
    const status = payload.ok ? 200 : payload.code === 'STOCK_NOT_FOUND' ? 404 : payload.code === 'TOO_MANY_ROWS' ? 422 : 503;
    sendJson(response, status, payload);
  }).catch(() => {
    if (!response.destroyed && !response.writableEnded) sendJson(response, 503, { ok: false, error: '库存快照暂不可用，请稍后重试。' });
  });
}

function fullStockParameters(parameters = {}) {
  return { sku_codes: [], warehouse_codes: [], start_date: parameters.start_date || '', end_date: parameters.end_date || '', stock_date: parameters.stock_date || chinaDay(new Date()) };
}
async function refreshInventorySnapshot(parameters = {}) {
  const full = fullStockParameters(parameters);
  if (full.stock_date < chinaDay(new Date())) return executeQuery(full, false, null);
  return snapshotStore.refresh('stock', full, () => executeQuery(full, false, null, true), payload => payload.rows.length > 0 && /^[a-f0-9]{32}$/.test(payload.snapshot_batch || ''));
}
async function readStockSnapshot(parameters, force = false) {
  const full = fullStockParameters(parameters);
  // Historical inventory always reads the immutable local archive, never the
  // live database. The Python adapter does not rebuild past adjusted stock.
  const payload = full.stock_date < chinaDay(new Date()) ? await executeQuery(full, false, null)
    : force ? await refreshInventorySnapshot(full) : await snapshotStore.read('stock', full) || snapshotStore.missing('stock', full);
  return payload.ok ? selectStockRows(payload, parameters) : payload;
}
async function readMovementSnapshot(parameters, force = false) {
  const { snapshot_id: version, ...sourceParameters } = parameters;
  if (parameters.end_date < chinaDay(new Date())) return executeQuery(sourceParameters, true, null);
  const stockParameters = { ...fullStockParameters({ stock_date: parameters.end_date }), sku_codes: [parameters.sku_code], warehouse_codes: [parameters.warehouse_code] };
  const pinned = !force && version ? await snapshotStore.read('stock', fullStockParameters(stockParameters), version) : null;
  const stock = !force && version ? pinned ? selectStockRows(pinned, stockParameters) : snapshotStore.missing('stock', fullStockParameters(stockParameters)) : await readStockSnapshot(stockParameters, force);
  if (!stock.ok) return stock;
  if (stock.rows.length !== 1) return { ok: false, code: 'STOCK_NOT_FOUND', error: '当前快照中没有该仓库商品。' };
  if (!/^[a-f0-9]{32}$/.test(stock.snapshot_batch || '')) return snapshotStore.missing('stock', fullStockParameters(stockParameters));
  const baseline = { ...stock, _archive_directory: path.join(snapshotStore.directory, 'batches', stock.snapshot_batch) };
  const payload = await queryCache.get('movements', { ...sourceParameters, batch: stock.snapshot_batch }, () => executeQuery(sourceParameters, true, baseline));
  return { ...payload, snapshot: stock.snapshot };
}

function executeQuery(parameters, movements, baseline, buildSnapshot = false) {
  if (activeQueries >= MAX_QUERIES) return Promise.resolve({ ok: false, code: "busy", error: "库存查询繁忙，请稍后重试。" });
  activeQueries += 1;
  return new Promise(resolve => {
  const python = process.env.INVENTORY_PYTHON || (process.platform === "win32" ? "py" : "python3");
  const childEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) => /^(DORIS_|PICKUP_DB_|PYTHON|PATH$|PATHEXT$|SYSTEMROOT$|WINDIR$|TEMP$|TMP$|USERPROFILE$|LOCALAPPDATA$|APPDATA$)/i.test(key)));
  const script = buildSnapshot ? "update_inventory_dashboard_snapshot.py" : movements ? "query_inventory_movements.py" : "query_inventory_web.py";
  const child = execFile(python, ["-X", "utf8", path.join(__dirname, "scripts", script)], {
    cwd: __dirname,
    env: { ...childEnv, INVENTORY_SNAPSHOT_DIR: snapshotStore.directory, PYTHONIOENCODING: "utf-8" },
    windowsHide: true,
    timeout: buildSnapshot ? 300000 : 150000,
    maxBuffer: 64 * 1024 * 1024,
    encoding: "utf8",
  }, (error, stdout) => {
    activeQueries -= 1;
    let payload;
    try { payload = JSON.parse(stdout); } catch { /* Runtime failures have no JSON body. */ }
    if (payload?.ok === true && !error && Array.isArray(payload.rows)) {
      resolve(payload);
    } else if (payload?.ok === false) {
      resolve(payload);
    } else {
      resolve({
        ok: false,
        code: error?.killed ? "query_timeout" : "query_unavailable",
        error: error?.killed ? "库存查询超时，请缩小仓库或 SKU 范围后重试。" : "库存查询服务不可用，请检查服务器 Python 和数仓连接配置后重试。",
      });
    }
  });
  child.stdin.on("error", () => {});
  child.stdin.end(JSON.stringify(baseline ? { parameters, stock_result: baseline } : parameters));
  // A disconnected visitor must not kill work shared by other visitors.
  });
}

module.exports = { parseParameters, parseMovementParameters, queryInventory, selectStockRows, refreshInventorySnapshot, readStockSnapshot, readMovementSnapshot, fullStockParameters };

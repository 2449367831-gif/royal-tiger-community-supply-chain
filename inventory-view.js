(() => {
  "use strict";
  const get = id => document.getElementById(id);
  const columns = ["仓库编码", "仓库名称", "SKU编码", "SKU名称", "当日库存", "库存变动时间"];
  const columnLabels = ["仓库编号", "仓库名称", "商品编号", "商品名称", "库存数量", "修改时间"];
  const numberFormat = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 20 });
  const number = value => value == null ? "—" : numberFormat.format(value);
  const html = value => String(value ?? "—").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const splitCodes = value => [...new Set(value.split(/[,，;；\s]+/u).filter(Boolean))];
  const chinaToday = () => new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString().slice(0, 10);
  const freezeLabel = payload => payload.corrected_at ? ` · 更正于 ${new Date(payload.corrected_at).toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" })}` : payload.frozen_at ? ` · ${payload.freeze_kind === "backfilled" ? "补建固定于" : "固定于"} ${new Date(payload.frozen_at).toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" })}` : "";
  function resetDate() {
    get("inventoryDate").max = chinaToday();
    get("inventoryDate").value = chinaToday();
  }
  let rows = [];
  let page = 1;
  let snapshot = null;
  let loading = false;
  let controller = null;
  let requestVersion = 0;
  let liveState = "尚未查询";
  let snapshotPoll;
  let snapshotPollCount = 0;
  let detailRow = null;
  let detailStockDate = null;
  let detailSnapshotId = null;
  let detailController = null;
  let detailVersion = 0;
  let movementData = null;
  let movementPage = 1;

  function updateHeading() {
    get("lastSync").textContent = snapshot ? new Date(snapshot.queried_at).toLocaleString("zh-CN", { hour12: false }) : liveState;
    if (!['#daily-sales', '#future-sales'].includes(location.hash)) {
      document.querySelector(".sync-label").textContent = liveState;
      document.querySelector(".sidebar-footer").textContent = `库存查询：${liveState}`;
    }
  }
  window.addEventListener('hashchange', () => {
    if (!['#daily-sales', '#future-sales'].includes(location.hash)) {
      updateHeading();
      if (!snapshot && !loading) query();
    }
  });

  function renderPage() {
    const size = Number(get("inventoryPageSize").value);
    const start = (page - 1) * size;
    const currentRows = rows.slice(start, start + size);
    get("inventoryBody").innerHTML = currentRows.length ? currentRows.map((row, index) => `<tr>
      <td class="inventory-code">${html(row["仓库编码"])}</td><td>${html(row["仓库名称"])}</td>
      <td class="inventory-code">${html(row["SKU编码"])}</td><td><strong>${html(row["SKU名称"])}</strong></td>
      <td class="numeric inventory-highlight">${number(row["当日库存"])}</td>
      <td>${html(row["库存变动时间"])}</td><td><button class="row-action" type="button" data-inventory-detail="${start + index}" ${row["当日库存"] == null ? 'disabled title="旧存档未保存完整当日库存，无法计算详情结存"' : ''} aria-label="查看 ${html(row["仓库名称"])} ${html(row["SKU名称"])} 库存详情">详情</button></td>
    </tr>`).join("") : `<tr><td colspan="7" class="inventory-empty">${loading ? "正在查询库存…" : snapshot ? "没有符合条件的库存" : "暂无库存查询结果"}</td></tr>`;
    get("inventoryPageSummary").textContent = rows.length ? `${start + 1}–${Math.min(start + size, rows.length)} / ${number(rows.length)}` : "0 / 0";
    get("inventoryPrev").disabled = loading || page <= 1;
    get("inventoryNext").disabled = loading || start + size >= rows.length;
    document.querySelector(".inventory-table-shell").scrollTop = 0;
  }

  function renderCount() {
    get("inventoryCount").textContent = snapshot ? `${number(rows.length)} 个仓库 SKU` : loading ? "正在查询…" : "尚无数据";
  }

  function setBusy(value) {
    loading = value;
    get("inventoryQuery").textContent = value ? "查询中…" : "查询库存";
    get("inventoryQuery").disabled = value;
    get("inventoryExport").disabled = value || !rows.length;
    get("inventoryRefresh").disabled = value;
    get("inventoryPageSize").disabled = value;
    document.querySelector(".inventory-table-shell").setAttribute("aria-busy", String(value));
  }

  function parametersFromForm() {
    const sku = splitCodes(get("inventorySku").value.trim());
    const warehouse = splitCodes(get("inventoryWarehouse").value.trim());
    if ([sku, warehouse].some(values => values.length > 100 || values.some(value => value.length > 100))) throw new Error("仓库和 SKU 各最多查询 100 个编码，每个编码最多 100 个字符。");
    const params = new URLSearchParams();
    const stockDate = get("inventoryDate").value;
    get("inventoryDate").max = chinaToday();
    if (!stockDate || !get("inventoryDate").validity.valid) throw new Error("请选择有效的库存日期，不能晚于今天。");
    params.set("stock_date", stockDate);
    sku.forEach(value => params.append("sku", value));
    warehouse.forEach(value => params.append("warehouse", value));
    return params;
  }

  async function query(force = false, quiet = false) {
    clearTimeout(snapshotPoll);
    if (!quiet) snapshotPollCount = 0;
    get("inventoryError").hidden = true;
    let parameters;
    try { parameters = parametersFromForm(); if (force === true) parameters.set("refresh", "1"); }
    catch (error) { get("inventoryError").textContent = error.message; get("inventoryError").hidden = false; return; }
    controller?.abort();
    controller = new AbortController();
    const version = ++requestVersion;
    const stockDate = parameters.get("stock_date");
    get("inventoryTitle").textContent = stockDate === chinaToday() ? "当前库存" : "历史库存";
    get("inventoryBasis").textContent = `${stockDate} · ${stockDate === chinaToday() ? "实时在库－待出＋待入" : "固定当日库存＝实时库存－待出库＋待入库"}`;
    if (!quiet && !force) { snapshot = null; rows = []; }
    page = 1;
    liveState = "正在查询";
    setBusy(true);
    renderCount();
    renderPage();
    updateHeading();
    get("inventoryStatus").textContent = force ? "正在更新服务器快照…" : "正在读取库存快照…";
    try {
      const response = await fetch(`/api/inventory?${parameters}`, { signal: controller.signal, cache: "no-store" });
      const payload = await response.json();
      if (payload.snapshot?.updating && snapshotPollCount++ < 120) snapshotPoll = setTimeout(() => query(false, true), 3000);
      if (!response.ok || payload.ok !== true) {
        const message = payload.code === "SNAPSHOT_NOT_READY" ? payload.error : response.status === 401 ? "登录已失效，请重新登录后查询。" : response.status === 429 ? "库存查询繁忙，请稍后重试。" : response.status === 400 ? "查询条件无效，请检查仓库、SKU 和库存日期。" : response.status === 504 ? "库存查询超时，请缩小查询范围后重试。" : "数仓查询失败，请检查服务器连接配置后重新查询。";
        throw new Error(message);
      }
      if (!Array.isArray(payload.rows) || !payload.parameters || !payload.queried_at || payload.rows.some(row => columns.some(key => !(key in row)))) throw new Error("库存接口返回格式不完整，请检查查询服务。");
      if (version !== requestVersion) return;
      snapshot = payload;
      rows = [...payload.rows].sort((a, b) => String(a["仓库名称"] ?? "").localeCompare(String(b["仓库名称"] ?? ""), "zh-CN") || String(a["SKU编码"]).localeCompare(String(b["SKU编码"])));
      liveState = "查询成功";
      const p = payload.parameters;
      const warehouses = p.warehouse_codes.filter(Boolean);
      const skus = p.sku_codes.filter(Boolean);
      get("inventoryBasis").textContent = `${payload.stock_date} · ${payload.stock_note}${freezeLabel(payload)}${payload.snapshot?.persisted ? " · 读取服务器快照" : ""}${payload.snapshot?.updating ? " · 正在后台更新，当前为上次成功数据" : ""}${!rows.length && payload.stock_basis === "daily_snapshot" ? " · 所选日期及条件下无快照数据" : ""}`;
      if (payload.snapshot?.last_error) { get("inventoryError").textContent = payload.snapshot.last_error; get("inventoryError").hidden = false; }
      get("inventoryStatus").textContent = `${payload.stock_date} · ${warehouses.length ? `${warehouses.length} 个仓库` : "全部仓库"} · ${skus.length ? `${skus.length} 个商品` : "全部商品"}`;
      const warehouseOptions = new Map(rows.map(row => [row["仓库编码"], row["仓库名称"]]));
      if (!warehouses.length && !skus.length) get("inventoryWarehouses").innerHTML = [...warehouseOptions].map(([code, name]) => `<option value="${html(code)}">${html(name)}</option>`).join("");
    } catch (error) {
      if (version !== requestVersion || error.name === "AbortError") return;
      liveState = "查询失败";
      get("inventoryError").textContent = error instanceof TypeError ? "无法连接库存服务，请确认服务已启动后重新查询。" : error.message;
      get("inventoryError").hidden = false;
      get("inventoryStatus").textContent = "未取得库存数据";
    } finally {
      if (version === requestVersion) { setBusy(false); renderCount(); renderPage(); updateHeading(); }
    }
  }

  get("inventoryFilters").addEventListener("submit", event => { event.preventDefault(); query(); });
  get("inventoryRefresh").addEventListener("click", () => query(true));
  get("inventoryFilters").addEventListener("reset", () => { setTimeout(() => { resetDate(); query(); }, 0); });
  get("inventoryDate").addEventListener("change", () => query());
  get("inventoryWarehouse").addEventListener("change", () => query());
  get("inventoryPageSize").addEventListener("change", () => { page = 1; renderPage(); });
  get("inventoryPrev").addEventListener("click", () => { if (page > 1) { page -= 1; renderPage(); } });
  get("inventoryNext").addEventListener("click", () => { if (page * Number(get("inventoryPageSize").value) < rows.length) { page += 1; renderPage(); } });
  get("inventoryBody").addEventListener("click", event => {
    const trigger = event.target.closest("[data-inventory-detail]");
    if (!trigger) return;
    detailRow = rows[Number(trigger.dataset.inventoryDetail)];
    detailStockDate = snapshot.stock_date;
    detailSnapshotId = snapshot.snapshot?.id || null;
    get("inventoryDetailBody").innerHTML = `<div class="movement-heading"><div><h3>${html(detailRow["SKU名称"])}</h3><div class="inventory-code">${html(detailRow["SKU编码"])} · ${html(detailRow["仓库名称"])} / ${html(detailRow["仓库编码"])} · 库存日期 ${html(detailStockDate)}</div></div><div class="movement-actions"><label class="sr-only" for="movementDays">截至所选库存日期的出入库日期范围</label><select id="movementDays"><option value="7">近 7 天</option><option value="30">近 30 天</option><option value="120" selected>近 120 天</option></select><button id="movementRefresh" class="secondary-button" type="button">刷新</button></div></div>
      <div class="movement-totals" aria-label="云仓库存变动计算"><div><span>期初结存 · 倒推</span><strong id="movementOpening">—</strong></div><b aria-hidden="true">＋</b><div><span>入库</span><strong id="movementIn" class="is-positive">—</strong></div><b aria-hidden="true">−</b><div><span>出库</span><strong id="movementOut" class="is-negative">—</strong></div><b aria-hidden="true">＝</b><div><span>${snapshot.stock_basis === "daily_snapshot" ? "所选日期当日库存" : "当前库存"}</span><strong id="movementStock">—</strong></div></div>
      <details class="movement-explanation"><summary>结存为倒推值，非原始结存日志 · 按计划/业务时间倒序<span>查看计算口径</span></summary><p id="movementNote" class="inventory-detail-note">按当前可见业务记录倒推结存，非原始结存日志。</p></details><div id="movementError" class="inventory-error" role="alert" hidden></div>
      <div class="movement-table-shell" tabindex="0" aria-label="云仓库存变动明细表"><table class="movement-table"><thead><tr><th scope="col">计划/业务时间</th><th scope="col">类型</th><th scope="col">来源</th><th scope="col" class="numeric">变动数量</th><th scope="col" class="numeric">变动前结存<small>倒推</small></th><th scope="col" class="numeric">变动后结存<small>倒推</small></th><th scope="col">来源单号</th></tr></thead><tbody id="movementBody"></tbody></table></div>
      <div class="movement-footer"><span id="movementStatus" role="status">正在查询…</span><div class="pagination"><button id="movementExport" type="button" class="secondary-button" disabled>导出明细</button><span id="movementPageLabel">0 / 0</span><button id="movementPrev" type="button" class="page-button" aria-label="明细上一页" disabled>‹</button><button id="movementNext" type="button" class="page-button" aria-label="明细下一页" disabled>›</button></div></div>`;
    get("movementDays").addEventListener("change", loadMovements);
    get("movementRefresh").addEventListener("click", () => loadMovements(true));
    get("movementPrev").addEventListener("click", () => { movementPage -= 1; renderMovements(); });
    get("movementNext").addEventListener("click", () => { movementPage += 1; renderMovements(); });
    get("movementExport").addEventListener("click", exportMovements);
    get("inventoryDetail").showModal();
    loadMovements();
  });
  function renderMovements() {
    const entries = [...(movementData?.rows || [])].reverse();
    const offset = (movementPage - 1) * 25;
    get("movementBody").innerHTML = entries.length ? entries.slice(offset, offset + 25).map(row => `<tr><td>${html(row.time)}</td><td><span class="movement-type ${row.direction === "in" ? "is-positive" : "is-negative"}">${row.direction === "in" ? "入库" : "出库"}</span></td><td>${html(row.source_label || row.source_kind)}</td><td class="numeric ${row.signed_quantity >= 0 ? "is-positive" : "is-negative"}">${row.signed_quantity >= 0 ? "+" : ""}${number(row.signed_quantity)}</td><td class="numeric">${number(row.before_stock)}</td><td class="numeric movement-balance">${number(row.after_stock)}</td><td class="inventory-code">${html(row.reference)}</td></tr>`).join("") : `<tr><td colspan="7" class="inventory-empty">${movementData ? "该期间没有库存变动记录" : "暂无库存变动查询结果"}</td></tr>`;
    get("movementPageLabel").textContent = entries.length ? `${offset + 1}–${Math.min(offset + 25, entries.length)} / ${entries.length}` : "0 / 0";
    get("movementPrev").disabled = movementPage <= 1;
    get("movementNext").disabled = offset + 25 >= entries.length;
    get("movementExport").disabled = !entries.length;
    document.querySelector(".movement-table-shell").scrollTop = 0;
  }

  async function loadMovements(force = false) {
    detailController?.abort();
    detailController = new AbortController();
    const version = ++detailVersion;
    const days = get("movementDays").value;
    movementData = null;
    movementPage = 1;
    renderMovements();
    get("movementError").hidden = true;
    get("movementRefresh").disabled = true;
    for (const id of ["movementOpening", "movementIn", "movementOut", "movementStock"]) get(id).textContent = "—";
    get("movementBody").innerHTML = '<tr><td colspan="7" class="inventory-empty">正在查询云仓库存变动…</td></tr>';
    get("movementStatus").textContent = "正在查询…";
    document.querySelector(".movement-table-shell").setAttribute("aria-busy", "true");
    try {
      const params = new URLSearchParams({ warehouse: detailRow["仓库编码"], sku: detailRow["SKU编码"], days, stock_date: detailStockDate });
      if (force === true) params.set("refresh", "1");
      else if (detailSnapshotId) params.set("snapshot_id", detailSnapshotId);
      const response = await fetch(`/api/inventory/movements?${params}`, { signal: detailController.signal, cache: "no-store" });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(response.status === 429 ? "查询繁忙，请稍后刷新。" : payload.code === "TOO_MANY_ROWS" ? "明细数量过多，请选择更短的日期范围。" : "库存变动查询失败，请重新刷新。");
      if (!Array.isArray(payload.rows) || payload.current_stock == null || payload.rows.some(row => !["in", "out"].includes(row.direction) || !Number.isFinite(row.signed_quantity) || !Number.isFinite(row.after_stock) || !row.source_kind || !row.source_label)) throw new Error("库存变动明细数据格式不完整。");
      if (version !== detailVersion) return;
      movementData = payload;
      if (payload.snapshot?.id) detailSnapshotId = payload.snapshot.id;
      if (force === true) void query(false, true);
      get("movementOpening").textContent = number(payload.opening_stock);
      get("movementIn").textContent = number(payload.total_in);
      get("movementOut").textContent = number(payload.total_out);
      get("movementStock").textContent = number(payload.current_stock);
      get("movementNote").textContent = (payload.note || "按当前可见业务记录倒推结存，非原始结存日志。") + freezeLabel(payload);
      get("movementStatus").textContent = `${payload.start_date || payload.parameters.start_date} 至 ${payload.end_date || payload.parameters.end_date} · 共 ${payload.rows.length} 笔${payload.cache?.hit ? " · 复用近期查询" : ""}`;
    } catch (error) {
      if (version !== detailVersion || error.name === "AbortError") return;
      get("movementError").textContent = error instanceof TypeError ? "无法连接库存变动服务。" : error.message;
      get("movementError").hidden = false;
      get("movementStatus").textContent = "未取得库存变动明细";
    } finally {
      if (version === detailVersion && get("inventoryDetail").open) {
        get("movementRefresh").disabled = false;
        document.querySelector(".movement-table-shell").setAttribute("aria-busy", "false");
        renderMovements();
      }
    }
  }

  function exportMovements() {
    if (!movementData) return;
    const headers = ["计划/业务时间", "类型", "来源", "变动数量", "变动前结存(倒推)", "变动后结存(倒推)", "来源单号"];
    const values = movementData.rows.map(row => [row.time, row.direction === "in" ? "入库" : "出库", row.source_label || row.source_kind, row.signed_quantity, row.before_stock, row.after_stock, row.reference]);
    downloadCsv(headers, values, `云仓库存变动_${detailStockDate}_${detailRow["仓库编码"]}_${detailRow["SKU编码"]}.csv`);
  }

  get("inventoryClose").addEventListener("click", () => get("inventoryDetail").close());
  get("inventoryDetail").addEventListener("close", () => { detailVersion += 1; detailController?.abort(); });
  get("inventoryDetail").addEventListener("click", event => {
    if (event.target !== get("inventoryDetail")) return;
    const box = get("inventoryDetail").getBoundingClientRect();
    if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) get("inventoryDetail").close();
  });
  get("inventoryExport").addEventListener("click", () => {
    if (!snapshot || !rows.length) return;
    const values = rows.map(row => [snapshot.stock_date, snapshot.stock_note, snapshot.frozen_at || "", row["库存更正时间"] || "", snapshot.freeze_kind === "dashboard" ? "看板快照" : snapshot.freeze_kind === "backfilled" ? "补建存档" : snapshot.frozen_at ? "凌晨存档" : "实时", ...columns.map(key => ["仓库编码", "SKU编码"].includes(key) ? `'${row[key]}` : row[key])]);
    downloadCsv(["库存日期", "库存口径", "固定时间", "更正时间", "存档类型", ...columnLabels], values, `仓库SKU库存_${snapshot.stock_date}.csv`);
    get("inventoryStatus").textContent = `已导出当前查询的 ${number(rows.length)} 条库存记录`;
  });

  function downloadCsv(headers, rows, filename) {
    const cell = value => {
      let text = value == null ? "" : String(value);
      if (typeof value === "string" && /^[=+\-@\t\r]/u.test(text)) text = `'${text}`;
      return `"${text.replaceAll('"', '""')}"`;
    };
    const csv = [headers, ...rows].map(row => row.map(cell).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  get("mobileMenu").addEventListener("click", () => { const opened = document.body.classList.toggle("nav-open"); get("mobileMenu").setAttribute("aria-expanded", String(opened)); });
  window.inventoryRefreshSchedule.subscribe(() => { if (!loading && (snapshot || !['#daily-sales', '#future-sales'].includes(location.hash))) query(false, true); });
  resetDate();
  if (!['#daily-sales', '#future-sales'].includes(location.hash)) query();
})();

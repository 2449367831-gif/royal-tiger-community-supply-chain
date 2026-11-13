const records = [
  { time: "2026-09-11 10:42:18", warehouse: "多多买菜-新济南云仓", sku: "5001010011120600", name: "皇家小虎脆皮烤肠（原味）0600G12", qty: 37, reason: "2026-09-10 添加调拨单-退货", stock: 5383, operator: "yuanyuan", source: "调拨单", low: false },
  { time: "2026-09-11 10:40:03", warehouse: "多多买菜-新济南云仓", sku: "5001010011120600", name: "皇家小虎脆皮烤肠（原味）0600G12", qty: 38, reason: "2026-09-10 添加调拨单-退货", stock: 5346, operator: "yuanyuan", source: "调拨单", low: false },
  { time: "2026-09-11 10:37:45", warehouse: "多多买菜-新济南云仓", sku: "5001010011120600", name: "皇家小虎脆皮烤肠（原味）0600G12", qty: -135, reason: "2026-09-10 添加调拨单", stock: 5308, operator: "yuanyuan", source: "销售出库", low: false },
  { time: "2026-09-11 10:31:21", warehouse: "多多买菜-新济南云仓", sku: "5001010011120600", name: "皇家小虎脆皮烤肠（原味）0600G12", qty: -180, reason: "2026-09-10 添加调拨单", stock: 5443, operator: "yuanyuan", source: "销售出库", low: false },
  { time: "2026-09-11 09:58:02", warehouse: "华北中心仓", sku: "5001020083000750", name: "皇家小虎香煎大鸡排（黑椒味）0750G00", qty: -4, reason: "盘点差异修正", stock: 214, operator: "linjie", source: "库存调整", low: true },
  { time: "2026-09-11 09:45:34", warehouse: "多多买菜-新济南云仓", sku: "5001010011120600", name: "皇家小虎脆皮烤肠（原味）0600G12", qty: 112, reason: "2026-09-09 添加调拨单-退货", stock: 5627, operator: "yuanyuan", source: "调拨单", low: false },
  { time: "2026-09-10 17:22:11", warehouse: "华东中心仓", sku: "5001030045001200", name: "皇家小虎台式香肠 1200G00", qty: -86, reason: "门店订单出库", stock: 92, operator: "zhangqi", source: "销售出库", low: true },
  { time: "2026-09-10 16:08:49", warehouse: "华北中心仓", sku: "5001040099000800", name: "皇家小虎黑椒鸡块 0800G00", qty: 246, reason: "采购入库单", stock: 1860, operator: "linjie", source: "采购入库", low: false }
];
const ids = ["recordsBody", "resultCount", "totalChange", "totalStock", "pageSummary", "metricSku", "metricIn", "metricOut", "metricAlert", "skuInput", "warehouseSelect", "startDate", "endDate", "changeType", "lowOnly", "filters", "toggleFilters", "exportButton", "toast", "drawer", "drawerBackdrop", "drawerBody", "closeDrawer", "mobileMenu"];
const els = Object.fromEntries(ids.map(id => [id, document.getElementById(id)]));
let visibleRecords = [...records];
let lastTrigger = null;
const fmt = value => Number(value).toLocaleString("zh-CN");
const escapeHtml = value => String(value).replace(/[&<>"']/g, char => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[char]));
const changeType = record => record.source === "库存调整" ? "adjust" : record.qty > 0 ? "in" : "out";
const typeLabel = record => changeType(record) === "adjust" ? "库存调整" : record.qty > 0 ? "入库" : "出库";
function renderMetrics(rows) {
  const skuCount = new Set(rows.map(row => `${row.warehouse}|${row.sku}`)).size;
  const inbound = rows.filter(row => row.qty > 0).reduce((sum, row) => sum + row.qty, 0);
  const outbound = rows.filter(row => row.qty < 0).reduce((sum, row) => sum + Math.abs(row.qty), 0);
  const lowCount = new Set(rows.filter(row => row.low).map(row => `${row.warehouse}|${row.sku}`)).size;
  els.metricSku.textContent = fmt(skuCount); els.metricIn.textContent = fmt(inbound); els.metricOut.textContent = fmt(outbound); els.metricAlert.textContent = fmt(lowCount);
}

function renderTable(rows) {
  renderMetrics(rows);
  els.recordsBody.innerHTML = rows.length ? rows.map(record => `<tr data-index="${records.indexOf(record)}" class="${record.low ? "is-low" : ""}"><td class="time-cell">${record.time}</td><td title="${escapeHtml(record.warehouse)}">${escapeHtml(record.warehouse)}</td><td class="code-cell">${record.sku}</td><td class="name-cell" title="${escapeHtml(record.name)}">${escapeHtml(record.name)}</td><td class="numeric change ${record.qty > 0 ? "is-positive" : "is-negative"}"><span>${record.qty > 0 ? "+" : ""}${fmt(record.qty)}</span><small>${typeLabel(record)}</small></td><td class="reason-cell" title="${escapeHtml(record.reason)}">${escapeHtml(record.reason)}</td><td class="numeric stock ${record.low ? "stock-low" : ""}">${fmt(record.stock)}${record.low ? '<span class="low-tag">低</span>' : ""}</td><td>${record.operator}</td><td><span class="source-tag">${record.source}</span></td><td><button class="row-action" type="button" data-detail="${records.indexOf(record)}">查看详情</button></td></tr>`).join("") : '<tr class="empty-row"><td colspan="10">没有符合条件的记录</td></tr>';
  const changeTotal = rows.reduce((sum, row) => sum + row.qty, 0);
  els.totalChange.textContent = `${changeTotal >= 0 ? "+" : ""}${fmt(changeTotal)}`;
  els.totalChange.className = `numeric ${changeTotal >= 0 ? "is-positive" : "is-negative"}`;
  const latestBySku = new Map();
  rows.forEach(row => { const key = `${row.warehouse}|${row.sku}`; if (!latestBySku.has(key) || row.time > latestBySku.get(key).time) latestBySku.set(key, row); });
  els.totalStock.textContent = fmt([...latestBySku.values()].reduce((sum, row) => sum + row.stock, 0));
  els.resultCount.textContent = `共 ${rows.length} 条记录`;
  els.pageSummary.textContent = rows.length ? `1-${rows.length} / ${rows.length}` : "0 / 0";
}
function applyFilters(event) {
  event?.preventDefault();
  const keyword = els.skuInput.value.trim().toLowerCase(); const warehouse = els.warehouseSelect.value; const type = els.changeType.value; const start = els.startDate.value; const end = els.endDate.value;
  visibleRecords = records.filter(record => (!keyword || record.sku.toLowerCase().includes(keyword) || record.name.toLowerCase().includes(keyword)) && (!warehouse || record.warehouse === warehouse) && (!type || changeType(record) === type) && (!start || record.time.slice(0, 10) >= start) && (!end || record.time.slice(0, 10) <= end) && (!els.lowOnly.checked || record.low));
  renderTable(visibleRecords); showToast(`已筛选 ${visibleRecords.length} 条记录`);
}
function resetFilters() { setTimeout(() => { visibleRecords = [...records]; renderTable(visibleRecords); }, 0); }
function showToast(message) { els.toast.textContent = message; els.toast.classList.add("is-visible"); clearTimeout(showToast.timer); showToast.timer = setTimeout(() => els.toast.classList.remove("is-visible"), 1800); }
function openDrawer(record, trigger) { lastTrigger = trigger; const detailType = typeLabel(record); const beforeStock = record.stock - record.qty; els.drawerBody.innerHTML = `<div class="detail-hero"><span class="detail-kicker">${detailType}</span><strong class="detail-qty ${record.qty > 0 ? "is-positive" : "is-negative"}">${record.qty > 0 ? "+" : ""}${fmt(record.qty)}</strong><span>件</span></div><dl class="detail-list"><div><dt>商品名称</dt><dd>${escapeHtml(record.name)}</dd></div><div><dt>商品编号</dt><dd class="mono">${record.sku}</dd></div><div><dt>仓库</dt><dd>${escapeHtml(record.warehouse)}</dd></div><div><dt>变动前库存</dt><dd>${fmt(beforeStock)}</dd></div><div><dt>变动后库存</dt><dd>${fmt(record.stock)} ${record.low ? '<span class="low-tag">低库存</span>' : ""}</dd></div><div><dt>调整原因</dt><dd>${escapeHtml(record.reason)}</dd></div><div><dt>操作时间</dt><dd>${record.time}</dd></div><div><dt>创建人</dt><dd>${record.operator}</dd></div><div><dt>来源单据</dt><dd>${record.source}</dd></div></dl><div class="drawer-note"><span class="status-dot"></span>该记录已写入库存流水，可在原始单据中追溯。</div>`; els.drawer.classList.add("is-open"); els.drawer.setAttribute("aria-hidden", "false"); els.drawer.setAttribute("aria-modal", "true"); els.drawerBackdrop.hidden = false; document.body.classList.add("drawer-open"); els.closeDrawer.focus(); }
function closeDrawer() { if (!els.drawer.classList.contains("is-open")) return; els.drawer.classList.remove("is-open"); els.drawer.setAttribute("aria-hidden", "true"); els.drawer.setAttribute("aria-modal", "false"); els.drawerBackdrop.hidden = true; document.body.classList.remove("drawer-open"); lastTrigger?.focus(); }
els.filters.addEventListener("submit", applyFilters); els.filters.addEventListener("reset", resetFilters); els.lowOnly.addEventListener("change", applyFilters); els.recordsBody.addEventListener("click", event => { const button = event.target.closest("[data-detail]"); if (button) openDrawer(records[Number(button.dataset.detail)], button); });
els.closeDrawer.addEventListener("click", closeDrawer); els.drawerBackdrop.addEventListener("click", closeDrawer); document.addEventListener("keydown", event => { if (event.key === "Escape") closeDrawer(); });
els.toggleFilters.addEventListener("click", () => { const isHidden = els.filters.hidden; els.filters.hidden = !isHidden; els.toggleFilters.setAttribute("aria-expanded", String(isHidden)); }); els.mobileMenu.addEventListener("click", () => { const opened = document.body.classList.toggle("nav-open"); els.mobileMenu.setAttribute("aria-expanded", String(opened)); });
els.exportButton.addEventListener("click", () => { const headers = ["修改时间", "仓库名称", "商品编号", "商品名称", "调整数量", "调整原因", "结存库存", "创建人", "来源"]; const csv = [headers, ...visibleRecords.map(r => [r.time, r.warehouse, r.sku, r.name, r.qty, r.reason, r.stock, r.operator, r.source])].map(row => row.map(value => `"${String(value).replaceAll('"', '""')}"`).join(",")).join("\n"); const link = document.createElement("a"); link.href = URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" })); link.download = "库存变动记录.csv"; link.click(); URL.revokeObjectURL(link.href); showToast("导出文件已生成"); });
renderTable(records);

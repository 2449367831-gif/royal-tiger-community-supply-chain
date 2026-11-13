(() => {
  'use strict';
  const get = id => document.getElementById(id);
  const owners = window.dailySalesOwners;
  get('salesOwner').innerHTML = '<option value="">全部负责人</option>' + owners.names.map(name => `<option value="${name}">${name}</option>`).join('');
  const html = value => String(value ?? '—').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const format = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 });
  const number = value => value == null ? '—' : format.format(value);
  const timestamp = value => value ? new Date(value).toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' }) : '—';
  const today = () => new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
  const rule = row => row.reference_kind === 'average' ? '7天日均' : row.reference_kind === 'peak' ? '不足7天取最高' : '无历史销量';
  const alertLabel = state => ({ warning: '库存不足', tight: '库存紧张', normal: '正常销售' }[state] || '待核验');
  const alertRank = state => ({ warning: 0, tight: 1, normal: 2 }[state] ?? 3);
  let data = null;
  let filtered = [];
  let page = 1;
  let busy = false;
  let controller;
  let version = 0;
  let followLatest = true;
  let snapshotPoll;
  let snapshotPollCount = 0;
  let checkingSnapshot = false;

  function renderRow(row) {
    const rolling = data?.alerts?.stock_basis === 'rolling-adjusted-with-transit-v1';
    const cloudNames = (row.cloud_warehouse_names || []).join('、');
    const shortCloudName = cloudNames.replace(/^多多买菜[-－]/, '') || '云仓待核验';
    const warehouseDetail = `来源平台仓：${row.source_warehouses || '—'}；云仓：${cloudNames || '—'}；编码：${row.cloud_warehouse_code || '—'}`;
    const reasons = [...(row.alert_reasons || []), ...(row.alert_missing || [])];
    const reasonText = row.alert_state === 'warning'
      ? `库存缺口 ${number(row.estimated_sales - row.cloud_stock)}`
      : row.alert_state === 'tight' ? '超过参考销量3倍' : '';
    const detailText = [reasonText, ...(row.alert_missing || [])].filter(Boolean).join('；');
    return `<tr data-alert-state="${html(row.alert_state)}">
      <td class="sales-warehouse" title="${html(warehouseDetail)}"><strong>${html(row.warehouse_name)}</strong><small>${html(shortCloudName)}${row.platform_warehouse_count > 1 ? `<span class="sales-merge-count">${row.platform_warehouse_count}仓合并</span>` : ''}</small></td>
      <td class="sales-product"><strong title="${html(row.product_name)}">${html(row.product_name)}</strong><small class="sales-sku-code" translate="no">${html(row.sku_code)}</small></td>
      <td class="numeric sales-reference"><span class="sales-value">${number(row.cloud_stock)}</span><small>${rolling ? '滚动' : '固定'} ${number(rolling ? row.rolling_stock : row.fixed_stock)} ＋ 在途 ${number(row.transit_stock)}</small></td>
      <td class="numeric sales-estimate" title="预估更新时间：${html(timestamp(row.estimated_updated_at))}"><span class="sales-value">${number(row.estimated_sales)}</span></td>
      <td class="numeric sales-reference"><span class="sales-value">${number(row.reference_sales)}</span><small>${rule(row)} · ${row.valid_days}/7天</small></td>
      <td class="sales-alert-cell" title="${html(reasons.join('；'))}"><span class="sales-alert ${html(row.alert_state)}"><span class="sales-alert-dot" aria-hidden="true"></span>${alertLabel(row.alert_state)}</span>${detailText ? `<small>${html(detailText)}</small>` : ''}</td>
      <td class="sales-owner-cell"><span class="sales-owner${owners.forRow(row) === '待分配' ? ' unassigned' : ''}">${html(owners.forRow(row))}</span></td>
    </tr>`;
  }

  function render() {
    const warehouse = get('salesWarehouse').value.trim();
    const sku = get('salesSku').value.trim().toLocaleLowerCase();
    const alertState = get('salesAlertState').value;
    const owner = get('salesOwner').value;
    filtered = (data?.rows || []).filter(row => (!warehouse || row.warehouse_name.includes(warehouse) || (row.source_warehouses || '').includes(warehouse) || (row.warehouse_id || '').split('、').includes(warehouse) || (row.cloud_warehouse_name || '').includes(warehouse) || (row.cloud_warehouse_codes || []).includes(warehouse)) && (!sku || [row.product_name, row.product_id, row.sku_code].some(value => String(value || '').toLocaleLowerCase().includes(sku))));
    filtered = filtered.filter(row => !alertState || row.alert_state === alertState);
    filtered = filtered.filter(row => !owner || owners.forRow(row).split('、').includes(owner));
    // Red, yellow, green, then unknown; preserve source order inside each group.
    filtered.sort((a, b) => alertRank(a.alert_state) - alertRank(b.alert_state));
    const size = Number(get('salesPageSize').value);
    page = Math.max(1, Math.min(page, Math.ceil(filtered.length / size) || 1));
    const offset = (page - 1) * size;
    get('salesBody').innerHTML = filtered.length ? filtered.slice(offset, offset + size).map(renderRow).join('') : `<tr><td colspan="7" class="inventory-empty">${busy ? '正在读取库存、预估和近7天销售数据…' : data ? '所选日期及筛选条件下暂无销量数据' : '暂无查询结果，请刷新重试'}</td></tr>`;
    get('salesCount').textContent = data ? `${format.format(filtered.length)} 个云仓商品` : busy ? '正在查询…' : '尚未查询';
    get('salesPageLabel').textContent = filtered.length ? `${offset + 1}–${Math.min(offset + size, filtered.length)} / ${filtered.length}` : '0 / 0';
    get('salesPrev').disabled = busy || page <= 1;
    get('salesNext').disabled = busy || offset + size >= filtered.length;
    get('salesExport').disabled = busy || !filtered.length;
    if (data) get('salesStatus').textContent = `${new Set(filtered.map(row => row.warehouse_name)).size} 个云仓组 · 7天完整 ${filtered.filter(row => row.valid_days === 7).length} 项 · 不足7天 ${filtered.filter(row => row.valid_days > 0 && row.valid_days < 7).length} 项 · 库存不足 ${filtered.filter(row => row.alert_state === 'warning').length} 项 · 库存紧张 ${filtered.filter(row => row.alert_state === 'tight').length} 项 · 正常销售 ${filtered.filter(row => row.alert_state === 'normal').length} 项 · 待核验 ${filtered.filter(row => row.alert_state === 'unknown').length} 项${data.snapshot?.persisted ? ' · 读取服务器快照' : ''}`;
    if (location.hash === '#daily-sales') {
      document.querySelector('.sync-label').textContent = busy ? '正在查询…' : data ? '销量已更新' : '查询失败';
      document.querySelector('.sidebar-footer').textContent = data ? `销量日期：${data.date} · 数量为基础量` : busy ? '销量查询：正在查询…' : '销量查询：尚无结果';
    }
    document.querySelector('.sales-table-shell').scrollTop = 0;
  }

  async function load({ force = false, latest = false, quiet = false, prefetched = null } = {}) {
    const savedPage = page;
    const savedScroll = document.querySelector('.sales-table-shell').scrollTop;
    clearTimeout(snapshotPoll);
    if (!quiet) snapshotPollCount = 0;
    controller?.abort();
    controller = new AbortController();
    const current = ++version;
    const params = new URLSearchParams();
    get('salesDate').max = today();
    if (!latest && get('salesDate').value) {
      if (!get('salesDate').validity.valid) return;
      params.set('date', get('salesDate').value);
    }
    if (force) params.set('refresh', '1');
    busy = true;
    if (!quiet && !force) data = null;
    page = 1;
    get('salesRefresh').disabled = true;
    get('salesError').hidden = true;
    get('salesFreshness').hidden = true;
    get('salesFreshness').textContent = '';
    get('salesUnmapped').hidden = true;
    get('salesStatus').textContent = force ? '正在更新服务器快照…' : '正在读取销量快照…';
    document.querySelector('.sales-table-shell').setAttribute('aria-busy', 'true');
    render();
    try {
      const response = prefetched ? null : await fetch(`/api/daily-sales?${params}`, { cache: 'no-store', signal: controller.signal });
      const payload = prefetched || await response.json();
      if (payload.snapshot?.updating && snapshotPollCount++ < 120) snapshotPoll = setTimeout(() => load({ latest: followLatest, quiet: true }), 3000);
      if ((response && !response.ok) || !payload.ok || !Array.isArray(payload.rows)) throw new Error(payload.error || '销量读取失败，请刷新重试。');
      if (current !== version) return;
      data = payload;
      get('salesDate').value = payload.date;
      if (payload.unmapped?.length) {
        get('salesUnmapped').hidden = false;
        get('salesUnmappedSummary').textContent = `${payload.unmapped.length} 项来源记录缺少有效内部SKU映射，未计入结果（点击查看）`;
        get('salesUnmappedBody').innerHTML = payload.unmapped.map(row => `<div>${html(row.source)} · ${html(row.warehouse_name)} · ${html(row.product_id)} · ${html(row.product_name)}</div>`).join('');
      }
      get('salesInventorySchedule').textContent = (payload.refresh_policy?.inventory_hours || [9, 13, 17, 21]).map(hour => `${String(hour).padStart(2, '0')}:00`).join(' / ');
      get('salesDailySchedule').textContent = `每日 ${payload.refresh_policy?.sales_daily_at || '09:30'} 补映射后`;
      const actualTimes = `实际取数时间：库存 ${timestamp(payload.inventory_queried_at || payload.alerts?.transit_queried_at || payload.queried_at)}；销量 ${timestamp(payload.sales_queried_at || payload.queried_at)}。`;
      const rolling = payload.alerts?.stock_basis === 'rolling-adjusted-with-transit-v1';
      const stockCaption = rolling ? `滚动库存 ${payload.alerts.stock_date}` : `历史固定库存 ${payload.alerts?.snapshot_date || '—'}`;
      get('salesStockBasis').textContent = rolling ? '滚动库存＋当前在途' : '昨日固定＋当前在途';
      get('salesBasis').textContent = `${actualTimes} 参考区间 ${payload.start_date} 至 ${payload.end_date}（不含业务日期当天） · ${payload.note} ${payload.alerts?.note || ''} ${stockCaption}。${payload.alerts?.unmapped_warehouses?.length ? ` ${payload.alerts.unmapped_warehouses.length} 个仓库组未配置有效云仓，相关库存显示待核验。` : ''}`;
      const mappingCaption = payload.warehouse_mapping_policy === 'explicit-excel-cloud-code' ? '仓库对应按确认表' : '历史快照仓库口径';
      get('salesBasisSummary').textContent = `${mappingCaption} · 参考区间 ${payload.start_date} 至 ${payload.end_date} · ${stockCaption}${payload.alerts?.unmapped_warehouses?.length ? ` · ${payload.alerts.unmapped_warehouses.length} 个仓库组待匹配` : ''} · 查看计算口径`;
      const noForecast = payload.rows.every(row => row.estimated_sales == null);
      if (payload.forecast_freshness === 'outdated_snapshot') {
        get('salesFreshness').textContent = `现有快照日期为 ${payload.date}，早于昨日，不作为今日预估。请点击“更新数据”生成最新快照。`;
        get('salesFreshness').hidden = false;
      } else if (payload.forecast_freshness === 'yesterday') {
        get('salesFreshness').textContent = `当前使用昨日（${payload.date}）抓取的预估，并非今日数据。预估与7日均销每天09:30补映射完成后更新一次；云仓库存每4小时更新，手动更新沿用当天销量。`;
        get('salesFreshness').hidden = false;
      } else if (payload.forecast_freshness === 'missing') {
        get('salesFreshness').textContent = `今日（${payload.today}）和昨日均暂无有效抓取的预估数据，当前不展示更早日期的预估；表中仅显示前7天销售参考。`;
        get('salesFreshness').hidden = false;
      } else if (payload.date !== payload.today || noForecast) {
        get('salesFreshness').textContent = noForecast ? `所选 ${payload.date} 暂无有效预估销量；表中仅展示此前7天的销售参考。最新预估数据日期为 ${payload.latest_date || '暂无'}。` : `当前为手动选择的 ${payload.date} 历史数据，并非今日数据。`;
        get('salesFreshness').hidden = false;
      }
      if (payload.snapshot?.last_error || payload.snapshot?.updating) { get('salesFreshness').textContent += ` ${payload.snapshot.last_error || '服务器正在更新快照，当前展示上次成功数据。'}`; get('salesFreshness').hidden = false; }
      get('salesWarehouses').innerHTML = [...new Set(payload.rows.map(row => row.warehouse_name))].map(name => `<option value="${html(name)}"></option>`).join('');
    } catch (error) {
      if (current !== version || error.name === 'AbortError') return;
      get('salesError').textContent = error instanceof TypeError ? '无法连接销量服务，请稍后刷新重试。' : error.message;
      get('salesError').hidden = false;
      get('salesStatus').textContent = '未取得销量数据';
    } finally {
      if (current === version) {
        busy = false;
        get('salesRefresh').disabled = false;
        document.querySelector('.sales-table-shell').setAttribute('aria-busy', 'false');
        if (quiet) page = savedPage;
        render();
        if (quiet) document.querySelector('.sales-table-shell').scrollTop = savedScroll;
      }
    }
  }

  // Read persisted snapshots only. n8n refreshes the source once its mapping write commits.
  // Do not repaint unchanged data or interrupt filters, pagination or an active query.
  async function checkLatestSnapshot() {
    if (checkingSnapshot || busy || !data || !followLatest || document.hidden || location.hash !== '#daily-sales') return;
    checkingSnapshot = true;
    const observedVersion = version;
    try {
      const response = await fetch('/api/daily-sales', { cache: 'no-store' });
      const payload = await response.json();
      if (response.ok && payload.ok && payload.snapshot?.id && payload.snapshot.id !== data.snapshot?.id &&
          observedVersion === version && !busy && followLatest && location.hash === '#daily-sales') {
        await load({ latest: true, quiet: true, prefetched: payload });
      }
    } catch { /* Keep the last successful data; manual refresh retains its normal error feedback. */ }
    finally { checkingSnapshot = false; }
  }

  function switchModule() {
    const sales = location.hash === '#daily-sales';
    const future = location.hash === '#future-sales';
    get('dailySales').hidden = !sales;
    get('liveInventory').hidden = sales || future;
    document.querySelector('.breadcrumbs strong').textContent = future ? '未来销量预估' : sales ? '每日预估销量' : '库存变动记录';
    document.querySelectorAll('[data-app-module]').forEach(link => {
      const active = link.dataset.appModule === (future ? 'future-sales' : sales ? 'daily-sales' : 'inventory');
      link.classList.toggle('is-active', active);
      if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
    });
    if (sales && !data && !busy) load({ latest: true });
    else if (sales) render();
  }
  get('salesRefresh').addEventListener('click', () => load({ force: true, latest: followLatest }));
  get('salesFilters').addEventListener('submit', event => { event.preventDefault(); if (!data) load(); else { page = 1; render(); } });
  get('salesFilters').addEventListener('reset', () => setTimeout(() => { followLatest = true; load({ latest: true }); }, 0));
  get('salesDate').addEventListener('change', () => { followLatest = false; load(); });
  for (const id of ['salesWarehouse', 'salesSku']) get(id).addEventListener('input', () => { page = 1; render(); });
  get('salesAlertState').addEventListener('change', () => { page = 1; render(); });
  get('salesOwner').addEventListener('change', () => { page = 1; render(); });
  get('salesPageSize').addEventListener('change', () => { page = 1; render(); });
  get('salesPrev').addEventListener('click', () => { page -= 1; render(); });
  get('salesNext').addEventListener('click', () => { page += 1; render(); });
  get('salesExport').addEventListener('click', () => {
    if (!data || !filtered.length) return;
    const cell = value => { let text = String(value ?? ''); if (/^[=+\-@\t\r]/.test(text) || /^\d{15,}$/.test(text)) text = "'" + text; return '"' + text.replaceAll('"', '""') + '"'; };
    const rolling = data.alerts?.stock_basis === 'rolling-adjusted-with-transit-v1';
    const records = [['业务日期', '参考开始日期', '参考结束日期', '云仓分组', '平台仓库ID', '平台SKU', '内部SKU', '商品名称', '单位', '预估销量', '前7天参考销量', '计算口径', '有效天数', '7天销售合计', '单日最高销量', '预估更新时间', '云仓编号', '云仓名称', '库存基准日期', '基础库存', '采购在途', '调拨在途', '云仓库存含在途', '预警状态', '触发原因', '待核验原因', '基础库存取数或固定时间', '在途查询时间', '来源平台仓', '合并平台仓数', '来源云仓', '库存口径', '实时在库', '待出库', '待入库', '库存最后变动时间', '销量批次取数时间'], ...filtered.map(row => [data.date, data.start_date, data.end_date, row.warehouse_name, row.warehouse_id, row.product_id, row.sku_code, row.product_name, row.unit, row.estimated_sales, row.reference_sales, rule(row), row.valid_days, row.total_sales, row.peak_sales, row.estimated_updated_at, row.cloud_warehouse_code, row.cloud_warehouse_name, rolling ? data.alerts.stock_date : data.alerts?.snapshot_date, rolling ? row.rolling_stock : row.fixed_stock, row.purchase_transit, row.transfer_transit, row.cloud_stock, alertLabel(row.alert_state), (row.alert_reasons || []).join('；'), (row.alert_missing || []).join('；'), rolling ? data.alerts.stock_queried_at : data.alerts?.frozen_at, data.alerts?.transit_queried_at, row.source_warehouses, row.platform_warehouse_count, (row.cloud_warehouse_names || []).join('、'), rolling ? '当前滚动库存＋当前在途' : '历史固定库存＋在途', row.realtime_stock, row.pending_out, row.pending_in, row.stock_changed_at, data.sales_queried_at || data.queried_at])];
    records[0].splice(24, 0, '负责人');
    filtered.forEach((row, index) => records[index + 1].splice(24, 0, owners.forRow(row)));
    const url = URL.createObjectURL(new Blob(['\ufeff' + records.map(row => row.map(cell).join(',')).join('\r\n')], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a'); link.href = url; link.download = `每日预估销量_${data.date}.csv`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  window.addEventListener('hashchange', switchModule);
  window.addEventListener('focus', checkLatestSnapshot);
  document.addEventListener('visibilitychange', checkLatestSnapshot);
  setInterval(checkLatestSnapshot, 30000);
  window.inventoryRefreshSchedule.subscribe(() => { if (!busy && (data || location.hash === '#daily-sales')) load({ latest: followLatest, quiet: true }); });
  switchModule();
})();

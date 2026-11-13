# 仓库 SKU 库存与云仓变动明细

## 页面范围

主表以仓库编码 + SKU 编码为唯一粒度，仅展示仓库编号、仓库名称、商品编号、商品名称、库存数量、修改时间和详情操作。

- 库存数量直接取用户 SQL 的 `当日库存`。
- 修改时间直接取用户 SQL 的 `库存变动时间`。
- 保留原 SQL 全部筛选、空值和数量单位，不追加货主、SKU 分类、箱规或仓库范围。
- 主表仓库、SKU 不填时查询全部。新增“库存日期”，默认中国时区今天，不能选择未来日期；切换日期或仓库自动查询。
- 2026-11-03 业务纠正：今天和凌晨历史存档均使用用户原 SQL 的 `ROUND(实时库存 - 待出库 + 待入库, 0)`，需要保存的是“当日库存”，不是原始在库量。例如 `536 - 219 + 0 = 317`。每日北京时间 03:30 直接执行原实时 SQL，保存所有分项，不把历史在库快照与今天的待出、待入拼接。
- 历史列表、筛选、导出和详情全部读取固定存档，不重新取数或覆盖。新存档标记 `stock_formula=realtime-minus-pending-out-plus-pending-in`；这是凌晨采集时点的当日库存，不是日终结存。
- 历史库存固定时一并保存仓库、SKU 名称和修改时间，之后主数据变化不影响该历史日期。文件保存于当前用户 `%LOCALAPPDATA%\RoyalTigerInventory\history\YYYY-MM-DD.sqlite3`，在网站静态目录之外。
- 没有保存过当日库存的历史日期不能用当前单据状态还原，显示无存档；旧版仅保存原始在库量的记录显示当日库存为 `—`、说明缺少历史待出/待入，并禁用倒推详情，不能继续误称为当日库存。
- 用户提供的 2026-11-01、9477048、XHZSNRJ120g 凭证明确为实时 536、待出 219、待入 0、当日 317，采用独立只写一次的 `2026-11-01.corrections.json` 更正，关联原存档 SHA256，保留原 SQLite 不改；页面及 CSV 显示实际更正时间。该凭证不能用于其他日期或 SKU。
- 原型演示数据文件 `app.js` 保留，但当前页面不再加载演示数据。

## 数据来源

`sql/inventory_web_user.sql` 保留用户原始 SQL，实时查询和后续凌晨存档使用同一模板。`sql/inventory_web_snapshot.sql` 只保留为旧版源表快照查询模板，不再用于新的当日库存存档。

当前库存公式：`ROUND(实时库存 - 待出库 + 待入库, 0)`。库存源为 `scm_warehous_manage`，按仓库和 SKU 汇总；待出、待入取原 SQL 中的调拨申请状态与仓库节点条件。

详情汇总云仓当前可见的库存变动来源：

- 调拨申请：`scm_transfer_order_header` 关联 `scm_transfer_order_detail`，保留两表 `status = 0` 的当前有效单据，数量使用 `SUM(request_qty)`。
- 出向：节点 14 到节点 15，以及原库存 SQL 包含的节点 14 到节点 14 / `WMS000097`，记为扣减。
- 入向：节点 15 到节点 14，记为增加。
- 销售出库：使用 `scm_sales_shipment_allocate_wms.actual_quantity`（回退 `allocate_qty`），按已发货状态汇总。
- 出库时间优先使用计划时间：调拨使用 `scm_transfer_order_header.plan_store_time`，销售使用 `scm_sales_shipment_header.scheduled_ship_on`；计划时间为空时回退实际发货/创建时间。
- 仓库收货入库：使用 `scm_warehous_detail.received_quantity`（回退 `total_quantity`），按实际到货时间汇总。
- 库存调整：使用 `scm_wms_adj_detail.adjust_quantity` 的正负号决定入库或出库。
- 调拨出库与销售出库按来源单号去重，避免同一业务重复计入。
- 详情最近 7、30、120 个中国自然日，默认近 120 天，以主表所选库存日期为结束日；日期范围采用开始日包含、结束日次日不包含。
- 单据当前状态可能为待处理或已完成。详情没有把申请数量当成实际执行数量。

## 倒推结存

数据源没有提供完整的逐次修改、撤销和历史结存日志。详情以原 SQL 当日库存（包含待出、待入）为基准倒推；历史日期使用固定的当日库存或有业务凭证的更正库存，以及同次固定的出入库记录。页面及 CSV 标记“倒推”，不等于真实历史轨迹。旧存档缺少正确当日库存时不生成虚假的结存。

`期初结存(倒推) = 当前库存 - 期间入库 + 期间出库`

将各来源按变动时间、来源单号、方向稳定排序，再逐笔加减。最后一笔倒推结存等于当前库存是计算关系，并非独立对账证据。该结果无法还原已删除、取消或被改量的历史记录，也不等同于数据库原始库存日志。

## 服务

- `GET /api/inventory?warehouse=仓库编码&sku=商品编码&stock_date=YYYY-MM-DD`，两类编码可重复参数多选，每类最多 100 个。省略库存日期读取今天的看板快照，不查询上游。响应增加 `stock_date`、`stock_basis`（`dashboard_snapshot` / `daily_snapshot`）、`stock_note`。
- `GET /api/inventory/movements?warehouse=仓库编码&sku=商品编码&days=120&stock_date=YYYY-MM-DD`，只接受单个仓库、SKU；支持 7/30/120 天并兼容旧请求的 90 天。省略库存日期时默认截至今天；返回 `source_kind/source_label` 标识来源。
- 普通打开、仓库/SKU筛选、翻页只读持久快照；今日库存和120天流水在同一次更新中完整采集。详情7/30/120天窗口均读取该批本地SQLite，不再逐个SKU查询Doris。主表带 `snapshot.id`，详情传 `snapshot_id` 固定到主表批次，旧批次仍可读，避免更新时串批。
- 仅“更新数据”（`refresh=1`）和服务端北京时间09:00、13:00、17:00、21:00任务重新查询上游。服务器生成快照与网页是否打开无关，白天重启补最近漏跑时点一次。历史库存始终只读原03:30档案，即使点击更新也不重算或覆盖。没有今日快照时明确提示手动生成，不自动回源。
- 成功后校验并原子替换最新快照；失败保留上次成功文件并提示更新失败，相同并发更新合并为一次。更新过程中继续读旧数据，网页仅轮询本地结果。快照无TTL过期回源，重启直接恢复；`queried_at` 始终为真实取数时间。
- 看板快照位于 `%LOCALAPPDATA%\RoyalTigerInventory\dashboard-snapshots`，JSON主表和独立 `batches/批次/日期.sqlite3` 流水，均在网站静态目录之外。按版本保留旧批次用于打开中的详情。与 `history/` 不可变03:30存档分开，今日手动更新不会覆盖历史档案。
- 库存 CSV 包含库存日期、库存口径、固定时间、存档类型；详情与主表使用同一日期和固定库存基准。接口返回 `frozen_at`、`freeze_kind`（`scheduled` / `backfilled`）、`archive_sha256` 和 `movements_frozen`。
- 服务器执行固定 SELECT，输入仅作参数绑定，不接受自定义 SQL。
- 复用现有 Node 认证边界，API 返回 `no-store`；凭据留在服务器，不返回浏览器。
- Python 从已有 `D:\取数agent\.env` 读取 `DORIS_*`，显式服务器环境变量可覆盖。不要将凭据复制到网页目录。
- 首次生成失败显示错误；已有快照更新失败保留上次结果，不回退到示例数据；过量明细拒绝，不截断后计算结存。

本地启动（Node.js、Python 和 PyMySQL 已安装）：

```powershell
$env:PICKUP_WEB_HOST = '127.0.0.1'
$env:PICKUP_WEB_PORT = '8791'
npm start
```

推荐使用隐藏启动脚本，避免关闭 PowerShell 窗口时结束服务：

```powershell
cscript.exe //nologo scripts\start_inventory_web_hidden.vbs
```

如果服务曾经通过 `npm start` 或直接执行 `node server.js` 在前台运行，关闭启动它的 PowerShell、注销会话或按下 `Ctrl+C` 都会结束 Node 进程；`tmp\pickup-web-8791.log` 末尾出现 `^C` 时就是这类停止。隐藏启动入口会让 PowerShell 和 Node 都以无窗口方式运行，监督进程会在 Node 异常退出后自动重启。

如需 Windows 启动后自动运行并在 Node 异常退出后拉起，请执行一次：

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts\install_inventory_web_task.ps1
```

安装器注册当前用户登录任务，即使管理员执行也使用当前用户身份，因为数据库账号和密码保存在用户级 Windows 环境变量中，SYSTEM 无法继承。若系统禁止注册任务，则自动写入当前用户 Startup 文件夹。任务/启动入口为 `RoyalTigerInventoryWeb8791`，后台监听 `0.0.0.0:8791`；启动器从当前用户的持久环境补齐缺失的数据库配置，不复制或记录凭据。日志写入 `tmp\pickup-web-8791-supervisor.log`、`tmp\pickup-web-8791.log` 和 `tmp\pickup-web-8791.err.log`。

`GET /health` 是原线下可提货页面的存活检查，其中的缓存行数不代表库存查询结果。验收库存服务需检查 `GET /api/inventory` 返回 `ok=true` 和实际 `row_count`，并验证库存变动详情。

可通过 `INVENTORY_PYTHON` 指定 Python 可执行文件。服务仅在本机运行，不涉及提交、推送或部署。

## 凌晨固定存档

`scripts/install_inventory_history_task.ps1` 安装当前用户的隐藏任务 `RoyalTigerInventoryHistory0330`：北京时间每天 03:30 执行，也在登录时补齐漏跑日期，允许电池供电且请求唤醒。网站 8791 服务启动和每 15 分钟也检查漏跑日期，两条入口使用相同的只写一次逻辑。

机器关机、用户未登录或源表延迟时无法保证准点采集。当日延迟运行会记录实际采集时间及 `backfilled` 标记；跨日遗漏只报告 `unavailable_historical_dates`，不会将今天的计算结果伪装为过去。`schedule-start.json` 记录启用日期。

整日库存在筛选前一次性采集全部仓库 SKU，同时固定 120 天各来源明细；写入临时 SQLite，全部成功后以不覆盖原文件的原子操作发布。失败不发布半成品，重复或并发执行不覆盖已有日期；读取只读连接并核对数据 SHA256。上游依然只有 SELECT，不创建或更新 Doris 表。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/install_inventory_history_task.ps1
py -X utf8 scripts/freeze_inventory_history.py --due
py -X utf8 scripts/freeze_inventory_history.py --date 2026-11-01
```

日志：`tmp/inventory-history.log`；服务补偿日志在 `tmp/pickup-web-8791.log`。存档保留在当前用户本地数据目录，服务重启不清理。

## 验证

```powershell
node --test scripts/test_inventory_api.cjs scripts/test_inventory_query_cache.cjs
py -m unittest scripts/test_inventory_web_query.py scripts/test_query_inventory_movements.py
```

本轮已执行真实只读库存查询：空筛选取得 2,721 个仓库 SKU，逐行库存公式核对无差异。该数量是验证时点结果，并非固定断言。浏览器截图和导出校验产物位于 `output/playwright/`。

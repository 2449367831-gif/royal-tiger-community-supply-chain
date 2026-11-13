# SKU 滚动组合销量预测

> GitHub 整合版：网站位于仓库根目录，引擎位于 `sales_forecasting/`。下方旧绝对路径是原实施记录，安装和启动优先参考根目录 README。

网页入口：http://192.168.51.159:8791/#future-sales 。位于“业务模块 → 未来销量预估”。
预测引擎位于本目录，网页源代码位于 `D:\数仓取数规则`。

## 安装与运行（PowerShell）

从 `D:\社团云仓程序` 执行：

```powershell
python -m venv --system-site-packages .venv
.\.venv\Scripts\python.exe -m pip install -e ./sales_forecasting
.\.venv\Scripts\python.exe -m sales_forecasting.cli make-demo-data --config sales_forecasting/configs/demo.yaml
.\.venv\Scripts\python.exe -m sales_forecasting.cli validate --config sales_forecasting/configs/demo.yaml
.\.venv\Scripts\python.exe -m sales_forecasting.cli run-all --config sales_forecasting/configs/demo.yaml
.\.venv\Scripts\python.exe -m pytest sales_forecasting/tests -q
```

支持 `validate / make-demo-data / backtest / evaluate / forecast / procurement / run-all`。V1 中后五个命令均执行一个完整审计批次，确保不消费与当前输入不一致的历史结果；不适合用这些命令重复做大规模参数搜索。默认正式配置按天回测；demo 为每3天一个起点以缩短验证时间，未来每天均预测。

## 接入正式数据

把文件放在本目录 `data/raw/`，再运行 `run-all --config sales_forecasting/configs/default.yaml`。网页只读成功批次的持久结果，点击“重新读取”不触发训练或数仓全量查询。

`sales_daily.csv`：必填 `date,sku,sales`，推荐 `product_name,warehouse_name,category,stockout,open_flag`。日期必须是已完成的业务日（北京时间今天之前）。`sku` 是序列唯一键；做仓库级预测时，需把“云仓编码+内部SKU”作为唯一序列键，并在 product_name 中保留内部商品信息，禁止把不同仓库同SKU混成一条序列。按用户确认，正式配置改为单SKU初始至少90个已观测日历史（随后使用扩展窗口），演示配置仍为180天；还需额外预测实现期和最终留出期（默认28天），推荐12个月。

`inventory.csv`：`sku,on_hand,allocated,safety_stock,lead_time_days,review_period_days,moq,pack_size`，各字段均须业务确认。库存参数缺失时销量仍预测，采购建议阻断。

`inbound_shipments.csv`：`sku,quantity,expected_arrival_date,status`。有效状态为 `confirmed,in_transit`；仅计需求覆盖期内到货的批次；逾期未处理到货标记人工核验。文件缺失会阻断采购建议；无在途时输入一个包含上述全部字段表头的空文件明确表示无在途。

负销量拒绝导入，需上游明确退货处理规则；不会擅自截为0。重复日期SKU拒绝。缺日补日历但销量保持NaN，真实零、缺货、闭店分别保留；不能用平台无记录推断零需求。异常只标记。预测学习已实现销量，不还原缺货损失。

源可用性检查（只读、有366天窗口、15秒超时）：
```powershell
python scripts/inspect_source.py
```
复用现有项目凭据读取方法，不输出凭据，不修改生产库或映射。2026-11-13检查到 `public.pdd_sales_data` 有151个有数据日期；用户指定90天初始训练门槛。现按今日抓取清单导入全部已批准云仓/SKU组合，含零销量商品；未配置云仓映射的仓库排除，SKU未映射保留待处理状态。来源先按账号+平台仓+平台商品+日期以updated_at/id取最新，使用现有批准的云仓映射及独立SKU映射换算，不修改生产库。映射及源修订记录不是历史版本，当前映射重建的回测不等同于严格的历史时点审计。缺失零销量、部分平台仓抓取、退货仍需业务复核。

## 实现与审计

- SARIMAX(1,0,0)x(1,0,0,7)、加法趋势与周季节 Holt-Winters、全局 LightGBM（原生训练接口）、TSB 统一 `fit_predict` 接口。失败逐模型记录，剩余模型归一化；全部失败输出空值。V1固定模型形式，没有自动选择乘法季节性或SES，避免未经回测选参。
- 每个起点重新拟合扩展窗口；LightGBM 全局训练标签的日期不晚于起点。没有使用实际未来价格、促销、库存。滞后和滚动值只依赖起点及之前。
- 每SKU、每步长从近90个日历日的 `origin<T,target_date<=T` 有效OOF记录优化非负、和为1的MAE权重；MAE按池内平均实际量缩放，lambda对应此归一化尺度。小样本回退到同h开发期OOF先验，先验不足时等权。每周更新并按rho=0.7平滑，保存实际权重及版本。缺模型行按可用集合重新归一化进入目标函数。
- 最终28天留出；配置固定后评估。测试期按预先规定的在线协议允许使用当时已经实现的早期测试结果更新权重/区间，未用未来测试结果调参。这里不是“测试期全程冻结权重”的评估方式。
- 区间使用近90天已实现的组合残差，SKU+h→类别+h→全局+h回退；少于20个则不可用。输出实际coverage、平均宽度和有效区间数；不保证天然95%。
- 采购须先有留出期评估；覆盖期=交期+检查周期，不超过7天；扣已分配库存，仅计有效到货；正采购满足MOQ后按包装倍数上取整。采购量是参考建议，未创建采购订单。预算、容量、供应商最大量尚无输入，不支持这些约束。
- 批次目录 `artifacts/{production|demo}/runs/<run_id>/` 保存基础OOF、组合OOF、权重、未来预测、采购建议、总体/SKU/horizon评估、开发评估、输入hash、配置及清单。成功完成后原子替换latest.json，网页不读取半成品。模型每起点重训，可用输入快照重现；V1未序列化拟合对象。源文件修订会影响回放，需自行保留输入快照，manifest中的hash用来检测变化。
- 使用紧凑模块结构（data/features/models/ensemble/procurement/metrics/pipeline/cli），与指南能力对应；没有为每个函数创建空目录。

## 网页

正式与演示数据隔离，默认正式。支持云仓、SKU、日期筛选；每个云仓/SKU一行，分列展示7日数量。趋势必须由用户通过下拉或行内按钮选择具体云仓/SKU后显示，不画全量汇总曲线；7个点显示整数标签。所有数量显示与CSV导出四舍五入到0位，模型内部不取整。支持分页、模型明细和回测指标表。7日总量始终按SKU/仓库筛选后的整段7天计算，日期筛选只影响明细。整体回测指标不随页面筛选变化。

服务端读取路径默认本目录，可用 `FUTURE_SALES_ROOT` 覆盖。`/api/future-sales?mode=production|demo` 复用现有服务鉴权，严格限制查询参数。未新增公网权限或定时任务。

## 已知限制

演示验证的是可运行性及防泄漏机制，不能证明真实业务准确率。已运行首批真实数据回测；真实采购参数和自动生产刷新尚未配置。真实回测为当前映射重建，并不是历史映射时点的独立审计。当前无促销计划或历史映射版本，未加入这些外生变量。默认lambda固定0.05，已对开发期池化先验做0.001/0.01/0.05/0.1/1/10的时间顺序敏感性诊断（reports/regularization_sensitivity.csv），未据此改变生产配置或用最终测试期调参。小样本SKU可能长期使用先验或无区间，90天为用户指定的正式初始门槛而非准确率保证。没有历史/最新日记录的SKU不会强行生成新产品预测。SARIMAX允许缺失观测由状态空间处理，TSB缺日不更新发生率和数量（与零需求衰减不同），Holt-Winters遇缺日回退；均不填零。

## 修改与恢复

新增网页文件：`future-sales-view.js/css`、`future_sales_api.js`。
修改现有 `index.html`（菜单/容器）、`daily-sales-view.js`（三模块切换）、`inventory-view.js`（未来页不触发初始库存查询）、`server.js`（只读API）。修改前备份位于 `D:\社团云仓程序\backups\20261113-forecast`，恢复这四个文件后按既有启动脚本重启即可移除入口。原日预估销量和库存计算代码未变更。

## 正式数据更新

在项目根目录执行（每次只读数仓并写本模块文件）：
```powershell
python scripts/import_sales_pg.py
.\.venv\Scripts\python.exe -m sales_forecasting.cli validate --config sales_forecasting/configs/default.yaml
.\.venv\Scripts\python.exe -m sales_forecasting.cli forecast-active --config sales_forecasting/configs/default.yaml
```
默认无商品数上限；`--max-series` 仅作局部诊断。当前在售范围按今日抓取清单确定，不把历史商品全集2107误称今日在售。`forecast-active`复用原10组合的不可变OOF审计文件；新增组合使用原开发期OOF先验，不能据此声称完成全量回测。首次使用需已有有效审计批次。完整逐起点回测命令仍为`run-all`，全量运行耗时显著更长。原始历史抽取窗口上限180天用于留出回测；每个回测起点至少90个已观测日，然后逐步扩展。90天权重/残差窗口保持独立。网页显示训练门槛及实际截止日。没有新增自动调度；隔日需重跑上述更新流程。

首批正式留出期：动态组合WMAPE 47.93%，上周同日基准37.94%，FVA -26.31%；区间实际覆盖92.53%。页面明确为试运行，组合尚未证明增益。Holt-Winters因日历缺失退出，当前3个基础模型参与。

## 页面库存关联（2026-11-13）

预测API读取与每日预估销量相同的成功持久快照，按云仓分组名+内部SKU精确关联，重复键不匹配，不把缺失库存当零。首批10/10匹配。新增滚动库存/在途/含在途库存、库存和在途取数时间，CSV包含相关列；库存KPI按云仓SKU去重，不按预测天数重复求和。7日静态缺口=max(0,完整7日预测总量-滚动库存-采购在途-调拨在途)，计入当前全部在途、不按预计到货日期拆分，不等于采购建议；过期预测/非今日库存/上次刷新失败时不算缺口。演示数据不关联真实库存。

库存已可用，采购状态显示“库存已接入 · 待补交期等采购参数”，仍未提供提前期、检查周期、安全库存、MOQ/包装及分批到货日期，不自动生成采购数量。页面库存读取不触发重新训练，不改写已保存回测结果；重新读取使用现有库存快照。库存保持原看板四小时更新计划。

## 全量云仓范围更新（2026-11-13）

正式批次覆盖60个已批准云仓、1831个今日抓取的云仓/SKU组合，222543条历史记录；1469个达到90个有效观测日且具备昨日销量，生成10283条未来7日预测。343个历史不足，19个缺昨日销量，仍保留7日空值行，不填0。库存1831/1831匹配。仓库名称和编码来自现有批准映射，不纳入未配置仓库，不修改生产映射。

原10组合的回测指标只评价原样本；新增1459个可预测组合尚未独立回测，使用开发期OOF先验并逐模型拟合当前预测，区间暂不可用，采购阻断。未来对这些组合完成全量OOF回测后才能声称逐SKU优化权重和独立准确率。今日抓取商品不等同经过商品主数据核验的在售状态，漏抓商品不能由本模块推断出来。

全量LightGBM特征已向量化，含缺日/真零的参考等价与标签截止检查通过；现20项测试通过。修改前备份backups/20261113-forecast-catalog。没有新增自动刷新计划。

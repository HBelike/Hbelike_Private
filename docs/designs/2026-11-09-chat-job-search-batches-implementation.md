# 对话求职批次与自动推荐实现记录

## 目标与技术取舍

实现已批准的[修复方案](../superpowers/specs/2026-11-09-conversational-job-search-repair-design.md)：搜索结果进入独立批次，用户追问固定提交时版本，当前预览与目标绑定分离，搜索并推荐时自动续答，同会话支持返回和比较旧批。

复用 PostgreSQL Turn 队列、原插件 search_jobs/get_job_detail、模型连接、SSE 与上下文预算，没有引入新框架、跨会话记忆或浏览器后台调度。新卡片 version=2 仅承载展示和批次引用，旧卡片真实已存数据可延迟导入。

## 模块与调用链

1. `job_search_sessions.py` 规范化岗位/条件；`JobSearchRepository` 以会话锁、批次锁管理批次、稳定 Sx-Jy 引用、不可覆盖的岗位版本及 request_id 回执。全部已加载岗位可读，JD 最多 30000 字符，过长明确拒绝而不静默截断。
2. `job_search_router.py` 提供创建、目录、分页、详情、预览、完成与分析重试。重复请求复用回执；取消批次同步取消已关联的内部分析。
3. `job-search-session.js` 保存每批写入队列及失败操作；Workspace 的搜索、详情和分页捕获原批次。旧回包不能改变新批归属。按实际引用批次等待保存，失败保留草稿，不因无关批次失败阻断普通聊天。
4. `turn_router → prepare_turn_payload → TurnRepository.enqueue` 在入队事务固定目录、版本与 focus，存入现有 request_metadata_json。Worker 按引用还原对应岗位版本，再交 Intake/Runner；不重新读活动卡片。
5. `job_search_intent/job_search_response` 共享冻结目录；明确搜索优先于泛方向咨询；旧批查看使用 view_batch_id，不重搜。预览不更新目标岗位。
6. `job_search_context` 只为相关任务选择岗位证据，长 JD 编号选择段落并标记完整性；旧 excerpt 永远不标为完整 JD。`job_search_selection` 对较多候选按预算初筛全部基础信息，保留覆盖数与合法引用，支持显式推荐数量；歧义或未保存数据不调用初筛模型。
7. 浏览器收集首屏详情结束或 30 秒后 complete，`job_search_analysis` 固定版本/原资料并幂等排入内部 Turn。入队事务检查取消状态，同时写 analysis_turn_id，避免“取消后又入队”。自动续答没有伪造 user，SSE 和历史均包含来源/父任务/批次，旧任务晚回不覆盖当前 lastTurn 或模型选择。
8. 历史分组保留没有 user 的内部助手消息；新增内部任务不重复计入 30 用户轮次，第 30 轮的自动分析仍可执行。较早批次的分析不覆盖较新用户模型选择。

## 数据与兼容

- 迁移 `20261109_36_job_search_batches.py` 接在 `20261109_35`，新增 `job_search_batches`、`job_search_job_versions`。已仅对 127.0.0.1:54329 执行升级。
- v2 原卡片 PATCH 仅允许 status/selected_job 展示更新，岗位事实通过专用接口保存。既有目标绑定流程保留用户明确确认。
- 旧卡片只存 10/15 条时仅导入真实 10 条，保留原总数和 legacy 标志；旧摘录不能变成完整 JD。恢复历史不定位、不重搜。
- PC 档案列表与详情并排，长 JD 可滚动，连续英文可换行；失败才显示重试保存。历史批次中的所有已存岗位可单独预览。

## 本地验证结果

- Python 定向 **195 passed**，覆盖真实数据库/真实 API/真实 Turn 入队与 Processor、Intake、Prompt、Runner；其中独立批次与自动分析三组本机数据库用例 **21 passed**。仅存在既有 Starlette 测试客户端弃用提示。
- Node 前端全量 **365 passed**。
- Chromium 对话求职两文件 **14 passed**，包括 15 条全保存、第 12 条焦点、已结束的 503 保存失败阻断追问且保留草稿、重试原操作后提交、自动分析不新增用户输入、刷新只读、长文本无横向溢出和既有手机兼容。
- Vite build 成功，保留既有 chunk 大小提示。相关 diff 格式检查通过。
- 已确认空闲后恢复本地 Worker，进程持续存活、启动错误日志为空；前端 5173 与后端 18080 健康请求均为 200。本模块迁移 36 已应用，联调结束全库 head 为 37（含并行模块迁移）。
- 使用本地已配置 `qwen3.6-flash` 对合成简历及合成岗位做一次真实模型抽查：正确引用 S1-J12，读取超过第 2000 字后的“必须博士”要求，核对本科背景并指出硬性差距。估算输入 3982 Token；Provider 上报输入 2490、输出 1795。此次只验证该单岗证据链，不代表所有推荐质量均完成逐项人工验收。
- 合成组织/会话在数据库用例后已清理；本次未使用用户真实简历和 BOSS 账号执行验证，未发送招呼语、投递或部署生产。

收尾补测包括：自动推荐失败的重试入口、刷新 SSE 不重复展示、无效/别名不明批次澄清、所有批次比较、取消不被失败或卡住的保存挡住、取消后旧回包不恢复状态。模型不可用的内部分析记为失败，可配置后显式重试。

关键命令：

```powershell
$env:RUN_JOB_SEARCH_POSTGRES_TESTS='1'
.venv/Scripts/python.exe -m pytest tests/test_job_search_analysis_postgres.py tests/test_job_search_repository_postgres.py tests/test_job_search_sessions.py -q
```

完整 195 项定向命令、Node/浏览器与构建输出已在本次执行记录中核实；浏览器命令为：

```powershell
cd web-ui
npm test
npx playwright test e2e/chat-job-search-flow.spec.js e2e/job-search-card.spec.js --project=chromium --reporter=line
npm run build
```

## 后续边界

实时检索与详情采集仍需要用户电脑上的现有插件及招聘网站登录态。关闭网页不继续操控浏览器；已保存的数据和已入队分析可恢复，未完成采集提供继续入口。JD 失败或证据裁剪会明确区分初筛与已完整核对，不保证搜索快照当前仍在招。没有新增手机实时插件能力或平板适配。

## 2026-11-10 发布前复核与修复

根据 11 月 9 日定向审查重新复现后处理：

- `job-search-session.js` 区分操作回执版本与当前批次版本，当前版本单调前进；晚到批次不覆盖较新状态，显式历史版本查询保持可用。
- `job_search_analysis.py` 将原请求放入内部分析当前任务，保留推荐数量和偏好，仅在原请求没有指定数量时默认最多三个；已保存岗位不足时解释实际范围，不补搜。
- `get_jobs` 在 SQL 中分页并只多取一条判断下一页；冻结目录的数量和预览存在性改为数据库聚合/EXISTS，避免解码全部 JD。普通聊天 Worker 不还原岗位正文，冻结目录仍保留。岗位分析仍完整还原冻结批次以保留公司别名与跨批解析，未在本次继续重构这条分支，也未重跑 32 并发压测。
- 新增浏览器控制器回退、真实 PostgreSQL 分页解码量、冻结目录、原始推荐目标、普通聊天 Worker 回归；独立测试库执行全套 Python 893 项通过。前端 371 项、扩展 99 项通过；Chromium 112 项首轮通过，另外 3 项原测试硬编码端口修正运行环境后补验通过。

本次已获用户明确生产发布授权，随工作区快照 `baa5f320b873` 上线，详见[发布回归记录](../regression-report-2026-11-10-production-release.md)。这不改变真实招聘平台收发尚未验收的边界。

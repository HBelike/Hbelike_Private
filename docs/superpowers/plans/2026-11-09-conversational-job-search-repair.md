# 对话求职完整链路实施计划

**Goal:** 修复七项审查问题，实现自动推荐、同会话多批比较和准确岗位指代。

**Architecture:** 新增搜索批次及不可覆盖的岗位版本；既有 Turn metadata 保存引用快照，Worker 按版本读取。浏览器收集结果后使用现有队列排入内部分析任务，复用 SSE 和模型预算。

**Tech Stack:** Python/FastAPI/SQLAlchemy/PostgreSQL、Vue、Node test、Playwright。

**Spec:** `docs/superpowers/specs/2026-11-09-conversational-job-search-repair-design.md`（用户已批准）。

## 约束

- 中文界面和注释，PC 先验收；只本地实施，不部署生产，不覆盖其他任务改动。
- 独立批次；全文 JD 最大 30000 字符；每页传输最多 50 条；所有已加载岗位可引用。
- 预览不绑定目标；每轮固定引用版本；普通聊天不强制注入 JD。
- 请求自动推荐时，首屏详情结束或 30 秒后提交 complete；幂等排入内部分析，不增加用户轮次。
- 按现有工作区实施；并行任务按文件职责隔离，根任务负责联调与验收，不自动启用 using-superpowers。

## 任务 1：批次、版本与 API

**Files:** 新增 `job_search_sessions.py`、`persistence/job_search_repository.py`、`web/job_search_router.py`、迁移及 `tests/test_job_search_sessions.py`。

**Interfaces:** `JobSearchRepository(database)` 提供 create_batch/list_batches/save_page/save_details/focus/complete/get_jobs/capture_snapshot/resolve_snapshot。所有方法首参数 actor_id、conversation_id；单批方法追加 batch_id。写入返回 `{batch, revision, job_refs}`，目录返回批次数组，岗位读取返回 `{batch,jobs,next_cursor}`。

- [x] 先写并验证批次隔离、重复 request_id、历史 revision、第 12 个岗位和旧卡片导入的失败用例。
- [x] 在会话行锁后锁批次，写入新版本；同内容重试返回原回执。
- [x] 实现接口及真实本地 PostgreSQL 回归；迁移先复核当前 head。

## 任务 2：前端批次控制与保存

**Files:** 新增 `web-ui/src/job-search-session.js` 和测试；修改 Workspace、CareerJobSearchCard、CareerAssistantPage。

**Interfaces:** HTTP `/conversations/{id}/job-search/batches`，页 POST `/{batch}/pages`，详情 POST `/{batch}/details`，focus PATCH `/{batch}/focus`，完成 POST `/{batch}/complete`；每次请求带 request_id。Turn 请求携带 job_search_view：active_batch_id、focused_job_ref、revisions、uncommitted_batch_ids。

- [x] 用 deferred Promise 先复现旧详情、旧分页、已结束保存失败。
- [x] 控制器保存 clean/saving/error 状态和可重试操作；请求捕获批次及条件。
- [x] 存储全部已加载岗位与独立 focus，历史可展开且不重搜；完成后订阅 analysis_turn。
- [x] PC 浏览器验证三种乱序、失败重试、第 12 个岗位与跨批返回。

## 任务 3：入队快照与引用

**Files:** 修改 contracts、turn_payloads、turn_router、turn_job_repository、intake_graph、turn_worker。

**Interfaces:** `CareerInboundMessage.job_search_snapshot` 与 `ModelTurnContext.job_search_snapshot`，内容为 `{schema_version,catalog,active_batch_id,focused_job_ref,uncommitted_batch_ids}`；Worker resolve 后增加 batches 数组，每批 jobs 为该 revision 的岗位。

- [x] 写入队后原卡重搜仍读原版本、附件入口同样冻结的测试。
- [x] 入队事务 capture_snapshot；metadata 序列化/恢复；Worker 按不可变版本展开。
- [x] 校验 focus、revision 与未保存状态，不能用其他批次替代缺失数据。

## 任务 4：意图与预算

**Files:** 修改 job_search_intent、job_search_response、job_search_context、prompt_context 和对应测试。

**Interfaces:** 使用 context.job_search_snapshot 共享目录；分析/比较保留普通模型回复路径，卡片动作兼容原 Runner 协议。JobSearchIntent 可增加 analysis_mode、batch_ids、job_refs、auto_analyze；新卡 version=2。

- [x] 明确新搜优先于“适合我”，公司关键词保留，分析和返回旧批不重搜。
- [x] 借助共享目录解析批次、当前 focus 和岗位编号，验证 16 轮后的继承。
- [x] 按任务和预算组装数据，普通聊天零 JD；候选基础信息超预算时分块而非截前十条。
- [x] 验证 16K 闲聊、JD 尾部条件、同步/流式真实 Prompt 参数。

## 任务 5：自动分析、历史与轮次

**Files:** 新增 job_search_analysis，修改 Worker、Runner、历史分组、计数及前端事件投影。

**Interfaces:** `enqueue_job_search_analysis(services, actor_id, conversation_id, batch, retry_request_id=None)` 返回可订阅的队列状态；source=job_search_auto_analysis，parent_turn_id 和 batch_id 随 metadata/SSE 返回。

- [x] complete 幂等排队，固定当前 revision 和批次创建时资料；重复回调不调用模型两次。
- [x] 内部任务不写伪造 user 消息、不再开搜索卡；历史保留助手续答，进度归原卡。
- [x] 用户计数排除新增内部任务；第 30 轮搜索仍能完成自动分析；最后模型选择不被旧内部任务覆盖。
- [x] 验证刷新、晚返回、队列忙时入队、自动任务失败和明确重试。

## 任务 6：联调验收与文档

- [x] Python 定向集成、Node 全量、PC Playwright 反例及 Vite build。
- [x] 真实本地 PostgreSQL/API 验证，合成会话验收后删除，仅允许本机数据库。
- [x] PC 完成后检查手机已有历史/对话兼容性，不添加平板专项。
- [x] 检查可用本地模型并对合成简历/岗位作真实模型抽查，明确实际完成范围。
- [x] 更新模块说明、设计状态和已验证结果；确认空闲后重启本地 Worker，复核健康接口。

代码步骤的关键契约示例：

```python
snapshot = searches.capture_snapshot(actor_id, conversation_id, view, connection=connection)
metadata["job_search_snapshot"] = snapshot
resolved = searches.resolve_snapshot(actor_id, conversation_id, snapshot)
assert resolved["batches"][0]["revision"] == snapshot["catalog"][0]["revision"]
```

```javascript
const batch = await session.start(sourceMessageId, conditions, analysisRequested)
const captured = { batchId: batch.id, conditions: { ...conditions }, page: 1 }
const result = await bridge.searchJobs(captured.conditions.keyword, captured.page, city)
await session.savePage(captured.batchId, result.jobs, captured.page, result.hasMore)
```

实施过程中每个任务通过后记录实际命令与结果；不把旧实现测试数量当成本次结果。


## 实际完成记录

2026-11-09：本地迁移 36；Python 定向195、Node全量365、Chromium14通过，构建通过；真实配置模型已对合成单岗引用及JD尾部硬条件抽查。详见 docs/designs/2026-11-09-chat-job-search-batches-implementation.md。未部署生产。

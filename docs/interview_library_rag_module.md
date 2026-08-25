# 面经库与 RAG 检索模块

## 目标

面经库是求职助手的长期知识底座：将手工录入、解析后的 PDF / Word / Excel / 图片内容沉淀为可编辑的 Markdown，按“公司 → 岗位与日期”组织，并向求职助手提供可追溯的检索证据。它不保存用户原始附件；长期数据只包含 Markdown、来源元数据、切片与检索反馈。

## 已实现范围

- `GET /api/career/interview-library/tree`：公司—岗位—日期树与关键词过滤。
- `POST /api/career/interview-library/experiences`：手工 Markdown 入库；同一组织、公司与“岗位 + 日期”再次保存时更新原面经、替换切片并重建索引，避免浏览器重试产生重复节点或数据库冲突。
- `POST /api/career/interview-library/import-file`：复用求职助手的受控解析链路导入文件，入库后清理原件。
- `GET /api/career/interview-library/experiences/{id}`、`PUT .../{id}`：预览、编辑、保存并重建索引。
- `GET /api/career/interview-library/mentions`：为后续 `@面经` 候选选择提供轻量检索。
- `GET /api/career/interview-library/search`：返回带公司、岗位、标题路径与来源链接的 RAG 证据片段。
- `POST /api/career/live-interviews/archive/preview`、`POST /api/career/live-interviews/archive`：结束实时面试后聚合当前用户的多个会话，只把识别出的面试问题按“公司 → 岗位 + 日期”归并到面经库；已有正文先读取再追加去重，随后复用本模块的切片与索引链路。
- 求职助手输入框支持 `@面经`：前端只搜索并提交面经 ID；服务端重新执行组织边界校验、按本轮问题召回最多 6 个片段，并将带来源标识的最小证据注入模型上下文。

## 求职助手联动

1. 在求职助手输入框键入 `@` 加公司或岗位关键词，调用 `GET /api/career/interview-library/mentions` 获取候选资料；选择后显示可移除的引用标签。
   `@` 只服务面经候选，Skill 统一通过 `/名称` 唤醒，两类入口不会共享候选菜单或解析规则。
2. 提交文本或附件时，客户端仅传递 `interview_experience_ids`，不传递 Markdown 正文、切片内容或来源凭据。
3. 服务端通过 `_build_interview_evidence` 重新校验当前组织是否拥有这批资料，再通过 `InterviewRetrievalService` 取证据。关键词和向量均未命中时，已显式选择的资料会返回受限数量的结构化切片，避免 `@面经` 因查询措辞差异失效。
4. 模型 Prompt 会明确要求把面经视为经验参考而非官方事实；回答若采用资料观点，应保留形如 `[面经：公司 · 岗位 · 标题]` 的来源标识。
5. 切换会话、归档会话和提交成功都会清理浏览器草稿中的待引用标签；已入库的面经和会话中已持久化的文本不受影响。

## 实时面试归档

1. 浏览器结束面试时先停止音频与 WebSocket，再使用本场累积的全部 `session_ids` 请求问题预览。
2. 服务端同时校验组织和用户归属，从 `live_interview_answers.original_question` 按会话时间与问题版本读取问题；回答失败或取消不影响问题归档。
3. 问题只进行空白、大小写和句末标点规范化去重，不调用 LLM，不保存模型答案、候选人转写或原始音频。
4. 同公司、职位和日期已有面经时，先读取旧问题并追加新问题，再通过 `update_markdown` 重建切片和索引；非实时来源的既有面经只在末尾维护独立的“面试大师归档问题”区块，避免覆盖用户原正文。
5. 来源类型使用现有 `authenticated_session`，来源平台标记为 `interview_master`。重复保存同一批会话不会重复追加问题。

## 数据模型与迁移

Alembic 迁移 `20260809_04_interview_library_rag` 创建以下表：

- `interview_companies`：公司根节点，含规范名称与别名预留。
- `interview_experiences`：一份可编辑的面经 Markdown 与来源元数据。
- `interview_ingestion_jobs`：导入/扫描的可观测任务记录，不存放原文件。
- `interview_chunks`：结构化切片、全文检索字段、可选 `vector(1024)` 向量。
- `interview_source_connections`：为未来官方 API / 已授权会话保存加密凭据引用，不保存明文账号、密码或 Cookie。
- `interview_retrieval_feedback`：为未来召回质量评估、纠错与离线优化保留反馈入口。

PostgreSQL 负责事务、JSON 元数据、全文能力；`pgvector` 位于同一事务边界内，避免主库与独立向量库出现双写不一致。未来数据规模或多租户隔离需求显著提升时，可通过检索适配层平滑新增 Qdrant / Milvus，而无需改动面经正文和 Agent 调用契约。

## RAG 策略

1. 解析层先复用现有 Docling / OCR / Vision / Office 转换链路，输出结构化文本；不把二进制附件写入长期存储。
2. `HierarchicalMarkdownChunker` 按标题层级切片，目标约 260 字符、单片最大 340、重叠 36；每个片段携带公司、岗位、面经名称和标题路径。
3. 基础召回使用 PostgreSQL `pg_trgm` 与 `ILIKE`，检索正文、公司、岗位和面经名称；因此未配置向量模型、模型限流或云端不可用时仍可用。
4. 语义增强层使用 OpenAI-compatible `/embeddings` 协议。只有部署环境显式打开开关、提供模型 ID、HTTPS Base URL 和 Key 时才会调用。
5. 双路召回结果使用 Reciprocal Rank Fusion (RRF) 融合；不直接混用不同 Provider 的相似度数值。当前 RRF 即确定性重排，待有经标注评测集后再接入专用 reranker。
6. 输出包含 `citation`、公司、岗位、日期、标题路径、来源 URL 和片段正文，后续 Agent 回复必须引用这些证据，而不是把整库文本塞入 Prompt。

## 运行配置

配置位于 `config/career_assistant.yaml` 的 `interview_library.retrieval`。

- 默认 `CAREER_INTERVIEW_EMBEDDING_ENABLED=false`，只启用关键词召回。
- 启用向量检索时，部署环境应提供 `CAREER_INTERVIEW_EMBEDDING_MODEL_ID`、`CAREER_INTERVIEW_EMBEDDING_BASE_URL` 与 `DASHSCOPE_API_KEY`（或更新 YAML 指向其他密钥环境变量）。
- 当前数据库索引固定为 1024 维。切换不同维度的 embedding 模型必须先新增迁移和重新索引，禁止仅修改配置。

## 安全与上线边界

- 只接受 HTTPS 的 embedding 服务地址，错误信息不包含密钥与上游响应正文。
- 文件上传只在临时目录存活，并沿用现有大小、90 页 PDF、TTL 与格式限制。
- 自动采集仅能使用官方 API、公开内容或用户合法授权的会话；须遵守目标平台条款、robots、速率限制和数据删除要求。账号/密码/Cookie 未来仅以服务端加密引用管理。
- 建议生产环境使用 PostgreSQL 16 + pgvector、对象临时存储或 `tmpfs`、独立 Docling 服务，并对解析、Embedding、检索延迟与失败率接入日志/指标。

## 验证命令

```powershell
Set-Location D:\MyPro\WechaOffiicialAccount
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe scripts\verify_interview_library.py
.\.venv\Scripts\python.exe scripts\verify_interview_library_api.py
npm --prefix web-ui run build
```

本模块额外回归覆盖：显式选择资料但查询无关键词命中时的兜底召回、面经树过滤、候选搜索、资料编辑重切片与前端生产构建。
其中保存接口还覆盖同一面经的重复提交：返回原有面经 ID，并以最新 Markdown 重建索引。
实时面试归档额外覆盖多会话聚合、失败问题保留、问题去重、已有面经合并、无权会话拒绝、零问题拒绝与成功后按 `experience_id` 打开面经。

## 后续演进

1. 在求职助手输入框实现 `@面经` 候选、确认选择与带引用上下文的对话组装。
2. 新增 URL 采集任务：优先官方接口与用户粘贴 URL 的主体内容提取；登录态采集必须采用加密凭据、最小权限和可撤销机制。
3. 建立离线检索评测集，评估命中率、引用完整性和重排效果，再决定云端 embedding / reranker 供应商。
4. 基于面经证据实现高频考题聚类、语音模拟面试、追问状态机与回答复盘。

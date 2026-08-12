# 面经库公共资料采集模块

更新时间：2026-08-12

## 目标

为面经库提供可追溯的资料进入路径：用户可粘贴公开文章链接生成 Markdown 候选资料，也可按平台和关键词登记采集任务。小红书图文会先经 OCR 与 `InterviewEvidenceAnalyzer` 做事实提取和正文清洗，再由用户确认公司、岗位、正文和标签，最后复用既有面经库入库、切片、向量化和混合检索流程。

## 数据边界

- 持久化采集任务、候选资料的解析后 Markdown、来源 URL、来源平台、状态与错误原因；候选被人工保存后，在元数据中回写 `imported_experience_id`。
- 不持久化上传原件、网页原始 HTML、第三方账号、密码、Cookie 或登录会话。
- 公开 URL 导入只访问用户主动提交的 HTTPS 地址；不执行页面脚本，不携带浏览器会话。
- 小红书关键词任务会转换为公开搜索页 URL，并复用公开 URL 导入链路；只处理无登录请求下页面实际暴露的笔记，未暴露列表时任务明确失败，不伪造结果。
- 牛客、脉脉等尚未具备公开检索实现的平台仍只登记为 `needs_user_interaction`。

## 当前连接器能力

| 场景 | 连接器 | 状态 | 说明 |
| --- | --- | --- | --- |
| 公开 HTTPS 文章 URL | `url_import` | 可用 | 提取正文并转换为候选 Markdown。 |
| 手动粘贴 Markdown | `manual_text` | 可用 | 使用既有面经导入功能。 |
| 文件资料 | `manual_upload` | 可用 | 复用求职助手的 PDF、Word、Excel、图片解析链路。 |
| 小红书关键词搜索 | `url_import` | 可用 | 生成 `/search_result?keyword=...`，再按“发现链接 → 正文/图片解析 → Agent 提取清洗 → 人工确认 → 入库”执行。 |
| 其他平台关键词搜索 | `user_authorized_browser` | 预留 | 仅在获得平台许可、官方 API 或合规用户授权连接器后启用。 |
| 平台官方 API | `official_api` | 预留 | 根据平台条款、授权范围和接口能力接入。 |

## 数据模型与迁移

迁移：`migrations/versions/20260809_05_interview_collection_jobs.py`

- `career_assistant.interview_collection_jobs`：关键词、平台、连接器类型、策略决定、执行状态、可观察错误。
- `career_assistant.interview_collection_candidates`：候选 URL、标题、摘要、解析后 Markdown、内容指纹及候选状态。

候选资料导入后调用 `InterviewLibraryService.ingest`：生成面经档案、RAG 切片与索引，不重复实现文档处理或检索链路。

## API

- `POST /api/career/interview-library/parse-file`：临时解析上传文件并返回“公司、岗位、日期、标签、摘要、Markdown”的可编辑草稿；不写入数据库、不保存原文件。
- `GET /api/career/interview-library/collection-platforms`：获取平台和连接器策略。
- `POST /api/career/interview-library/collection-jobs`：创建关键词采集任务；小红书任务创建后立即交由 `BackgroundTasks` 执行公开搜索页导入，默认不自动写入面经库。
- `GET /api/career/interview-library/collection-jobs/{job_id}`：查询任务与候选资料。
- `POST /api/career/interview-library/collect-url`：读取用户提交的公开 HTTPS URL。
- `POST /api/career/interview-library/collection-candidates/{candidate_id}/select`：标记候选待入库。
- `POST /api/career/interview-library/collection-candidates/{candidate_id}/import`：确认元数据和可编辑的 `markdown_content` 后写入面经库/RAG；人工正文优先于候选原正文。

## 小红书候选审核与人工保存

1. 采集器按公开笔记正文、图片顺序和 OCR 结果生成候选；图片字节只在临时解析期间存在。
2. `InterviewEvidenceAnalyzer` 只从已提取文本中做事实判断和清洗：删除平台标语、公开配图 URL、OCR 分段标签等采集噪音，不编造公司、岗位或题目。
3. 即使 Agent 标记为“需人工核验”或“不建议自动归档”，只要候选保留了正文，页面仍显示可编辑正文与“保存到面经库”入口。该判断只作为审核提示，不阻止用户决定。
4. 新建小红书链接/关键词任务的 `auto_import` 默认为 `false`。候选不会因为模型判定而自动落库；用户确认后才调用候选导入接口。
5. 保存时使用用户编辑后的连续正文，不写入“材料 N”“解析原文”“公开配图”“配图文字识别”等系统包装。来源、公司、岗位、摘要和标签由独立字段保存。
6. 入库成功后候选状态变为 `imported`，候选元数据保存 `imported_experience_id`，用于页面提示与防止重复保存。

## 安全与上线要求

URL 提取器拒绝 HTTP、URL 内嵌用户名密码、非标准端口、本机/私网/保留地址，并限制响应为 HTML、15 秒和 3MB 上限。生产部署时应在受控出网代理或网络策略中再次实施域名/IP 白名单与重定向校验，避免 DNS rebinding 等服务端请求伪造风险。

面向平台的自动采集必须在复核平台协议、官方 API 能力、访问频率、账号授权范围和内容使用权后再启用；不得通过保存用户密码、Cookie 或绕过验证码实现采集。

## 验证与运行

```powershell
# 执行数据库迁移
.\.venv\Scripts\alembic.exe upgrade head

# 离线验证采集策略与安全 URL 校验
.\.venv\Scripts\python.exe scripts\verify_interview_collection.py

# 验证小红书采集、Agent 清洗、人工正文优先与入库 ID 回写
.\.venv\Scripts\python.exe scripts\verify_xiaohongshu_import_orchestration.py

# 验证小红书创建接口默认关闭自动入库
.\.venv\Scripts\python.exe scripts\verify_xiaohongshu_import_api.py

# 真实 PostgreSQL API 契约验证（仅创建并清理一个受限平台关键词任务）
.\.venv\Scripts\python.exe scripts\verify_interview_collection_api.py

# 前端构建
npm --prefix web-ui run build
```

## 文件导入交互

1. 用户仅选择 PDF、Word、Excel 或图片，所有元数据字段均可留空。
2. 后端复用 Docling/OCR 与云端 Vision 的附件解析链路，先得到正文文本；原文件始终只在临时目录内存在。
3. 本地元数据提取器从标题、字段行和问题列表预填公司、岗位、轮次、日期、技术标签与摘要，例如“字节AI Agent开发一面面经（TikTok）”会预填为“字节跳动 / AI Agent开发 / 一面”。
4. 前端展示识别置信度和证据，用户可修改任意字段及 Markdown 正文；只有点击“确认保存并建立索引”后，才会写入面经档案与 RAG 切片。

## 后续演进

1. 接入平台官方 API，或在用户明确授权、符合平台条款的浏览器连接器中实现候选发现。
2. 添加域名级限流、采集队列和失败重试，避免单页读取阻塞 Web 请求。
3. 对候选正文进行重复检测、质量评分和人工选择后再写入 RAG。

# 求职助手模块边界

## 目的

本模块为平台新增一个独立的求职 Agent。它嵌入同一套 WebUI 和未来部署环境，但不修改、调用或依赖现有技能库、GitHub 热门项目工作流、文章排版、图片视频生成和微信公众号草稿推送逻辑。

## 第一阶段边界

- 历史会话、按隐私配置处理的消息、模型档案和 Agent 运行记录存入 PostgreSQL。
- PDF、图片和其他附件仅在一次 Agent Turn 的临时目录中存在；无论成功、失败或超时，都会在清理步骤删除。
- 现阶段不保存原始附件、完整 OCR 文本或可还原的完整简历档案。
- 现阶段不启用本地 Ollama，仅保留将来服务器部署时可配置的 Provider 入口。
- 免费云端模型需要对应平台的免费 API Key。Key 仅通过服务端环境变量读取，不写入 PostgreSQL，也不返回浏览器。

## 长回复策略

- `config/career_assistant.yaml` 的 `response_generation.max_completion_tokens` 统一控制每次模型生成的最大 Token 数；当前默认值为 `4096`，替代了早期 `900` Token 的短回复上限。
- `response_generation.request_timeout_seconds` 当前为 `90` 秒，给长回复、推理模型和网络波动留出足够时间；连接测试仍使用独立的小输出请求，不会浪费正常对话额度。
- 流式回复结束后会按 `privacy.redaction_enabled` 处理，再按 `response_generation.max_persisted_response_characters` 持久化；当前上限为 `30000` 个字符。因此页面实时内容与刷新后历史内容遵循同一上限。
- 该限制只约束单条助手回复，并不限制会话历史的总轮数。后续如接入输出上限较低的免费模型，应在不超过该 Provider 限制的前提下调整 YAML。

## 2026-08-06：纯文本对话输入修复

- 问题：`CareerIntakeGraph._build_redacted_resume_outline` 会在不存在 PDF 简历时仍对空字符串调用脱敏器，触发“待脱敏文本不能为空”，使纯文本对话被错误拦截。
- 修复：只在本轮确实存在可用简历提纲时才脱敏；没有附件、仅输入普通咨询文本时返回空提纲并继续后续模型路由。
- 验证：`scripts/verify_career_intake_graph.py` 覆盖真实 PostgreSQL 的纯文本输入、脱敏、历史写入和临时文件清理链路。

## 2026-08-07：多格式附件与可关闭脱敏

- `AttachmentKind.RESUME_DOCUMENT` 用于区分 Word/Excel 简历与 PDF、图片简历；WebUI 统一显示为“上传材料”。
- `AttachmentParser` 的格式路由是：PDF → 文本探测/按需 Docling；DOCX/XLSX → Docling；DOC/XLS → `GotenbergOfficeConverter` → PDF 解析；JPG/PNG/WebP/BMP/TIFF → Vision。
- `GotenbergOfficeConverter` 是独立、可替换的协议实现，不在 FastAPI 进程调用本机 Office。失败时返回安全状态提示并保留“已收到附件”的事实，避免模型说成“没有接收到简历”。
- `SensitiveDataRedactor` 支持 `enabled` 开关；个人模式关闭后只进行文本规范化，持久化记录会显式携带 `is_redacted=false`。这不会改变附件临时删除策略。
- 本模块只改动 `src/career_assistant`、`config/career_assistant.yaml`、文档处理 Compose 配置及求职助手组件；技能库和公众号工作台保持不变。

## Nanobot 风格的运行时分层

```text
Career WebUI
  -> CareerChannel
  -> AgentLoop
  -> AgentRunner / LangGraph
  -> 受控领域工具 + Model Gateway
  -> PostgreSQL
```

- `CareerChannel` 统一 WebUI 的文字、职位链接和临时附件输入。
- `AgentLoop` 管理会话、上下文、流式输出和最终持久化。
- `AgentRunner` 驱动 LangGraph 节点与工具调用循环。
- `Model Gateway` 根据能力、免费额度和健康状态解析出本轮实际模型。
- 所有模块之间只传递稳定的 contracts，不互相读取对方内部实现。

## 当前新增文件

- `src/career_assistant/contracts.py`：本轮输入、临时附件、模型选择和工作流步骤的稳定契约。
- `src/career_assistant/__init__.py`：模块公共导出。
- `src/career_assistant/document_probe.py`：PDF 文本层质量探测与扫描件路由信号。
- `src/career_assistant/document_parsing.py`：独立 Docling 服务的匿名上传、OCR/版面解析与安全错误适配器。
- `src/career_assistant/resume_normalizer.py`：当前 Turn 内存中的简历章节归类器，将 Markdown/纯文本归纳为工作、项目、教育、技能等稳定区块；不调用 LLM、不写入数据库。
- `src/career_assistant/attachments.py`：当可选的 Docling 服务短暂不可用时，保留本地已提取内容与安全状态提示；无效或加密附件仍严格拒绝。

## 明确不改动的目录

- `src/services/skill_library_service.py`
- `src/providers/wechat_client.py`
- `src/tasks/deliver_task.py`
- `src/tasks/summary_task.py`
- `web-ui/src/App.vue` 中已完成的技能库与审核台功能

## 2026-08-06：会话工具栏与错误反馈

- 会话级操作与输入级操作统一收敛到输入框上方的 `composer-toolbar`：左侧放置职位链接、临时简历、模型选择，右侧放置模型连接与归档会话，避免用户在会话顶部和输入区之间来回寻找操作。
- 错误反馈改为页面中央的非阻塞 Toast：错误出现后默认展示 3 秒，用户也可以随时关闭。Toast 不影响对话历史布局和输入区高度，适合附件校验、模型调用和网络异常等短时反馈。
- 移动端工具栏会自然换行；发送按钮继续固定在输入区底部，确保窄屏下不会遮挡文本输入或附件状态。

## 2026-08-07：会话切换状态隔离

- 浏览器中的草稿文字、职位 URL、正在生成的流式片段和上传文件都属于当前页面的临时状态；切换会话、新建空会话或归档当前会话时必须清空，不能跨会话展示或提交。
- 附件原件按照隐私边界只存在于单次 Agent Turn 的临时目录，任务结束后自动删除；因此历史会话只保留对话文本，不恢复文件本身。
- 每一轮请求的 `requested_selection_mode` 和 `requested_model_profile_id` 已由 `career_assistant.agent_turns` 持久化。读取历史会话时，API 返回该会话最新一轮的选择，WebUI 据此恢复模型下拉框；没有 Turn 或对应档案已不存在时安全回退到“免费额度优先”。
- 前端用递增的会话请求编号丢弃过期响应，防止用户快速点击 A、B 两个会话时较慢的 A 响应反向覆盖 B 的消息或模型选择。

## 2026-08-07：职位链接验证页识别

- `JobPostingExtractor` 继续只读取公开、受限大小的 HTML，保留 SSRF 防护、跳转上限和超时控制。
- 针对 BOSS 直聘，若服务端实际拿到“请稍候”“安全验证”等验证页，不再把该页面误判成职位详情并交给模型分析。
- API 会返回不含原始 URL 和页面正文的 `job_source` 状态与安全提示；WebUI 在本轮反馈中明确提示用户复制 JD 后继续分析。
- 系统不尝试规避登录、Cookie、验证码或反爬机制。部署环境的服务端抓取无法可靠复用用户浏览器登录态，也不应把这种规避行为作为产品能力。

## 2026-08-07：流式回复、重试与部署收口

- WebUI 使用 SSE 接收安全的进度文本、模型增量正文和最终结果；页面不会展示模型内部推理过程，只展示“正在解析材料”“正在组织分析建议”等可解释状态。
- 输入处理、图片理解和文本模型调用均具备受控重试：仅网络、限流和服务端临时错误可在尚未输出正文前重试；鉴权、参数和内容错误不会重试，避免无效消耗额度。
- SSE 心跳、单进程并发闸门、120 秒的预期时长提示，以及数据库中的最新 Turn，使浏览器断线或刷新后能够安全恢复结果。
- 新增 Docker Compose 生产基线：PostgreSQL、迁移任务、FastAPI、Nginx WebUI、Gotenberg 与可选 GPU Docling 服务相互隔离；原始附件使用容器 tmpfs，不随数据库和应用卷持久化。
- 详细部署步骤、资源建议、回滚和验证命令见 [求职助手部署说明](career_assistant_deployment.md)。

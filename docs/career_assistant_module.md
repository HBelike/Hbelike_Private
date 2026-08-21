# 求职助手模块边界

## 2026-08-21：会话视口与职位信息画布

- 目标：进入求职对话后把主要视口留给消息阅读，同时让岗位评分和职位原文保持可查但不常驻堆叠。
- PC 布局：`CareerAssistantPage.vue` 的会话历史支持折叠为窄操作栏，保留“展开历史、开启新对话、会话数”三个入口；折叠状态写入浏览器 `localStorage`，刷新和切换会话后继续沿用。
- 右栏宽度：聊天区与职位信息栏之间提供可拖拽分隔线，允许在 `360px–760px` 范围内调整，默认与双击复位宽度为 `620px`；宽度写入浏览器 `localStorage`。窄窗口会优先保证聊天区最小阅读宽度，手机端不显示拖拽手柄。
- 右栏滚动：PC 端职位信息栏固定在当前工作区高度内并独立纵向滚动，始终保留可见滚动槽；外层 Grid 子项补充 `min-height: 0`，避免长 JD 被视口裁切。手机端仍跟随页面自然滚动。
- 评分交互：四项职业匹配指标置于职位信息画布最顶部，以高对比大字号评分卡作为右栏第一视觉层级；鼠标悬停或键盘聚焦显示指标含义，点击指标后用弹窗展示该维度的全部要求、简历证据和匹配状态。原来的底部指标区已删除。
- 信息精简：删除“结构化阅读视图”“已转换原始 JD”“岗位版本”“岗位画像”等重复说明，只保留公司、职位名称、岗位基本信息与关键词本身。
- 职位画布：`CareerJobCanvas.vue` 只按 JD 中明确的“核心职责 / 工作职责 / 职位要求 / 任职要求”等标题做确定性切分；带编号或项目符号的条目以标记为边界，后续换行合并为同一条，避免把一句职责拆成多个片段。只有职责与要求两个核心分区都可靠识别时才展示双列结构；任一分区不可靠时直接展示完整职位描述原文，不调用 LLM、不推断也不补写。技能标签只使用后端已确认的标准技能别名，元数据和页面控件不进入“其他职位信息”。
- 案例一致性：职位标题、元数据胶囊、岗位画像、职责编号、门槛证据卡、折叠补充信息与四项评分 footer 重新收敛为一个完整画布；基准简历名称作为评分基准显示，不再单独占据画布上方的大卡片。
- 数据调用链：会话上下文 API 返回 `target_role` → `CareerContextRail` 传入 `CareerJobCanvas` → 前端只重排原文；无法分类的有效文本进入可折叠“其他职位信息”，收藏、沟通、分享、举报等网页控件噪音不展示。
- 依赖与边界：未增加第三方依赖、数据库字段或评分算法；岗位原文缺少某项元数据时直接隐藏该项，不推测公司、薪资或地点。本轮优先确认 PC 端，手机端仅保持现有单列与底部弹窗行为。
- 验证：`web-ui` Vite production build 通过；使用真实“小红书 · AI 全栈工程师-电商营销”历史会话验证了历史栏折叠、右栏宽度调整与复位、宽窄画布切换、分区收折、画布内评分入口及证据弹窗。

## 2026-08-20：对话阅读与职位侧栏整理

- 目标：降低长回复的阅读负担，明确区分“你”和“求职助手”，并把右侧职位上下文整理为可快速扫描的证据栏；相关页面不再使用英文眉题叠加中文标题。
- 前端：新增 `CareerMessageContent.vue`，将模型返回的 Markdown 安全拆分为标题、段落、列表、引用、代码块和表格；使用 Vue 文本插值渲染，不引入 `v-html`，避免把模型输出当作 HTML 执行。
- 气泡：用户消息保持右对齐的紧凑浅色气泡；助手消息改为白底阅读卡片并使用主题色左边线，角色名称统一为中文，时间与状态降级为辅助信息。
- 职位侧栏：`CareerContextRail.vue` 移除英文装饰标题，将 `N/A`、职位缩写标题和版本缩写改为中文状态；匹配指标、逐项证据和职位原文继续沿用现有数据与交互，不改变计算逻辑。
- 启动向导：`CareerContextSetupDialog.vue` 同步移除英文眉题和中英混合操作标题，职位信息、岗位版本、简历版本统一使用中文表达。
- 技术取舍：未增加 Markdown 第三方依赖；当前解析器只负责求职消息中已经使用的基础结构，不支持模型输出中的任意 HTML、复杂嵌套列表或合并表格单元格。
- 验证：`web-ui` 生产构建通过；PC 端布局优先完成，手机端仅保持现有响应式行为，本轮未做专项视觉适配。

## 2026-08-21：简历与目标岗位改为可选增量上下文

- 目标：新会话和没有资料绑定的历史会话均可直接聊天；基准简历、目标岗位分别独立可选，用户可在任意后续轮次前补充。
- 前端调用链：点击“开启新对话”直接创建空会话 → 输入框立即可用 → “补充求职资料（可选）”按需解析简历或 JD → `POST /api/career/conversations/{id}/context` 墦量生成绑定版本。
- 后端调用链：空会话不创建 binding；补充任一资料时沿用当前绑定中的另一项并新增版本；每轮服务端读取最新 binding，分别向 `candidate_profile_context`、`target_role_context` 注入实际存在的资料。
- 展示规则：只有简历和 JD 同时存在时才计算四维匹配画像；只有一项时展示现有资料与补充提示，两项都没有时保持普通两栏对话布局。
- 数据依赖：迁移 `20260821_15_optional_career_context.py` 将两个 binding 外键改为可空，并通过约束保证一条 binding 至少包含一项资料。
- 验证：`tests/test_career_context_profiles_v2.py` 覆盖单项 payload、单项模型上下文注入与可选更新请求；`scripts/verify_career_response_prompt.py` 确认无资料时不会要求先上传；前端通过 Vite production build。
- 边界：本次不提供资料解绑；未提交的新字段沿用会话当前版本，历史消息不改写。

## 2026-08-21：从职位库导入目标岗位

- 设计目标：求职助手会话右上角提供“简历资料”和“职位检索”两个快捷入口；原“补充求职资料”中的手动粘贴、输入和文件解析能力完整保留，两条链路并行。
- 交互调用链：点击“职位检索” → `CareerJobSearchDialog.vue` 以弹窗承载现有 `JobSearchWorkspace.vue` → 搜索并加载完整岗位详情 → 点击右下角“确认岗位” → 映射为目标岗位 → 绑定当前会话。
- 数据调用链：`job-library-target-role.js` 将职位库详情统一整理为 `company_name / role_name / source_kind / source_label / job_text` → `POST /api/career/target-role-profiles` 沿用现有确定性要求拆解 → `POST /api/career/conversations/{id}/context` 沿用现有上下文版本和四维指标计算。
- 展示规则：确认成功后右侧 `CareerContextRail` / `CareerJobCanvas` 立即读取新上下文；存在简历时显示技能、经验、项目、关键缺口四项指标，没有简历时只展示职位信息和补充简历提示。
- 状态边界：只有加载到完整职位正文后才能确认；创建目标岗位成功但会话绑定失败时允许原地重试，不重复创建同一岗位版本；导入只影响后续回答，不改写历史消息。
- 验证：职位映射单测 3 项、WebUI production build、既有四维指标后端测试 7 项通过；PC 本地浏览器完成“打开弹窗 → 搜索 → 选岗 → 确认 → 右栏刷新”全流程验收。
- 界面收口：会话标题右侧的“简历资料 / 职位检索”统一为单行标准工具按钮，用图标与状态点表达语义；用户消息改为内容自适应的右侧气泡并移除重复身份行，降低标题栏和对话区的视觉噪声。

## 2026-08-20：基准简历 + 目标岗位上下文工作台

- 目标（已由 2026-08-21 决策修订）：在资料齐全时提供 JD、上下文版本、四维匹配画像及逐项要求证据。
- 前端：`CareerContextSetupDialog.vue` 负责三步启动向导，`CareerContextRail.vue` 负责岗位侧栏，`CareerAssistantPage.vue` 只在上下文存在时开放输入和发送。
- 显示条件：右侧 JD 上下文栏只在会话已经同时绑定基准简历与目标岗位后渲染；未完成导入时保持历史列表与配置引导的两栏布局，不展示空白占位栏。
- 首屏性能：会话历史使用轻量仓储快速路径，只初始化 PostgreSQL Engine 与会话仓储，不触发模型、Embedding、文档解析或云视觉 HTTP Client；前端在历史接口返回后立即渲染，不再等待模型配置、简历档案和免费模型目录。
- 数据：迁移 `20260820_14_career_context_profiles.py` 新增 `candidate_profiles`、`target_role_profiles`、`conversation_context_bindings`。一个会话同一时刻只读取最新绑定；调整岗位会新增 binding version，不改写历史消息。
- Prompt：服务端每轮按当前用户和会话读取绑定，把已确认简历作为个人事实边界、JD 作为目标数据注入 `ModelTurnContext`；浏览器不能直接提交这两段系统上下文。
- 指标：`context_profiles.py` 使用 `lexical-evidence-v2` 计算“技能证据覆盖率、经验要求达成率、项目场景适配率、关键要求缺口率”。算法先识别任职资格、工作职责等章节，过滤标题、图片占位、薪资和扫码噪音，再优先保留可计算的任职资格；技能、经验和项目分别保存分子、分母、得分、逐要求因子与简历证据，确实无法识别时才返回 `N/A`。
- 旧数据兼容：岗位表中的 `requirements` 是创建时快照；会话读取会基于已保存的 `job_text` 使用当前算法重新抽取和评分，因此算法修复会直接作用于既有会话，不要求用户重新上传简历或 JD，也不改写历史绑定版本。
- 面经：面试准备意图会自动以目标公司、岗位和当前问题检索面经；显式 `@面经` 保持限定资料范围的优先语义。
- 验证：前端生产构建、Python compile、Alembic 升级、匿名上下文真实 API、原有求职 API 与 Intake Graph 回归均通过。PC 端完成后再安排手机端适配。

## 目的

本模块为平台提供独立的求职 Agent。它嵌入同一套 WebUI 和部署环境，并通过只读的 `CareerSkillRuntime` 调用现有技能库；仍不依赖 GitHub 热门项目工作流、文章排版、图片视频生成和微信公众号草稿推送逻辑。

## 2026-08-21：职位库与浏览器助手

- 设计目标：将“职位库”作为左侧独立一级菜单，保留 `/interviews/jobs` 路由并与面经库分别控制权限和高亮；用户日常只输入岗位名称，不复制 URL，也不操作扩展执行过程。
- 页面结构：顶部单输入搜索带，左侧真实岗位卡片流，右侧岗位详情阅读区；默认城市为上海。正式页面已移除全部预览岗位，扩展不可用时不会使用模拟数据兜底。
- 调用链：`JobSearchWorkspace.vue` → `job-library-bridge.js` → Chrome Content Script → MV3 Service Worker → BOSS 页面 MAIN world 同源只读 `fetch` → `boss-data.js` 归一化 → 页面。
- 详情语义：搜索完成后读取第一条详情；此后每次点击卡片都会重新请求详情，重复点击当前卡片也不读取旧缓存。请求序号会丢弃快速切换时迟到的旧响应。
- 权限边界：扩展只覆盖项目本地地址、正式域名和 `www.zhipin.com`，只开放连通性检查、岗位搜索和详情读取；不读取 Cookie/密码，不执行投递、沟通或批量操作。
- 访问限制：登录、验证码、风险控制和 HTTP/接口异常均停止当前调用并给出人工处理指引，不尝试绕过。
- 验证：扩展字段与错误契约测试通过，Service Worker、Content Script 和 Manifest 语法检查通过，前端 `npm run build` 通过。真实端到端验证需要先在 Chrome 加载项目扩展并登录 BOSS。
- 完整安装、调用链和维护边界见 [职位库模块](job_library_module.md)。

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
- 求职助手只复用 `SkillLibraryService` 的本地发现和按 ID 读取能力，不改变技能库的保存、GitHub 搜索和 Star 刷新行为；公众号工作台保持不变。

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

## 明确不改动的业务链路

- `src/providers/wechat_client.py`
- `src/tasks/deliver_task.py`
- `src/tasks/summary_task.py`
- `web-ui/src/App.vue` 中已完成的技能库与审核台功能

## 2026-08-20：`/命令`确定性挂载 SKILL.md

- 目标：在求职助手输入框中使用 `@skill-name` 或 `/skill-name` 调用技能库中已经安装的 Skill；原有 `@面经` 引用继续可用。
- 技术取舍：参照 [Agent Skills 客户端实现](https://github.com/agentskills/agentskills/blob/main/docs/client-implementation/adding-skills-support.mdx) 的渐进披露方式，候选接口只返回 `name + description`，激活后才读取完整 `SKILL.md`。未引入新的 Skill 格式或第三方运行时。
- 调用链：`CareerAssistantPage.vue` 候选/Chip → `CareerSkillRuntime.resolve` 服务端解析 → 按稳定 ID 读取对应 `SKILL.md` → 移除 frontmatter、展开参数变量 → `ActivatedSkill` → 独立 `system` 消息 → DeepSeek 多轮 `tool_calls → role=tool` Agent Loop → 最终回复。
- 文本是首次激活事实来源：前端仍兼容提交稳定 Skill ID，但服务端只接受用户输入中真实存在的 `@name`、`/name` 作为新挂载。挂载后会话后续普通追问继承最近一次显式 Skill；本轮显式调用新 Skill 时替换继承项，新会话不继承。用户在首次发送前手工删除调用标记时，旧 Chip/ID 不会暗中激活；原始消息照常持久化，交给模型和工具的任务正文会移除调用标记。
- 上下文预算：单轮最多 3 个 Skill，单份展开后正文最多 48000 字符，总计最多 80000 字符；该上限已覆盖当前扫描到的最大 `SKILL.md`。预算内必须完整挂载，禁止静默截断；超过预算时在调用模型前明确报错。候选阶段不读取正文；最终 API 只返回激活的名称和说明，不向浏览器泄露 `SKILL.md` 内容或本机路径。
- 挂载语义：所有 Skill（包括 `find-skills`）统一使用自身 `SKILL.md` 正文，不再根据 Skill 名称硬编码替换为平台工具。正文中的 `$ARGUMENTS`、`${ARGUMENTS}` 会展开为去掉调用标记后的任务文本；`${SKILL_DIR}`、`${CLAUDE_SKILL_DIR}` 会展开为该 Skill 所在目录。
- Prompt 优先级：求职助手基础规则是第一条 `system`；本轮显式或会话继承的 Skill 使用第二条独立 `system`；历史对话随后加入；任务正文始终作为最后一条 `user`。会话继承时会重新读取 `SKILL.md` 并用当前追问重新展开 `$ARGUMENTS`，不会复用上一轮参数。Skill 必须被用于完成任务，而不是被模型介绍或总结。
- 执行层：`SkillToolRegistry` 向显式挂载 Skill 的模型提供 `search_skill_registry`、`inspect_skill_repository`、`install_skill_repository`。具体 GitHub 仓库必须先检查真实元数据、许可证和标准 Skill 清单；用户明确要求安装后，模型才能继续把完整 Skill 目录安装到项目 `.agents/skills`。安装保留 `scripts/references/assets` 等随附文件，不执行其内容；已存在目录按幂等语义跳过，不静默覆盖。
- Agent Loop：每轮由模型自行选择 `auto` Tool Calling，服务端执行后追加匹配 `tool_call_id` 的 `role=tool`，最多 6 轮，直到模型返回没有 `tool_calls` 的最终正文。工具失败也作为结构化结果返回模型，最终 API 的 `skill_executions` 分别标记 `succeeded/failed`，前端不再把失败显示为真实成功。
- DeepSeek 兼容：现有历史模型档案可能只登记 `text`；由于 DeepSeek Chat Completions 已支持官方 Function Calling，`provider_key=deepseek` 的已挂载 Skill 会直接进入原生 Tool Calling。其他兼容 Provider 仍需显式声明 `tools`，避免向未知端点发送不支持的协议字段。
- 执行边界：当前开放的是 Skill 发现、仓库检查和项目级安装，不开放任意 Shell，也不自动运行新下载的脚本。纯写作、分析、评审和流程型 Skill 可直接生效；其他运行时能力必须继续以明确的受控工具扩展，模型不得伪造执行结果。
- 依赖评估：上游 `agentskills/agentskills` 采用 Apache-2.0（文档 CC-BY-4.0），公开维护活跃；本实现只复用其格式与渐进披露思路，继续兼容项目既有 `.agents/skills` 扫描规则，不复制上游代码。
- 验证：`tests/test_career_skill_runtime.py` 覆盖解析、调用标记剥离、旧选择清理、会话继承/显式替换、变量展开、数量限制、Prompt 边界及序列化后的真实模型请求；`tests/test_career_skill_tools.py` 覆盖三项工具、官方 Tool Calling 消息、多轮检查→安装→最终回复以及完整目录安装/幂等跳过。`scripts/verify_career_skill_agent.py --install` 已用 `deepseek-v4-pro` 和 `JimLiu/baoyu-skills` 真实验证安装链；Web API 双轮实测中，第一轮 `/find-skills` 检查 `phuryn/pm-skills`，第二轮不带命令仍返回 `invocation_source=session`，并通过 `inspect_skill_repository` 成功取得 68 个 Skill。21 个 `baoyu-skills` 目录共 474 个文件已进入项目 Catalog；前端通过 `npm run build`。

## 2026-08-06：会话工具栏与错误反馈

- 会话级操作与输入级操作统一收敛到输入框上方的 `composer-toolbar`：左侧放置职位链接、临时简历、模型选择，右侧放置模型连接与归档会话，避免用户在会话顶部和输入区之间来回寻找操作。
- 错误反馈改为页面中央的非阻塞 Toast：错误出现后默认展示 3 秒，用户也可以随时关闭。Toast 不影响对话历史布局和输入区高度，适合附件校验、模型调用和网络异常等短时反馈。
- 移动端工具栏会自然换行；发送按钮继续固定在输入区底部，确保窄屏下不会遮挡文本输入或附件状态。

## 2026-08-07：会话切换状态隔离

- 浏览器中的草稿文字、职位 URL、正在生成的流式片段和上传文件都属于当前页面的临时状态；切换会话、新建空会话或归档当前会话时必须清空，不能跨会话展示或提交。
- 附件原件按照隐私边界只存在于单次 Agent Turn 的临时目录，任务结束后自动删除；因此历史会话只保留对话文本，不恢复文件本身。
- 每一轮请求的 `requested_selection_mode` 和 `requested_model_profile_id` 已由 `career_assistant.agent_turns` 持久化。读取历史会话时，API 返回该会话最新一轮的选择，WebUI 据此恢复模型下拉框；没有 Turn 或对应档案已不存在时安全回退到“免费额度优先”。
- 前端用递增的会话请求编号丢弃过期响应，防止用户快速点击 A、B 两个会话时较慢的 A 响应反向覆盖 B 的消息或模型选择。

## 2026-08-20：会话重命名与永久删除

- 会话列表为每条记录提供轻量操作入口；重命名在当前列表项内完成，永久删除必须经过二次确认。
- 重命名调用 `PATCH /api/career/conversations/{conversation_id}`，后端按当前用户范围更新标题和时间，不能修改其他用户的会话。
- 删除调用 `DELETE /api/career/conversations/{conversation_id}`。仓储先删除该会话产生的面经检索反馈，再物理删除会话；消息、摘要、Agent Turn、模型用量和步骤记录由 PostgreSQL 外键级联同步删除。
- 删除当前会话后，前端同步清空消息、草稿、文件引用和任务恢复状态；删除其他会话不会打断当前对话。

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

## 2026-08-21：通用 LLM Judge 岗位分析

- 设计目标：岗位分析不再把“技能、经验、项目”三项技术岗位模板强加给所有职位。主播、销售、运营和技术岗位都由固定 Judge 模型根据本次 JD 生成 2 至 5 个岗位专属维度；“关键要求缺口率”仍由服务端统一追加。
- 模型选择：`config/career_assistant.yaml` 的 `job_assessment.judge_profile_key` 指向管理员维护的模型档案，岗位分析不会跟随聊天输入区的模型选择器。解析时优先匹配唯一档案键，并兼容唯一模型 ID；同一模型 ID 对应多个 Provider 时拒绝猜测。模型支持 Tool Calling 时强制调用 `submit_job_assessment`；仅登记文本能力时使用 JSON Object 模式并执行同一套服务端校验。
- 调用链：会话绑定简历与岗位 → 创建或读取 `job_match_assessments` 排队记录 → FastAPI 后台任务原子领取 → 一次 LLM 调用同时完成 JD 分区、动态维度识别和逐项证据判断 → 服务端校验 `JD-nnn`/`CV-nnn` 引用并重新计分 → 页面每 2 秒读取现有会话接口直到进入终态。
- 证据边界：LLM 不能提交总分作为最终结果；服务端只接受真实存在的 JD/CV 编号。`supported` 和 `partial` 必须引用简历证据，`unsupported` 与 `needs_confirmation` 不允许伪造简历引用。公司介绍、薪资福利和工作条件按独立分区展示，不再混入任职要求。
- 状态与降级：状态为 `queued`、`analyzing`、`ready`、`fallback_ready`、`failed`。可重试的模型错误、JSON/Schema 错误最多调用三次；仍失败后执行原 `lexical-evidence-v2`。旧逻辑至少形成两个有效维度才展示“基础规则分析”，否则页面明确显示岗位分析失败并提供重新分析入口。
- 持久化与复用：`career_assistant.job_match_assessments` 以简历版本、岗位版本、Provider、模型和 Prompt 版本组成唯一组合，避免同一资料重复调用；`claim()` 只允许一个后台任务从 `queued` 进入 `analyzing`。读取到遗留 `queued` 记录时会重新安排任务，避免页面刷新后一直停留在排队状态。
- PC 展示：评分卡、说明和证据弹窗完全读取动态维度；下方职位画布优先使用已验证的 Judge 分区，结果未完成时显示分析状态，降级时显示来源标记，失败时允许手动重试。本轮未做平板专项适配，也未新增移动端专属布局。
- 依赖与取舍：复用现有 `ModelGateway`、OpenAI-compatible 客户端、PostgreSQL、FastAPI `BackgroundTasks` 和旧词典算法；没有引入新的队列、缓存、Embedding 服务或 LLM 框架。
- 验证：Alembic 已升级至 `20260821_16 (head)`；岗位分析、上下文与历史恢复共 26 项 Python `unittest` 通过；前端动态维度与 JD 分区 6 项 Node 测试通过；`compileall` 与 Vite production build 通过。当前环境未安装 `pytest`，因此本轮 Python 回归使用标准库 `unittest` 执行。
- 故障修复：曾因配置填写 `deepseek-v4-flash`（模型 ID），而运行时只接受 `deepseek-deepseek-v4-flash`（档案键），导致 Judge 在第 0 次调用前即被判定不可用。现已修正配置并增加档案键/唯一模型 ID 双路径解析及歧义回归测试；JSON Object 模型遗漏服务端 Schema 常量时由服务端补齐，错误版本仍拒绝。`needs_confirmation` 现按“当前简历无可见证据”计入指标分母，避免未知硬性要求被排除后形成虚假高分或 0% 缺口；仅含 `context_only` 的薪资、公司介绍等维度由服务端从匹配指标中剔除。Prompt 版本升级为 `career-job-judge-v2`，并禁止用间接经历推断未写明条件。34 项求职助手相关 Python 回归通过；真实“居家语音厅主播”岗位以 `deepseek-v4-flash` 一次分析成功，终态为 `ready`，保留 2 个岗位专属指标和 1 个关键缺口指标。

## 2026-08-21：会话历史分页与满高列表

- 设计目标：左侧会话历史不再一次加载并滚动全部记录。默认每页 15 条，用户可在分页栏切换为 10、15、20 或 25 条；选择结果保存到浏览器本地，下次进入求职助手继续沿用。
- 后端契约：`GET /api/career/conversations?page=1&page_size=15` 返回 `items`、`page`、`page_size`、`total` 和 `total_pages`。`page` 最小为 1，`page_size` 限制在 1 至 100；仓储在同一事务中读取总数和当前页，并使用稳定的 `updated_at DESC, id DESC` 排序。
- 交互同步：新建会话、发送消息或重命名后切回第一页，以保证刚更新的会话仍位于正确的全局顺序；删除和归档会缩减总数，并在末页变空时自动回退到仍存在的最后一页。并发切页使用请求序号丢弃过期响应。
- PC 布局：左栏保持整列满高，操作区和分页栏固定，会话卡片只在中间剩余区域内均匀伸展；默认请求 15 条，超出可用视口时只滚动记录区，不推动聊天区和页面整体滚动。分页控制条可切换每页数量、前后翻页，也可输入页码后按 Enter 或点击“前往”跳转；越界页码会自动修正到有效范围。
- 依赖与边界：复用现有 FastAPI、SQLAlchemy、Vue 和浏览器 `localStorage`，没有增加分页组件库或新的数据表。本次保持已有手机端自然滚动规则，不做平板专项适配。
- 验证：20 项 Node 测试、分页与会话管理相关的 6 项 Python `unittest` 及 Vite production build 均通过；覆盖默认 15 条、四档页容量、指定页越界修正、仓储分页和历史快速路径。

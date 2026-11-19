# 技能库模块说明

## 2026-11-04：resume-builder主对话与部署种子

- 安装指定Reactive Resume仓库的resume-builder，保留MIT许可证和schema参考；中文适配允许先审查已绑定简历，不强制转换JSON。当前平台无Reactive Resume MCP/schema工具，导入草稿不能宣称已验证。
- 主对话支持`/resume-builder`与`/tailored-resume-generator`分别或组合调用；沿用手动Slash、模型选择和会话继承。自动开场仍仅使用既有tailored技能，不增加JSON输出或第二次自动模型调用。
- 修复种子/导出只携带SKILL.md的问题：同包保留references中的md/json/txt及LICENSE/NOTICE，只读资料与许可证不会作为脚本执行；默认不覆盖用户文件。清单记录附带文件校验值，生产不依赖开发机home目录。
- 验证：真实主对话两次模型调用通过；本地Linux容器断网、无用户Skill目录时两份种子可安装挂载；后端660、前端252项通过。并发延迟、注入故障和当前边界见`docs/resume-skills-stress-report-2026-11-04.md`。本轮未部署生产。

## 2026-11-04：自动初评的一次性挂载

- 确认简历并绑定对话后，后台复用Runtime读取`tailored-resume-generator`，以`invocation_source=automatic`挂载到单个初评Turn；手动Slash与会话继承规则保持原样。
- 初评在Skill上下文附加任务范围：无JD也给建议，不直接生成完整简历，不编造指标或未知日期。历史不写Slash命令，因此下一轮不会因这次初评继承Skill；用户原先显式激活的Skill仍按既有规则处理。
- `SkillToolRegistry`对纯自动挂载返回空工具列表，不开放搜索/安装等无关操作。手动/会话继承的Skill仍按原规则提供工具。
- 触发链、并发去重与真实本地验收见`docs/career_assistant_module.md`。

## 2026-11-04：内置按岗位定制简历

- 设计目标：将用户提供的 `tailored-resume-generator` 适配为求职对话可调用的中文技能，覆盖 JD 优先级、真实经历映射、ATS 关键词、定制简历和缺口说明。
- 文件：`.agents/skills/tailored-resume-generator/SKILL.md` 为当前本地生效副本；`deploy/skill-seeds/tailored-resume-generator/SKILL.md` 为同内容内置种子。项目副本优先于用户目录同名版本，只展示一个候选；种子只用于后续按既有机制安装，不代表本轮已部署。
- 技术取舍：复用 `SkillLibraryService` 和 `CareerSkillRuntime`，不新增按名称分支、路由或数据库结构。保留原技能的 JD 拆解、经历重组和 ATS 思路，删除无来源量化成果的示例，要求只使用简历或用户确认的事实；默认中文，地区和模板偏好由用户决定。
- 调用链：输入 `/tailored-resume-generator 请根据已绑定简历和 JD 定制简历` → `/api/career/skills/mentions` 可检索候选 → Runtime 去除调用标记并展开任务参数 → 完整正文挂载到模型上下文 → 与当前会话已有简历/JD 一起参与回答。当前会话后续轮次按既有规则继承；换技能或新会话时仍遵循原运行时行为。
- 产品边界：这是求职聊天内的显式技能，不会自动改写所有回复，也不改变独立 `/resume-assistant` 页面的分析/生成 Prompt。缺简历或 JD 时先补资料；不把待确认信息写成事实，不自动保存历史或投递；仅输出实际支持的格式，不宣称已经创建 Word/PDF。
- 验证：实际文件扫描验证项目优先、同名去重、中文候选检索、Slash 激活、参数展开、后续会话继承和两份文件一致性均通过；`test_career_skill_runtime.py` 与 `test_career_skill_tools.py` 共 20 项通过；`verify_skill_portability.py` 通过。本轮未调用真实模型生成简历，未新增前端功能，未连接生产环境。
- 后续边界：事实约束由模型指令承载，不是确定性事实校验器；若需要在独立简历助手里选择策略或生成文件，应作为单独功能设计，不由此 Skill 安装隐式扩展。

## 目标与边界

- 技能库用于查看本机已安装 Skill、借助 `find-skills` 搜索 GitHub 开放 Skill，并查看或编辑对应的 `SKILL.md`。
- 该模块不改动微信公众号内容生成和审核发布链路；求职助手通过只读适配层复用已安装 Skill 的摘要与 `SKILL.md`，不会从聊天页保存或覆盖 Skill。
- 保存行为会更新已有项目本地 Skill，或以用户填写的名称创建项目本地副本；前端按钮统一使用“保存并更新”表达这两种结果。

## 视口布局

- 页面沿用全局固定视口：左侧已安装 Skill 列表独立滚动，右侧由搜索区与预览编辑区组成。
- 右侧预览编辑区会占满搜索区以下的可用高度；`SKILL.md` 编辑器仅在自身区域内部滚动，保存栏保持在该面板底部。
- 此布局避免已选 Skill 内容较短时，面板下方留下大块无意义空白；窗口高度不足时，右侧主面板内部滚动，不扩张全局页面。

## 2026-08-07：预览区填充调整

- `skills-main-panel` 明确继承工作区高度，`skill-editor-panel` 从固定 720px 改为弹性填满剩余高度。
- 编辑器使用可用高度而不是固定内容高度，确保预览和保存区域形成完整的单一面板。

## 2026-08-20：求职助手调用适配

- `SkillLibraryService` 仍是 Skill 发现和读取的唯一文件层入口；`CareerSkillRuntime` 不自行扫描第二套目录，避免名称优先级和去重规则分叉。
- `/api/career/skills/mentions` 只返回候选元数据。首次挂载必须由输入中的精确 `/skill-name` 触发；`@` 仅用于求职助手引用面经。`selected_skill_ids` 中已经从文字删除的旧选择会被忽略；挂载成功后，该 Skill 会在当前会话后续轮次持续生效，直到用户显式调用另一个 Skill 或开启新会话。
- 所有 Skill 统一按文件语义挂载：移除 YAML frontmatter 后，将正文作为独立 `system` 上下文交给模型，并把命令后正文展开到 `$ARGUMENTS` / `${ARGUMENTS}`，把 Skill 目录展开到 `${SKILL_DIR}` / `${CLAUDE_SKILL_DIR}`。
- 不再存在 `find-skills → skill_search` 的名称硬编码。`/find-skills` 与 `/tech-resume-optimizer` 使用完全相同的加载路径，因此模型收到的行为规则始终以用户选中的 `SKILL.md` 为准。
- `SkillToolRegistry` 以通用能力而不是 Skill 名称绑定工具：skills.sh 实时搜索、GitHub Skill 仓库检查、项目级完整目录安装。仓库检查优先识别标准 `skills/`、`.agents/skills/`、`.claude/skills/`，避免把仓库维护用内部 Skill 与公开分发目录混装。
- 项目安装从 GitHub 默认分支归档中只提取选中的完整 Skill 目录，单次上限 2000 个文件、80 MB，目标固定为 `.agents/skills/<name>`；已有目录跳过，不覆盖用户本地修改。新目录会被 `SkillLibraryService.list_skills()` 立即扫描，无需另一套缓存刷新。
- 最终响应中的 `activated_skills[].status = mounted` 表示文件已真实进入本轮模型上下文；会话继承轮次的 `invocation_source` 为 `session`。`skill_executions` 则证明模型通过 Agent Loop 实际执行了哪些工具、状态和结果数。挂载或安装不等于执行新 Skill 自带脚本。

## 2026-08-25：GitHub Star 快照恢复

- Star 仓库识别优先读取 `SKILL.md` 的 `repository_full_name`、`github_repository`、`repository`、`homepage` 等字段；缺失时读取项目、用户目录或 `deploy/skill-seeds/.skill-lock.json` 中经过核验的 GitHub 安装来源。生产种子只记录公开仓库地址，不携带凭据。
- 列表和详情接口仍只读取 `data/skill_star_cache.json`，不会因打开技能库而同步等待 GitHub。首次快照会立即展示总 Star 数；积累下一期快照后再计算新增量和增长率。
- 生产 `pipeline-scheduler` 与 API 共用 `application_skills` 和 `application_data`：Scheduler 启动时刷新一次过期快照，周五内容任务开始时再次检查；七天内的新快照直接复用，不重复请求 GitHub。
- 无法关联公开 GitHub 仓库的项目自建或本地 Skill 明确显示“本地 Skill”，不再显示“暂无 Star”圆环；这类 Skill 不伪造仓库和 Star 数据。
- `scripts/refresh_skill_stars.py` 继续保留为独立手动诊断入口，使用同一服务方法和七天有效期规则。

## 2026-08-28：grill-me 工作流修复

- `grill-me` 改为自包含的分轮追问工作流，不再通过正文中的 `/grilling` 间接引用另一个 Skill；当前运行时只解析用户消息中的显式 `/skill-name`，不会递归激活 Skill 正文里的 Slash Command。
- 每轮只询问当前已经具备前置条件的决策，并附推荐答案；模型必须等待用户回答后再展开下一轮，在用户确认形成共同理解前不得直接实施方案。
- 项目种子与本机当前生效副本同步更新；未调整求职助手前端，也未新增 Skill 状态展示。


## 2026-11-06 手机阅读与编辑

手机将已安装、搜索、内容编辑按当前任务呈现，使用现有 CareerMessageContent 阅读正文，编辑框和阅读区共用 skillEditorMarkdown。切换不清空草稿；保存错误放在公共可见区域，避免编辑页看不到搜索区域中的错误。原 skills API、个人／公共技能规则和保存语义不变，没有新增导入能力。

前端 295 项、浏览器 47 项通过，覆盖修改后阅读、切回编辑、保存 503 时保留内容与错误提示可见。真实 GitHub 搜索和真实写盘未在本轮联调。详见 [双端适配](mobile_ui_adaptation.md)。

### 压力审查修复：草稿与请求顺序

App 的详情加载采用请求序号，只接纳最新选择的成功或失败响应；同项重选保留编辑，跨项选择通过技能 ID 的内存 Map 保存文本和名称，退出登录清空。保存重载后再设置成功反馈，手机阅读正文使用 16px；内容页接入手机返回层，先回列表再离开路由。

使用两个以上技能验证迟到响应与跨技能草稿，另测同项重选、保存成功反馈和原有 503 保留内容。沿用 skills API，不新增存储格式或依赖；草稿仅在当前登录会话中保留。最终验证见 [修复记录](designs/2026-11-06-dual-ui-grill-fixes.md)。

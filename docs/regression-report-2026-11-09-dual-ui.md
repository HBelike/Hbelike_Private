# 全平台 PC／手机双端 Grill 检查报告

日期：2026-11-09（Asia/Shanghai）。范围：当前工作区全部 Web 模块，先 PC 后手机；只做启动、审查、复现与记录，未修复业务源码或修改既有测试。

**结论：项目已在本地启动，但尚不能验收为全平台双端兼容。确认 12 项未修复问题：9 项 P1、3 项 P2；另有 1 项跨模块草稿边界待产品确认。** 历史“61 项通过”不是本次结论，本次首次基线为 60/61，失败项是测试选择器在合法等待状态下匹配到两个元素。

后续更新：用户已确认 D01 应保留会话、草稿和附件，并已完成该项本地实现；[工作区保留记录](designs/2026-11-09-career-workspace-retention.md) 包含修复后验证。上述 12 项审查缺陷及原始证据不变，仍待修复；下文其余“本轮”描述审查发生时的状态。

## 1. 启动和运行状态

| 项目 | 本轮结果 |
| --- | --- |
| Git | HEAD `3215973a9e6ac9d40f0284813a2f7a2dca735ad0`；开始时已有大量未提交修改，已保留，见 [初始状态](../tmp/regression-20261109/git-status-before.txt) |
| 前端 | [http://127.0.0.1:5173](http://127.0.0.1:5173)，Vite 已启动并保持运行；使用当前源码，代理指向本机 18080 |
| API | [http://127.0.0.1:18080/api/health](http://127.0.0.1:18080/api/health) 返回 200；通过 `preview_server.py` 启动，关闭热重载以稳定审查 |
| 数据库 | `.env.career-assistant` 的目标已核对为 `127.0.0.1:54329`；真实只读连接通过；当前与代码迁移均为 `20261104_34`，未执行迁移 |
| Worker | 本地 `run_career_agent_worker.py` 已启动，进程保持运行；启动前 queued=0、到期租约=0、记忆任务无待办，避免领取历史任务 |
| 历史任务 | 有 1 条 `running` 且租约为空的旧记录；保持原状。审查前后 Turn 状态计数相同：84 succeeded／16 failed／1 running |
| Docker | 用户启动 Docker 后，本机 PostgreSQL、Docling、Gotenberg、Piston 容器已运行；未重建容器，未操作生产 |
| 实际鉴权 | 未登录访问 `/career` 跳转登录页；真实 `/api/auth/me` 和 `/api/navigation/modules` 返回 401，属正常拒绝；真实模型列表读取到 3 项，不代表模型已调用验证 |

真实启动证据：[startup-status.json](../tmp/regression-20261109/startup-status.json)、[backend.log](../tmp/regression-20261109/backend.log)、[真实认证页结果](../tmp/regression-20261109/live-startup/results.json)。健康检查仅证明存活，不能替代登录后的业务闭环。

## 2. 逐模块验收矩阵

| 模块 | PC 实际检查 | 手机实际检查 | 本轮判断／限制 |
| --- | --- | --- | --- |
| 应用壳层／导航 | 多尺寸导航、布局、直接路由、权限基线 | 抽屉、焦点、嵌套返回、旋转、浏览器前进 | 基础布局通过；返回状态有 R12 |
| 登录／注册／找回 | 真实未登录重定向与三路由，受控注册验证码返回 | 360／390px、844px 横屏；真实组件输入布局与注册返回 | 基础布局通过；认证状态错误 R09，未发送真实验证码 |
| 求职对话／资料／模型 | 中文组词、发送次数、503、上下文、短屏、报告、模型列表 | 多宽度、键盘近似高度、工具展开、正文、Sheet、焦点 | 原核心用例通过；跨模块草稿为 D01；真实模型调用未验收 |
| 职位库／职位检索 | 扩展连接替身、长 ID、非空结果、详情、确认、900px 与短屏 | 原有详情、提示及移动能力边界 | 检索弹层有 R01、R02；未实际连接招聘扩展 |
| 一键打招呼 | 模拟搜索、选择、字数限制、生成／发送防重逻辑 | 原字数与布局门禁、电脑能力说明 | 短屏选择区有 R02；未调用模型或真实发送 |
| 匹配报告 | 长岗位／简历名、短屏、筛选、原文、失败保留、延迟两轮 queued→ready | 390×844、390×430、844×390，同样验证报告替换 | 本轮业务探针通过；旧自动化存在 T01，不能忽略首次失败 |
| 简历助手 | 文件／JD／要求／建议／生成内容保留；延迟历史详情后换模块 | 同样验证跨模块及迟到响应 | 已有正常保留路径通过；迟到详情破坏路由 R03；真实文档解析未验收 |
| 面经库 | 非空列表、查询、阅读、A／B 响应乱序 | 列表返回、前进、相同乱序 | 有 R05、R12；未进行真实采集／Embedding |
| 技能库 | 多技能草稿、读取乱序、保存成功／失败、保存等待期间继续编辑 | 阅读／编辑与相同保存竞态 | 正常切换通过；保存覆盖新编辑 R04；未真实写盘或调用 GitHub 搜索 |
| 公众号工作台 | 文章／素材／历史／任务／提示词路由基线，非空失败运行日志 | 长文表格、相同日志正文、Tab、返回 | 日志弹层有 R08；未执行生成、审核、发布 |
| 评测中心 | 创建与表单、真实指标键、两项非空实验乱序 | 四类表单、360px 四子路由、相同详情乱序 | 有 R06、R10；未启动真实实验 |
| 管理台 | 权限、模块、GitHub／策略、非空模型上下文，编辑返回 | 360／390px 同样检查 | 布局基线通过；提示词草稿丢失 R07；未写入实际配置 |
| 链路监控 | enabled=true、正常长项目名及控制台入口 | 360px 非空项目名，内部宽度 | 有 R11；未登录真实 LangSmith |
| 线上笔试／面试大师 | 窄 PC 标签编辑保留；桌面协议／音频逻辑测试与构建 | 挂载采集前的电脑能力说明，不初始化手机采集 | 符合既定“电脑专属能力”边界，**不等于手机拥有完整功能**；真实音频／浏览器权限／执行器链路未验收 |

PC 沿用 900×420、1024×600、1093×614、1280×720、1440×900、1920×1080；补查 760／900／1024px 弹层断点。手机覆盖 360／375／390／430px、844×390 横屏、390×430 键盘近似高度。窄 PC 使用桌面 UA；手机使用 Chromium 移动 UA／触控模拟。没有开展平板专项，也没有把模拟视口当作 iPhone Safari／Android 真机。

## 3. 已确认问题

### R01 · P1 · 900px PC 职位检索隐藏详情，但允许确认

900×720 → 已有会话 → 职位检索 → 搜索 Agent → 点岗位；详情始终 `display:none`，确认按钮却可用。760／1024px 同数据存在详情。用户无法在确认前核对 JD。

原因：[CareerJobSearchDialog.vue:89](../web-ui/src/components/CareerJobSearchDialog.vue:89) 按 viewport≤900 隐藏详情，而 [JobSearchWorkspace.vue:1064](../web-ui/src/components/JobSearchWorkspace.vue:1064) 按容器≤760 才切换列表／详情。应统一容器断点与显示状态；验收须在 760／900／1024px 均能查看完整 JD 再确认。

证据：[截图](../tmp/regression-20261109/career-evidence/job-dialog-900.png)、[结果](../tmp/regression-20261109/career-evidence/results.json) 的 `job-dialog-900`。

### R02 · P1 · 短屏 PC 的检索／招呼岗位区被裁切

900×420 搜索成功后，检索结果卡正常点击被结果标题／外壳拦截；招呼的 `.job-browser` clientHeight=0，无法选岗和生成。760×720 另有搜索控件与详情头重叠。1440×900 同一流程正常。

定位：[CareerJobSearchDialog.vue:85](../web-ui/src/components/CareerJobSearchDialog.vue:85)、[CareerGreetingDialog.vue:714](../web-ui/src/components/CareerGreetingDialog.vue:714)、[JobSearchWorkspace.vue:941](../web-ui/src/components/JobSearchWorkspace.vue:941)。固定控制区耗尽高度，结果承担全部压缩，外层又隐藏溢出。应为结果保留可操作高度并建立可滚动主体；通过标准为短屏真实搜索后，至少可正常点击、核对、选择岗位，不能仅检查打开按钮或强制点击。

证据：[检索遮挡](../tmp/regression-20261109/career-evidence/job-height-900x420-error.png)、[招呼选择](../tmp/regression-20261109/career-evidence/greeting-select-900x420.png)、[额外结果](../tmp/regression-20261109/career-evidence/extra-results.json)。

### R03 · P1 · 简历迟到响应修改其他模块的 URL

PC／手机：点击简历历史记录，阻塞详情 GET，导航到求职助手，再释放详情。屏幕仍是求职助手，URL 却变成 `/resume-assistant/{id}`。

定位：[ResumeAssistantPage.vue:262](../web-ui/src/components/ResumeAssistantPage.vue:262)。缓存实例中的请求完成后没有核对路由／请求版本，直接设置详情并 pushState。应在停用和新选择时废弃响应；验收须保证迟到成功／错误均不影响当前模块与 URL。

证据：[history results](../tmp/regression-20261109/history-evidence/results.json) 的 `resume-late-record-pc/mobile`。

### R04 · P1 · 技能保存覆盖保存期间的新输入

PC／手机：提交版本 A，延迟保存响应；编辑框仍可输入版本 B。响应返回后，B 被静默改回 A。

定位：[App.vue:936](../web-ui/src/App.vue:936) 仅核对技能 ID，[939 行](../web-ui/src/App.vue:939) 直接覆盖文本；编辑框仍启用。应锁定编辑或按提交时的编辑版本确认是否可以覆盖；验收须保留保存期间的新输入，并准确说明已保存哪个版本。

证据：[history results](../tmp/regression-20261109/history-evidence/results.json) 的 `skill-save-late-pc/mobile`，含 intendedDraft 与 actualDraft。

### R05 · P1 · 面经迟到详情覆盖最新选择

PC／手机：A 慢、B 快，依次选择 A→B，显示 B 后释放 A，页面和当前选中变回 A。

定位：[InterviewLibraryPage.vue:703](../web-ui/src/components/InterviewLibraryPage.vue:703)。缺少请求版本校验。应只允许最新选择提交详情、错误和 loading 状态；验收覆盖 A／B 两项数据与两种返回顺序。

证据：[history results](../tmp/regression-20261109/history-evidence/results.json) 的 `interview-late-selection-pc/mobile`。

### R06 · P1 · 评测实验存在相同的响应乱序

PC／手机：选实验 A 后选 B，先返回 B 再返回 A，详情和 active 状态回到 A，不能保证后续操作针对最后选择的实验。

定位：[EvaluationCenterPage.vue:226](../web-ui/src/components/EvaluationCenterPage.vue:226)，232 行直接赋值。应核对最新选择；验收须用两项非空实验验证标题、ID、操作对象和等待状态一致。

证据：[business results](../tmp/regression-20261109/business/results.json) 的 `evaluation-race-*`、对应截图。

### R07 · P1 · 管理策略草稿跨子页无提示丢失

PC／手机：编辑文章提示词 → GitHub 热门 → 浏览器返回；非空修改被服务器原文覆盖，未发生确认。

定位：[AdminConsolePage.vue:78](../web-ui/src/components/AdminConsolePage.vue:78) 路由 watch 重新加载，[176 行](../web-ui/src/components/AdminConsolePage.vue:176) 的 loadConfig 覆盖共享 form。应保留会话草稿或在离开前提供保存／放弃选择；验收须验证返回、取消离开及保存后的基准状态。

证据：[business results](../tmp/regression-20261109/business/results.json) 的 `admin-draft-pc/mobile`。

### R08 · P1 · 工作流日志手机不可读，弹层行为缺失

390px 的日志 grid 为 `72px 66px 150px 0px`，正文宽度 0，128 字正文高度约 2008px，控制台看不到可读日志。PC／手机打开后焦点仍在背景，Tab 可进入背景卡片；手机返回直接退出工作台。

定位：[styles.css:1797](../web-ui/src/styles.css:1797) 固定列，[PipelineLogDialog.vue:58](../web-ui/src/components/PipelineLogDialog.vue:58) 和 [207 行](../web-ui/src/components/PipelineLogDialog.vue:207) 未接入共用焦点与 history。应采用手机可读布局并复用弹层行为；验收须有非空日志、关闭可达、Tab 约束、返回先关闭弹层。

证据：[手机截图](../tmp/regression-20261109/business/pipeline-log-mobile.png)、[extra results](../tmp/regression-20261109/business/extra-results.json) 的 `pipeline-log-*`。焦点、返回作为该弹层的子问题，不重复计数。

### R09 · P1 · 注册验证码状态被带入登录流程

PC／手机真实认证组件：登录 → 创建账号 → 注册验证码阶段 → 浏览器返回登录。仍显示验证码已发送，提交到 `/api/auth/email-login/verify` 时携带注册 challenge_id。

定位：[LoginPage.vue:107](../web-ui/src/components/LoginPage.vue:107) 只同步 mode，没有像按钮切换一样清理验证码阶段／挑战状态。后端 [repository.py:417](../src/platform_access/repository.py:417) 按用途查询 challenge，因此注册挑战不能通过登录验证；这是依据源码的结论，本轮没有发送真实邮件。应在路由模式变化时清理或按用途隔离挑战；验收覆盖注册／找回→登录的系统返回。

证据：[final results](../tmp/regression-20261109/business/final-results.json) 的 `auth-challenge-back-pc/mobile`。

### R10 · P2 · 360px 评测四子路由右侧裁切

默认空数据即可复现：main clientWidth=360、scrollWidth=371；页面可用 312px，标题／Tab／操作区实际 347px，右边界 371。不是超长模拟字段导致。

定位：[styles.css:1444](../web-ui/src/styles.css:1444) 的 grid 收缩边界与 1468 行 max-content Tab 组合。应约束 grid 子项的最小宽度并让 Tab 在自身范围滚动。验收四子路由 360／375／390px 的 main、表单、卡片与操作区内部宽度，不能只检查根节点。

证据：[final results](../tmp/regression-20261109/business/final-results.json) 的 `empty360/*`。

### R11 · P2 · 监控项目名溢出卡片

正常 CamelCase 配置 `WechaOfficialAccountCareerAssistantProductionTraces` 即复现：手机项目卡 310／481px，main 360／506px，文字被裁；PC 1440px 时卡片 375／481px，文字侵入邻卡。根页面宽度检查仍通过。

定位：[styles.css:1391](../web-ui/src/styles.css:1391)、[1402 行](../web-ui/src/styles.css:1402)，缺少卡片收缩／长词换行约束。验收 PC、手机正常长配置名的卡片内部宽度和完整可读性。

证据：[正常项目名结果](../tmp/regression-20261109/business/monitor-normal-results.json)、[手机截图](../tmp/regression-20261109/business/observability-normal-mobile.png)。

### R12 · P2 · 手机 history 前进与非顶层清理不一致

触发一：面经详情后退回列表，再前进，marker 恢复而详情不恢复。触发二：岗位抽屉上打开报告，390×844 转 844×390 导致父抽屉关闭；报告关闭、切模块后还残留历史层，额外一次返回才离开求职助手。

定位：[use-history-layer.js:14](../web-ui/src/use-history-layer.js:14) 只处理关闭，[41 行](../web-ui/src/use-history-layer.js:41) 非顶层 withdraw 仅移除 registry，[52 行](../web-ui/src/use-history-layer.js:52) 依赖栈顶 marker。应完整协调历史层的恢复与非顶层清理；验收前进／后退、显式关闭、旋转和跨模块组合。

证据：[history results](../tmp/regression-20261109/history-evidence/results.json) 的 `interview-forward-layer`、`nested-report-resize-parent`。

## 4. 待确认产品边界与测试问题

**D01：未发送的求职草稿跨模块保留。** 手机填写草稿后进入线上笔试电脑能力说明，再点返回，草稿为空、会话未选择。当前实现确实丢失输入，但历史设计主要承诺跨断点保留，对跨模块的范围需明确。本轮已向用户提出“保留／离开前提示／允许清空”选项，暂不计入 12 项确认缺陷，也没有擅自假定答复。证据：[state-results.json](../tmp/regression-20261109/career-evidence/state-results.json) 的 `capability-roundtrip-draft`；涉及 App 的求职页面卸载与 [DesktopCapabilityNotice.vue:16](../web-ui/src/components/DesktopCapabilityNotice.vue:16) 的 href 返回。

**T01：既有报告 E2E 断言有时序歧义。** [job-match-dialog.spec.js:89](../web-ui/e2e/job-match-dialog.spec.js:89) 用不带筛选的 `getByRole('status')`，等待期间合法存在“上一次成功报告”和“正在分析”两项 status，首次全量触发 strict mode violation。有限单跑通过，说明时序会掩盖断言问题。补充延迟两轮轮询的 PC／手机探针均确认业务正确：旧 75–100% 保留、重试禁用，新 25–50% 返回后替换并移除旧标识。应限定要检查的状态文本并断言新旧报告替换；本轮没有改测试使其变绿。

证据：[首次日志](../tmp/regression-20261109/browser-baseline.log)、[定向重跑](../tmp/regression-20261109/browser-baseline-retry.log)、[延迟状态结果](../tmp/regression-20261109/career-evidence/state-results.json)。

## 5. 实际套件结果和证据等级

| 检查 | 本轮结果 | 证据等级与日志 |
| --- | --- | --- |
| Web Node | 297/297，通过 | 离线逻辑／源码契约；[日志](../tmp/regression-20261109/web-node.log) |
| Vite build | 通过；已有大 chunk 提示 | 编译；[日志](../tmp/regression-20261109/web-build.log) |
| 浏览器首次基线 | **60/61，退出 1**；T01 严格选择器错误 | Chromium 业务 fixture，Firefox 基础布局；[日志](../tmp/regression-20261109/browser-baseline.log) |
| 失败项有限单跑 | 1/1，通过；不覆盖首次失败 | [日志](../tmp/regression-20261109/browser-baseline-retry.log) |
| 本轮新增压力探针 | 复现 R01–R12、D01，另验证合法报告等待状态 | API／扩展受控替身；脚本完成不代表业务通过，原始 JSON 与截图均保留 |
| 相关后端契约 | 54/54，通过，3 warnings | 仅导航、身份中间件、评测、简历、笔试／面试 Web 的 Fake／TestClient；[日志](../tmp/regression-20261109/backend-ui-contracts.log)，未声称全后端回归 |
| 浏览器扩展 Node | 53/53，通过 | 扩展逻辑替身；[日志](../tmp/regression-20261109/extension-node.log)，未在招聘网站实际发送 |
| 桌面助手 | 8/8、typecheck、build 通过 | 协议／音频逻辑与编译；[测试](../tmp/regression-20261109/desktop-node.log)、[类型](../tmp/regression-20261109/desktop-typecheck.log)、[构建](../tmp/regression-20261109/desktop-build.log)，未授权真实音频采集 |
| 真实本地服务 | API、DB、迁移、Worker 进程、匿名鉴权页面 | 只读检查通过，不是登录后全部业务闭环 |

真实付费模型、邮件发送、BOSS 招呼发送、公众号写入／发布、LangSmith 登录均未主动调用。浏览器探针没有写入真实业务库；未创建测试用户或清空队列，保留历史 running；所有探针浏览器已关闭。用户要求启动项目，因此 Vite、API、Worker 保持运行；没有停止用户现有 Docker 服务。原始日志可能包含本机路径，不含本轮主动打印的凭证。

## 6. 复跑与下一步

前端／后端启动命令分别为 `npm --prefix web-ui run dev` 和 `.venv/Scripts/python.exe preview_server.py`；完整开发脚本为 `scripts/start_dev_backend.ps1`，会同时启动 Worker。当前已有进程，复跑前检查端口，勿重复启动。

```powershell
# 在 web-ui 目录
npm test
npm run build
npx playwright test --reporter=line

# 在项目根目录，本机 Vite 5173 保持运行
node tmp/regression-20261109/mobile-history-probes.mjs
node tmp/regression-20261109/career-audit.mjs
node tmp/regression-20261109/career-extra-audit.mjs
node tmp/regression-20261109/career-state-audit.mjs
node tmp/regression-20261109/audit-business.mjs
node tmp/regression-20261109/audit-business-extra.mjs
node tmp/regression-20261109/audit-business-final.mjs
node tmp/regression-20261109/audit-monitor-normal.mjs
node tmp/regression-20261109/live-startup-audit.mjs
```

修复顺序：先 R01／R02 的功能不可达与 R03–R09 的状态丢失／对象错误；再 R10–R12 的布局和返回一致性，同时修 T01 并将本次非空、延迟、360px 探针迁入正式断言。修前证据保留，PC 修复验证后再验手机。真实设备与真实账户闭环应另行验收，不能用 Mock 补记通过。

## 新任务交接摘要

1. 目标：启动本地项目并逐模块 Grill 双端兼容；启动和只读审查完成，兼容验收未通过。
2. 新增文件：本报告及 `tmp/regression-20261109/` 的日志、独立探针、JSON、截图；未修改业务源码、既有测试或生产配置。
3. 启动状态：Vite 5173、API 18080、本地 Worker 保持运行；数据库 54329 可连、迁移与代码同为 20261104_34。
4. 验证结果：Web 297、扩展 53、桌面 8 与相关后端 54 项通过，Web／桌面构建与类型通过；首次浏览器 60/61，失败项单跑 1/1。
5. 未解决：R01–R12（9 P1、3 P2）均待修复；D01 后续已确认并实现，结果见工作区保留记录；T01 测试选择器待修。
6. 关键命令：`npx playwright test --reporter=line` 和本报告列出的 `node tmp/regression-20261109/*.mjs`，逐个选择执行。
7. 数据边界：受控浏览器 API；没有真实模型、验证码、招呼、发布写操作；旧 running 未改；未创建测试库数据。
8. 下一步：按优先级修复、补正式断言、PC→手机回归，再做真实设备／真实账户联调；未授权部署生产。

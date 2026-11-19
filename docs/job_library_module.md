# 职位库模块

## 2026-11-09：岗位验证误判与状态修复

扩展0.6.1收窄岗位读取前的验证检测，普通安全说明和等待文案不再拦截真实请求；保留真实验证、登录与限流边界。前端将已有结果与后续失败分开提示，完整JD未取得时不标为详情已更新，预取在验证时停止。调用链、取舍与本地验证见 [修复记录](designs/2026-11-09-job-search-verification-fix.md)。

## 2026-11-09：对话检索批次与历史预览

对话检索每次建立独立批次，所有已加载岗位与完整可用 JD 按版本保存；第 12 个等任意已存岗位可用稳定引用追问，历史返回不重搜。旧详情/分页回包只能归属原批次。插件动作保持 search_jobs/get_job_detail，批次保存和自动分析由平台完成，详见 [本地实现与验证](designs/2026-11-09-chat-job-search-batches-implementation.md)。

## 2026-11-09：对话岗位匹配证据同步

对话卡片在搜索后和现有详情加载成功后保存结果摘要，增加经验、学历、技能与最多 2000 字的 JD 摘录，最多十条并保留总数。完整岗位不改动，也不新增插件动作。后续对话通过独立 Prompt 组件读取，具体调用链与验证见 [对话上下文修复](designs/2026-11-09-chat-job-search-context.md)。

## 目标

职位库是左侧导航中的独立一级模块，固定路由为 `/interviews/jobs`，位于“面经库”之后。用户进入“职位库”后，选择工作城市并输入岗位名称，即可查看 BOSS 直聘当前会话返回的真实岗位卡片和完整职位详情。

页面不要求用户复制职位 URL，不显示扩展执行过程，也不保存 BOSS 登录凭据。首次安装浏览器助手并登录后，日常操作保持为“输入岗位名称 → 搜索 → 点击岗位”。助手未连接时，职位搜索和一键打招呼展示连接提示与重新检测入口；安装说明由用户主动打开，可下载 ZIP 并查看 Chrome 或 Edge 图文教程。网页不根据超时认定插件未安装。

职位库使用独立模块键 `job_library`。管理员可在管理台单独控制其启用状态；关闭职位库不会影响面经库，关闭面经库也不会影响职位库。原有 `/interviews/jobs` 地址保持不变，历史书签可继续使用。

## 2026-11-04：真实岗位长标识的卡片摘要修复

- 根因：插件规范化结果以 securityId 优先作为运行时 id，前端直接持久化该值，而旧卡片摘要把所有字段统一限制为 200 字符；长标识使保存搜索结果/确认摘要的 PATCH 返回 422“卡片字段格式或长度无效”，与求职意图识别无关。
- 契约：摘要优先使用 jobId，再使用原 id/securityId；岗位标识完整保留，上限 8192。标题/公司摘要各 240、薪资/城市各 120，展示摘要按 Unicode codepoint 截取；仍只保存十条摘要与全部结果总数，不修改原岗位、完整 JD 或运行时检索标识。
- 错误反馈：后端按字段校验，超限时指出具体字段与限制；非法更新不部分保存。
- 验证：用真实 FastAPI PATCH 路由和本地 PostgreSQL 保存 count=15、十条各含 1200 字符岗位标识的摘要，并确认目标岗位、重读历史成功；8193 字符标识返回 422 且原状态不变，临时会话已清理。PC/手机各十五条长标识浏览器回归通过，mock API 镜像字段约束并主动验证超限确实拒绝。
- 发布版本 0.4.2 同时包含招呼语 200 字提交保护；本轮只本地验证和打包，未部署生产。

## 2026-11-04：已安装插件误弹安装窗口修复（0.4.1）

- 目标与根因：原先所有消息包括 ping 都进入全局 requestQueue，而网页握手只等待两次 1.8 秒、间隔 160ms。岗位详情预取、BOSS 页面等待或小红书任务可阻塞探测；前端 catch 又把所有失败标成 missing，旧检测还能覆盖新成功。本地实际分发逻辑已复现约 3.78 秒误判。
- 插件调用链：Content Script 转发 → Service Worker 同一来源/通道验证 → ping 直接响应；其他业务继续进入原串行队列并保留原节流。不会通过保活或放开业务并发掩盖问题。
- 网页调用链：Workspace → createJobLibraryConnection（合并检测、序号丢弃旧结果、卸载保护）→ jobLibraryBridge.ping（跨组件在途 Promise 合并）→ 版本化 ready/disconnected 通知使旧探测失效并重新握手。ready 只表示桥接注入，不直接等同后台可用。
- 错误与交互：握手超时 bridge_timeout，业务超时 request_timeout，桥接失联 bridge_disconnected，其他桥接错误 bridge_error；兼容旧版 extension_unavailable，但不认定未安装。连接失败状态统一 unavailable，卡内提供重新检测及查看安装说明；顶部连接按钮重新检测。自动和手动搜索均不自动打开安装窗口。打招呼的连接异常同样只显示提示；收到实际连接成功后确认缺少所需能力时，仍显示升级提示。
- 生命周期边界：失效 Content Script 解除监听，并且只有当前桥接能发 disconnected，避免旧实例晚失败覆盖新 ready。恢复连接不会重放已提交的岗位搜索或发送动作；旧版本插件需要更新/重新加载才能获得 ping 独立响应修复，网页端提示修复兼容旧包。
- 发布物：manifest 和网页下载版本升级为 0.4.1；`scripts/package_boss_extension.py` 生成 `web-ui/public/downloads/find-job-boss-helper-v0.4.1.zip`，九个条目逐字节与源码匹配。旧包保留为历史发布物，不覆盖用户安装目录。
- 验证：扩展 Node 50/50、前端 Node 273/273、Chromium 16/16 通过，覆盖真实探测超时、业务阻塞下即时 ping、来源拒绝、手动失败不误弹、旧回包保护、ready/断线恢复、合并检测、明确点击才打开安装及原有对话求职/定位链路；PC 后检查 390px 无横向溢出。浏览器使用模拟扩展响应，未操作用户 BOSS 登录态或执行真实发送。构建结果见实施计划。
- 本轮仅本地实施、验证和打包，未连接生产或部署。

## 调用链

```text
职位库 JobSearchWorkspace.vue
  -> job-library-bridge.js / window.postMessage
  -> Chrome Content Script
  -> MV3 Service Worker（业务串行、ping 独立响应、只读、来源校验）
  -> 将专用 BOSS 搜索页同步到用户选中的城市
  -> BOSS 已登录页面 MAIN world fetch
  -> /wapi/zpgeek/common/data/city/site.json（城市目录）
  -> /wapi/zpgeek/search/joblist.json
  -> /wapi/zpgeek/job/detail.json
  -> boss-data.js 归一化
  -> 职位卡片 / 详情阅读区
```

浏览器助手安装拦截链路：

```text
进入职位库 / 打开职位检索 / 进入一键打招呼选岗
  -> JobSearchWorkspace.vue 挂载并调用 ping
  -> 当前浏览器未响应
  -> BossExtensionInstallDialog.vue 阻止本次搜索
  -> 下载版本化 ZIP / 打开 Chrome 或 Edge 教程
  -> 用户安装并刷新
  -> ping 返回 connected + version
  -> 解锁真实职位搜索和后续打招呼流程
```

## 交互语义

- 城市：职位库与求职助手职位检索弹窗共用 `JobCityPicker.vue`。界面使用 MIT 许可的 Vue 3 开源组件 `v-region` 提供省市浏览和城市名搜索；实际请求代码来自当前 BOSS 会话的城市目录，扩展不可用时使用经过验证的常用城市代码兜底。
- 搜索：用户选择城市并输入岗位名称，单页请求 15 条。城市选择保存在当前浏览器；切换城市会清空旧结果，分页始终沿用发起本次搜索时的城市。每次主动搜索前会校验 BOSS 搜索页 URL 中的城市代码，只有不一致时才做一次正常页面切换。
- 城市一致性：归一化层只接受响应中明确标注为所选城市的岗位，不用界面所选城市覆盖或猜测原始岗位城市。缺少城市、城市不一致的记录直接丢弃；整批响应均不一致时返回 `city_mismatch`，不展示错误岗位。“全国”搜索不启用单城市过滤。
- 分页：只有用户点击“加载更多岗位”才请求下一页；按稳定 ID 去重，平台不再返回新岗位时结束。
- 详情：搜索完成后读取第一条岗位详情；此后每次点击任何卡片（包括重复点击当前卡片）都会重新请求详情。
- 竞态：页面分别维护搜索与详情请求序号，快速切换岗位时丢弃迟到的旧详情。
- 数据：正式页面没有预览或兜底岗位，扩展不可用、未登录、验证码、访问限制和空结果都有独立状态。
- 复用：`JobSearchWorkspace.vue` 既作为独立一级“职位库”页面，也可通过 `selectionMode` 嵌入求职助手的职位检索弹窗；嵌入模式只额外向父组件发布已加载的完整岗位详情，不改变搜索、分页、刷新详情与错误处理。

## 求职助手导入链路

```text
求职助手“职位检索”
  -> CareerJobSearchDialog.vue
  -> JobSearchWorkspace.vue（selectionMode）
  -> 选择完整岗位详情
  -> job-library-target-role.js 生成标准职位正文
  -> POST /api/career/target-role-profiles
  -> POST /api/career/conversations/{id}/context
  -> CareerContextRail / CareerJobCanvas / 既有四项指标
```

弹窗底部固定显示当前选择与“确认岗位”。未取得完整职位描述时按钮保持禁用；确认后不再进入第二个编辑页。手动粘贴、输入或上传 JD 仍由原有求职资料向导负责，与职位库链路互不替代。

## 稳定数据模型

列表字段包括：`securityId`、`jobId`、岗位名、薪资、经验、学历、城市与商圈、公司、行业、规模、融资阶段、招聘者、技能、福利和原岗位链接。

详情字段包括：职位描述、办公地址、经纬度、技能、福利、招聘者状态、公司信息和本次获取时间。网页只消费归一化字段，不读取 BOSS 原始响应结构。

## 技术取舍

- 使用 Chrome MV3 扩展而不是服务端抓取：BOSS 内部接口依赖真实浏览器会话，服务端无法可靠复用用户登录态。
- 使用 Content Script 桥而不是扩展固定 ID：正式域名和本地开发页面都可直接通信，不要求把扩展 ID 写入部署配置。
- 网页向 Content Script 发送详情请求前，只提取字段白名单并生成普通对象：Vue 响应式 Proxy 不能被 `window.postMessage` 的结构化克隆算法直接复制，列表对象不得原样跨越网页/扩展边界。
- 使用 `chrome.scripting.executeScript({ world: "MAIN" })`：同源请求由 BOSS 页面运行环境发出，浏览器自动处理当前会话；扩展不读取 Cookie。
- 标签页优先复用已加载完成的 BOSS 搜索页，再考虑当前激活、未休眠和最近访问状态。若只有岗位详情等非搜索页，则创建非激活搜索页，不覆盖用户正在浏览的页面。
- 使用稳定归一化层：接口字段变化时只调整扩展映射，不改职位库组件。
- 城市目录与平台代码分层：`v-region` 只负责开源城市选择交互，`list_cities` 返回的 BOSS 九位城市代码才进入真实搜索请求；两者按规范化城市名映射，无法映射时明确提示，不猜测代码。
- 城市选择器按需加载：进入职位库时不下载完整行政区数据，只有用户打开城市面板时才加载，避免增加首次打开耗时。
- 不引入本地 Python、Patchright 或新增后端服务，降低安装与运行负担。
- 不用数据库记录“已安装”：扩展属于浏览器实例而非用户账号；用户更换电脑、浏览器、浏览器配置或手动停用扩展后，数据库标记无法代表当前设备。页面以当前浏览器实时 `ping` 为唯一判定依据。
- 下载包走现有前端静态资源链路，文件名携带 `manifest.json` 版本号；当前不引入应用商店审核流程，也不做旧版本兼容或最低版本拦截。

## 访问限制处理

- 没有扩展：首次进入依赖 BOSS 的页面时显示安装拦截弹窗；关闭弹窗会取消当前动作，页面保留固定的黄色“需要安装助手”入口，避免换电脑或换浏览器后没有恢复路径。
- 未登录：提示用户在已打开的 BOSS 页面完成登录。
- 验证码或安全验证：立即停止本次调用，提示人工处理，不自动重试或绕过。
- `code=37`：用户搜索、读取岗位详情或执行单岗发送前检查时，普通刷新所选 BOSS 标签页一次，并只重试原只读请求一次。刷新失败或重试仍异常时，本岗记录失败且不执行写入；多岗位发送队列继续尝试下一岗，发送进度页对尚未提交消息的失败项保留手动重试。
- HTTP 或接口 `429`：按真实访问频率限制处理，绝不自动刷新或重试，提示稍后再试。
- 不做后台轮询、不读取或替换 Cookie/token、不绕过验证，不因为空结果主动追加请求。
- 城市上下文：只在用户点击搜索且搜索页城市与所选城市不一致时导航一次。响应城市仍不一致时停止展示，不自动追加搜索或连续切换。
- 详情失败：保留列表卡片，允许用户点击当前卡片重新获取。

## 依赖与依据

- Chrome 官方 `scripting`、Content Script、Messaging 与 Tabs API。
- `TerryZ/v-region`（MIT，Vue 3）：复用其 `RegionCityPicker` 的省市浏览和内置搜索交互，不自行维护全国行政区 UI。
- `browser-use/browser-harness` 的 BOSS 页面实测说明，用于核对 2026-05 仍在使用的列表、详情端点和字段。
- `can4hou6joeng4/boss-agent-cli` 的只读优先、用户主动触发和平台风险处理思路；未引入其 Python/Patchright 运行时。

上述开源项目均仅作为技术依据。本项目没有复制自动沟通、批量触达或账号数据处理逻辑。

## 文件职责

- `src/platform_access/navigation_config.py`：职位库一级模块目录、默认启用状态和角色访问能力。
- `web-ui/src/App.vue`：职位库左侧菜单、路由归属、页面标题和独立高亮。
- `web-ui/src/components/JobSearchWorkspace.vue`：职位库 UI、请求状态、分页、详情竞态处理。
- `web-ui/src/components/BossExtensionInstallDialog.vue`：未连接扩展时的前置拦截、ZIP 下载和 Chrome/Edge 教程入口。
- `web-ui/src/boss-extension-onboarding.js`：扩展版本、静态下载地址、教程锚点和连接状态归一化。
- `web-ui/public/boss-extension-guide.html`：无需登录即可查看的 Chrome/Edge 图文安装教程。
- `scripts/package_boss_extension.py`：依据扩展 `manifest.json` 生成确定性的版本化 ZIP。
- `web-ui/src/components/JobCityPicker.vue`：共用城市选择器、`v-region` 适配、热门城市入口与 BOSS 城市代码映射。
- `web-ui/src/job-city-catalog.js`：常用城市代码兜底、BOSS 城市目录合并与校验。
- `web-ui/src/components/CareerJobSearchDialog.vue`：求职助手内的职位库弹窗和固定确认区。
- `web-ui/src/job-library-target-role.js`：将职位库完整详情转换为现有目标岗位 API 的标准字段与可拆解正文。
- `web-ui/src/job-library-target-role.test.js`：验证完整映射、可选字段缺省和缺少职位正文时的拒绝语义。
- `web-ui/src/job-library-bridge.js`：网页侧有超时与错误语义的扩展调用；详情请求将响应式岗位转换为可结构化克隆的纯数据。
- `web-ui/src/job-library-bridge.test.js`：验证 Proxy 岗位及其数组字段转换后可通过 `structuredClone`。
- `browser-extension/job-library/content-script.js`：网页与扩展消息桥。
- `browser-extension/job-library/service-worker.js`：BOSS 标签页复用、限频、同源只读请求。
- `browser-extension/job-library/boss-data.js`：端点构造、字段归一化和错误分类。
- `browser-extension/job-library/tests/boss-data.test.mjs`：离线契约验证。

## 验证

```powershell
.\.venv\Scripts\python.exe scripts\package_boss_extension.py
.\.venv\Scripts\python.exe -m pytest tests/test_boss_extension_distribution.py -q

.\.venv\Scripts\python.exe -m unittest tests.test_navigation_config -v

cd browser-extension/job-library
npm test
node --check service-worker.js
node --check content-script.js

cd ../../web-ui
npm test
npm run build
```

2026-08-24 扩展安装引导验证：生产下载包版本与 `manifest.json` 保持一致，ZIP 根目录可直接作为“加载已解压的扩展程序”的目标；Chrome、Edge 教程和职位库返回路径均包含在前端静态构建中。连接判定使用当前浏览器实时 `ping`，没有新增数据库字段或迁移。

2026-08-24 扩展安装弹窗本地视觉调整：删除页脚右侧的本机登录态说明，将完整图文教程提升为独立浅蓝操作区，并把 Chrome、Edge 教程改为更大、更明确的按钮式入口；下载、检测和教程链接行为不变。本次调整仅在本地验证，未发布生产。

2026-08-24 一键打招呼安全验证恢复：扩展 `0.2.3` 将建立沟通与定制文案提交分开记录。若 BOSS 默认招呼语已发送、定制文案在点击发送前被验证拦截，用户完成验证后可只重发该文案；明确发送失败也可重试，点击后结果未知仍禁止重试。页面按岗位记录状态、失败原因、尝试次数和最近处理时间，桌面弹窗高度调整为视口 `80%`。验证识别不再匹配普通安全说明。本次仅完成本地修改与验证，未发布生产。

2026-08-24 扩展能力门禁与失败重试交互：扩展升级至 `0.2.4`，`ping` 返回 `retry_greeting_message`、`greeting_submission_state` 两项能力。发送进度页使用批次级失败操作条，重试按钮与当前选中岗位解耦，并在失败后自动聚焦失败项。旧版助手留下的验证失败只允许先更新，不执行无法确认阶段的盲目重发。最初采用的全局 `outdated` 拦截已由下条兼容修复替代。本次仅本地验证，未部署生产。

2026-08-24 Chrome Tabs 瞬时锁定兼容：扩展升级至 `0.2.5`。复用或创建 BOSS 聊天标签页时，仅对 `Tabs cannot be edited right now` 进行有限退避重试；其他标签页错误仍立即上抛。若锁定持续存在，返回 `browser_tab_busy`、`submissionState=not_submitted`、`retryMode=message` 和 `defaultGreetingSent`，Web UI 因此可以在不重复建立沟通关系的前提下安全重发定制文案。本次仅本地验证，未部署生产。

2026-08-25 一键打招呼逻辑消息回执：扩展升级至 `0.2.6`。`service-worker.js` 在点击发送前保存本人消息文本序列，点击后通过受限的 outgoing row 选择器和 MutationObserver 持续采样；`boss-greeting.js` 负责空白/不可见字符归一化、逻辑序列增量判定和跨短暂 DOM 重绘的稳定计时。只有新增逻辑消息、输入框清空、连续稳定 2 秒且没有明确失败时才继续 Web UI 串行队列；历史同文案与真正未知结果继续停止且不自动重试。依赖仍为 Chrome MV3 `tabs`、`scripting` 和 BOSS 当前聊天 DOM，没有新增服务端接口、数据库字段或第三方依赖。本次仅本地验证，未部署生产。

2026-08-26 一键打招呼逐岗直接触发：扩展升级至 `0.2.7`。设计目标是消除 BOSS 页面未提供稳定回执时对多岗位队列的阻塞；WebUI 对每岗直接调用 `sendGreeting`，扩展完成输入与按钮点击后立即返回 `submitted`，不再创建 MutationObserver 或等待送达状态，单岗失败也只落本岗记录并继续下一岗。技术取舍为继续复用一个聊天标签页并保持串行，避免并行导航覆盖输入内容以及集中触发带来的账号限制风险；调用链为岗位队列 → 单岗详情/会话检查 → friend/add → 聊天页写入并点击 → 下一岗。新增 `greeting_fire_and_continue` 能力门禁和 `0.2.7` 下载包，不新增服务端接口、数据库字段或第三方依赖。WebUI 全量 135 项、扩展全量 21 项与 Service Worker 语法检查通过，Vite production build 通过（仅保留原有大包体积提示）；仅本地验证，未部署生产。

2026-08-24 已安装扩展误弹窗回归修复：连接归一化现在只用 `connected` 判断安装/连接状态，安全重试能力通过 `greetingRetryReady` 单独表达。`JobSearchWorkspace` 初始化采用静默检测，单次失败不会自动打开安装窗口；只有用户主动搜索且仍未连接时才拦截。桥接 `ping` 遇到 `extension_unavailable` 会等待 160ms 后再尝试一次，以覆盖 Manifest V3 后台冷启动。普通搜索和首次招呼发送兼容已安装旧版，只有安全重试动作要求新版能力。本次仅本地验证，未部署生产。

2026-08-24 职位检索弹窗本地布局调整：移除外层重复的“选择当前会话的目标岗位”标题，保留面包屑与可访问名称；桌面端宽高均按视口的 `80%` 展示，遮罩层保持水平与垂直居中，列表和详情继续在内部滚动，底部岗位确认区保持完整可见。本次调整仅完成本地验证，未发布生产。

2026-08-21 回归验证：修复点击岗位时直接把 Vue Proxy 传给 `window.postMessage` 导致的 `DataCloneError`。详情请求仍在每次选中卡片时触发，扩展协议与 BOSS 详情端点不变。

2026-08-23 会话恢复验证：`code=37` 与 `429` 保持拆分；搜索、详情和发送前预检统一为按操作单次普通刷新和单次只读重试，不再使用跨操作冷却。安全验证、登录失效、真实限流和刷新后失败均不会自动循环重试。

2026-08-21 城市检索回归：城市目录切换为当前 `/wapi/zpgeek/common/data/city/site.json`，修正深圳城市代码为 `101280200`。搜索前同步 BOSS 搜索页城市，响应后按真实 `cityName` 严格校验；北京请求不会再展示上海或未知城市岗位。验证仅使用离线契约与前端构建，没有对真实账号发起自动化回归请求。

真实端到端验证需要一次性在 Chrome 加载扩展并登录 BOSS；不通过测试代码伪造登录态或真实岗位结果。

PC 端导航验证还需确认：“面经库”和“职位库”分别只在自身路由高亮；面经库中不再显示职位入口；职位库中不再显示返回面经库按钮。

2026-08-22 生产发布记录：扩展归一化与会话恢复测试 `7 passed`，前端职位协议与映射包含在 `20 passed` 中，生产构建通过；正式站点资源已包含“职位库”和“职位检索”，Career 目标岗位写入接口已上线并受登录保护。BOSS 实时列表与详情继续由用户 Chrome 中已登录会话提供，服务器不持有 BOSS 凭据。


## 2026-11-06 手机能力提示与列表状态

- 目标：维持 PC 实时检索／选岗；手机明确现有扩展能力边界，避免进入安装和反复检测流程。
- 调用链仍为 JobSearchWorkspace → 原 job bridge；`platform-capabilities.js` 只判断移动浏览器，不用窄窗口禁用 PC 能力。移动浏览器不启动 bridge 初始化，显示电脑端操作说明。
- 列表／详情切换复用现有数据，返回显式恢复列表滚动；筛选和选择不销毁。岗位选择 Sheet 调整高度、主体滚动与确认区，匹配报告手机单列但不改变评分数据。
- 验证：47 项浏览器回归覆盖原连接恢复、长 securityId、搜索状态与确认岗位；另覆盖手机提示和窄 PC。真实招聘站点、扩展和实际发送没有在本轮联调。
- 详见 [适配记录](mobile_ui_adaptation.md)。

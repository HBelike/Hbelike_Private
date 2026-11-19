# 猎聘与前程无忧接入调研及设计建议

调研日期：2026-11-05（北京时间）。2026-11-15 更新：首期只读搜索与共用职位工作区已进入本地实现；多平台消息发送仍待各平台真实账号能力验证，不视为已完成。

用户已明确目标：类似当前插件，在我们的平台获取猎聘、前程无忧职位信息，并向对应招聘者发送消息。仅投递简历、生成可复制文案或跳转 App 均不算完成消息发送目标。

## 当前落地状态（2026-11-15）

- 职位工作区增加 BOSS 直聘、猎聘和前程无忧平台入口；猎聘接官方只读搜索，前程无忧显示“授权后开放”。BOSS 仍走原搜索与岗位详情链路。
- 猎聘令牌在扩展 `options.html` 设置页保存到 `chrome.storage.local`；网页只读取令牌是否存在，扩展后台直接请求官方搜索服务。
- 猎聘搜索结果统一归一到现有岗位卡片字段；完整 JD 仅在官方搜索结果包含正文时显示。缺少 JD 时不调用 BOSS 详情接口，也不进入 BOSS 招呼多选。
- 猎聘消息发送、简历投递、真实账号现场验收均未实现；前程无忧无数据接入。方案与 UI 现状见 [多平台职位搜索模块说明](../modules/job-search-platforms.md)。
- 本次本地改动没有访问招聘平台账号、发送消息或连接生产环境。

## 结论

建议复用当前 PC WebUI、Chrome MV3 扩展和招呼语生成服务，增加按平台划分的适配层。用户在我们平台完成搜索、完整 JD 阅读、文案审核和逐岗发送，平台操作由本地已登录浏览器执行。

猎聘 PC 聊天已有公开源码实现线索，优先验证后编写浏览器适配器；官方 MCP 不能携带自定义消息，仅可作为后续搜索等辅助通道。前程无忧必须先确认求职者 PC 网页的文本消息通道；当前证据不足，不能承诺增加 DOM 适配器便可完成。若该通道仅限 App，需要另行评估本地 App 执行器，不能用简历投递替代目标。

## 证据分级与平台事实

“已确认”指本次读取了官方页面、真实工具 schema 或相关源码；“源码线索”不代表已在本项目账号跑通；“待账号验证”不能显示为可执行能力。

| 项目 | 猎聘 | 前程无忧 |
| --- | --- | --- |
| PC 搜索 | 官方 MCP 有 user-search-job；网页也有搜索路径 | 官网搜索导航指向 we.51job.com/pc/search |
| 完整 JD | MCP 当前没有独立详情工具；搜索结果是否含完整 JD 待授权验证；可研究扩展读取岗位页 | 公开搜索页本次只有外壳/模板；真实卡片及详情需登录验证 |
| 简历投递 | MCP 有 user-apply-job，必填 jobId、jobKind，无 message、resumeId 或附件参数 | 官方帮助及搜索页模板包含申请、选择在线简历、同步附件等流程；提交链路待验证 |
| PC 自定义消息 | Boss-Claw 有“聊一聊—填当前文案—点击发送”的实际代码；未在本项目账号验证 | 当前官方 PC 沟通组件生成微信/App 二维码；尚未核实其他求职者 PC 文本消息入口 |
| App 沟通 | 官方 Agent 授权页建议即时沟通回 App 完成 | 官方客户端下载页明确支持求职者与 HR 在线沟通 |
| 官方求职者接入 | 已发现并探测 MCP 服务 | 本次未找到普通求职者可用的公开 MCP、CLI 或 API 文档 |

### 猎聘官方服务

当前可用的官方授权入口是 [猎聘 Agent 授权页](https://www.liepin.com/mcp/auth)。本地 HTTP GET 返回 200；该页直接链接 [liepin-tech-2026/liepin-cil](https://github.com/liepin-tech-2026/liepin-cil)。仓库名是 cil，README 中部分命令仍使用 cli 拼写，安装地址不能只按 README 猜测。

向 [官方 MCP 服务](https://open-agent.liepin.com/mcp/user) 发出未携带凭证的 initialize、tools/list 请求均返回 200，本次返回 14 个工具。工具列举可用，不代表职位搜索或投递无需认证。

- user-search-job 支持职位名、城市、公司、薪资、经验、学历等字段，page=0 表示第一页。
- user-apply-job 只要求 jobId:number 和 jobKind:string。[官方 CLI 投递 schema](https://github.com/liepin-tech-2026/liepin-cil/blob/858a62bd839d490e8745b7503961e4676a54b9d7/schemas/apply-job-input.json) 进一步列出 jobKind 的 1、2 类型；实际类型必须来自搜索结果。
- 当前工具列表没有自定义消息发送、聊天记录、投递进展或独立岗位详情工具。
- 官方授权页说明搜索、查看、简历更新、投递共用 60 次/分钟配额，Token 有效期 90 天；这不是每分钟可发送 60 条招呼语的保证。
- 官方 FAQ 表明 HR 回复通过 App 推送和短信触达，建议即时沟通回 App，Agent 查询投递进展属于后续计划。[官方 FAQ](https://www.liepin.com/mcp/auth#faq)

官方 CLI 的 [job.py](https://github.com/liepin-tech-2026/liepin-cil/blob/858a62bd839d490e8745b7503961e4676a54b9d7/liepin_cli/cli/job.py) 实际调用 POST /mcp/search-job、POST /mcp/apply-job；[客户端](https://github.com/liepin-tech-2026/liepin-cil/blob/858a62bd839d490e8745b7503961e4676a54b9d7/liepin_cli/core/client.py) 使用 x-user-token。可用于交叉核对协议，但不能把这些路由视为已获得长期兼容承诺的开放 API。

### 猎聘 PC 聊天线索

[Boss-Claw 猎聘模块](https://github.com/YanQuan-dozzy/Boss-Claw/blob/64bddfdbc20e0c24dd663fa9bf602e07d401c318/desktop-app/camoufox/platforms/liepin.py#L284-L360) 实际包含定位当前会话、填写当前岗位文案、点击发送、检查新增本人消息与输入框清空的代码；[共享 DOM helper](https://github.com/YanQuan-dozzy/Boss-Claw/blob/64bddfdbc20e0c24dd663fa9bf602e07d401c318/desktop-app/electron/preload/liepin-im.cjs) 处理页面聊天组件和多个窗口。

这是实现定制招呼语的有效研究线索，与官方 MCP 只支持投递并不矛盾。正式接入前必须确认当前账号、岗位类型、招聘者身份下确实存在对应输入框，并验证会话属于目标岗位。不可把“出现聊天入口”“已打开会话”或平台默认招呼计作当前定制文案已提交。

### 前程无忧网页链路

[官方首页](https://www.51job.com/) 导航链接到 [PC 搜索](https://we.51job.com/pc/search)。[官方帮助](https://help.51job.com/) 与 [搜索投递分类](https://help.51job.com/qa.html?flag=true&sign=4) 包含搜索、快速投递、简历中心、我的申请等流程。

本次读取 [公开搜索页](https://we.51job.com/pc/search?keyword=Java&jobArea=020000) 的模板，发现在线简历选择、立即申请、同步附件简历、发送和应届生平台跳转等元素，但未取得真实岗位列表。模板存在不等于已验证提交成功。

[官方客户端下载页](https://app.51job.com/) 的在线沟通宣传只能证明客户端能力。本次还直接读取了 PC 搜索页引用的 [官方沟通组件脚本](https://fccdn.51jobcdn.com/we/static/js/app-43a4fc3e-14f3a1ae.js)：它按 allowChat 渲染微信扫码或 App 沟通提示；微信二维码目标是 https://we.51job.com/wxbridge?page=chat，参数包含招聘者和岗位身份。[个人中心消息提示组件](https://fccdn.51jobcdn.com/we/static/js/app-792f8fa3-1747d902.js) 同样提供二维码。这足以确认当前这些 PC 入口采用扫码引导，不能由此证明全站不存在其他网页聊天通道。

本次读取的 [官方路由脚本](https://fccdn.51jobcdn.com/we/static/js/app-06837ae4-55eda667.js) 包含 PC、移动搜索和我的申请路由，未在该路由表找到聊天页；无真实业务参数访问 wxbridge 仅得到 SPA 外壳，不能确认登录后最终落点。移动网页请求也未取得足以证明可发送文本的内容。三个脚本的摘录位置、SHA-256 和北京时间抓取时间已保存在 [.firecrawl 证据索引](../../.firecrawl/2026-11-05-51job-static/evidence.json)，便于后续对照平台版本。

公开项目 [jobpilot 的 51job 模块](https://github.com/YouNianShao/jobpilot/blob/bc75591312214e57854b5fa61d611184dfac04ec/src/jobpilot/platforms/job51.py) 只有申请及进展检查；[OpenCLI 的 51job 能力](https://github.com/jackwener/OpenCLI/blob/97dc0cd5214b9602bcda78d7422f231ee8a803e9/docs/adapters/browser/51job.md) 为搜索、热榜、详情、公司信息。它们不能作为已实现求职者网页消息发送的证据。第三方 [平台适配记录](https://github.com/3155498108/job-auto-apply/blob/main/skill/job-auto-apply/references/platform-adapters.md) 的二维码实测仅作交叉线索，核心判断以官方页面脚本为依据。

[平台现行服务条款](https://we.51job.com/service) 对第三方工具接入有约束。真实账号技术验证和产品化接入应分别核对适用条件；开源脚本能点击不代表已取得平台接入许可。

## 开源方案对比

维护时间、许可证和动作能力均以本次固定源码版本为准。

| 项目及版本 | 维护与许可证 | 实际用途 | 复用取舍 |
| --- | --- | --- | --- |
| [猎聘官方 CLI 858a62b](https://github.com/liepin-tech-2026/liepin-cil/tree/858a62bd839d490e8745b7503961e4676a54b9d7) | 最后提交 2026-04-13；未发现 LICENSE 或许可证字段 | 官方求职端搜索、简历管理、投递，无定制消息参数 | 参考服务协议和参数；不直接复制、打包该源码 |
| [Boss-Claw 64bddfd](https://github.com/YanQuan-dozzy/Boss-Claw/tree/64bddfdbc20e0c24dd663fa9bf602e07d401c318) | 最后提交 2026-10-31；声明 Apache-2.0，相关文件标注参考非商业许可项目，来源许可需追溯 | 猎聘 PC 自定义聊天实现最接近目标；51job 以投递为主 | 参考验证用例与平台交互，自行实现薄适配；不整体搬入 Electron/Camoufox |
| [job-hunter b7dcfaf](https://github.com/Liooo0/job-hunter/tree/b7dcfafb23200f1c15d82fe32f199c82d2d15ffd) | 最近推送 2026-11-01；MIT；小型个人项目 | 51job 卡片投递、猎聘投简历，非逐岗定制聊天 | 可参考 DOM 与状态判断；不照搬重复点击逻辑或 DrissionPage 运行时 |
| [codex-job-search-china 63cd08e](https://github.com/Kerwin-k/codex-job-search-china/tree/63cd08e203c1c169441425bc96bfefdfbd7bb74f) | MIT；README 明示迁移后仅离线验证 | [51job DOM 与 JSON-LD 解析](https://github.com/Kerwin-k/codex-job-search-china/blob/63cd08e203c1c169441425bc96bfefdfbd7bb74f/plugins/job51/skills/search-51job-jobs/scripts/playwright/browser-runtime.js) | 参考字段归一与测试样例；不是可直接验收的适配器 |
| [get_jobs 36bf5f9](https://github.com/loks666/get_jobs/tree/36bf5f986fad93374d77b99205c7684f8450874a) | 最后提交 2026-11-03；PolyForm Noncommercial；README 明示 51job 停止维护 | 猎聘点聊一聊并使用平台预设招呼；51job 批量投递 | 仅作流程线索；不能把旧 MIT 状态用于当前代码 |
| [BossHunter 338d2b4](https://github.com/shengjidaguai-china/BossHunter/tree/338d2b4b45b7d531fcc9093760e192da611ce0e7) | 最后提交 2026-11-03；PolyForm Noncommercial | 猎聘、51job 采集/评分/生成，能力表未开放发送 | 参考显式能力声明；不作为已实现自动消息的证明 |

同名 npm 包 @viyzhu/liepin-cli 面向 lpt.liepin.com 招聘者端，与官方求职 CLI 不同，不适用于本需求。

## 三种接入方案

| 方案 | 优点 | 成本与限制 | 判断 |
| --- | --- | --- | --- |
| 现有扩展加平台适配器，优先走 PC 网页搜索与聊天 | 复用本地会话、选岗、文案审核及现有发送入口，最接近用户目标 | 猎聘须验证当前聊天组件；51job PC 消息通道尚未确认 | 推荐，以真实消息通道验证为前提 |
| 后端统一接 MCP 与网页采集 | 服务端统一调用方便 | 51job 没有已确认的官方接口；需额外账号凭证管理及浏览器环境，不能解决 App 聊天边界 | 不作为当前首选 |
| 独立本地桌面工具或 App 执行器 | 可进一步探索仅 App 可用的聊天，结果仍由我们平台呈现 | 增加运行时、设备连接、安装步骤和新的交互验收；公开脚本不能证明已可稳定运行 | 仅确认网页无法完成后再评估 |

推荐方案平台标识为 boss、liepin、51job；核心能力为 search_jobs、get_job_detail、send_message。apply_resume 是不同动作，本轮不作为替代交付。猎聘 MCP 若用于辅助搜索，也必须继续由经过验证的 PC 消息适配器完成真正的定制文案发送。

## PC 交互建议

沿用“一键打招呼”的三步流程，选择阶段加入平台选择器。首期每批固定一个平台，继续保持 1–10 个岗位；切换平台前结束或明确取消当前批次，避免把不同账号和动作混在一起。

1. **选择岗位**：选择 BOSS、猎聘或前程无忧，展示该平台连接状态、搜索结果和完整 JD。扩展连接与平台账号登录状态分别展示。
2. **审核招呼语**：复用当前文案编辑器、简历证据与岗位关注点；只有取得完整 JD 且平台消息能力已验证时才可进入真实发送。
3. **发送进度**：逐岗发送用户审核的当前文案，显示已触发消息、结果待核对、已跳过、失败或已停止；有明确回执才进一步显示已确认。

动作文案必须准确：聊天能力可用时显示“发送招呼语”；仅出现二维码时显示“当前网页尚不能发送，请处理平台沟通入口”，不能显示发送成功，也不能自动替换成投递。复制文案或转到 App 只是辅助操作，不能作为本轮消息发送验收通过。

生成依据继续使用当前绑定简历。如果建立新沟通会连带发送平台默认招呼或平台简历，须在最终确认前说明平台的实际行为，并分别记录默认消息与本次定制文案。不能把平台的默认文案当作我们生成的文案已经送出。

## 数据与接口设计

### 岗位身份

保留现有 title、company、city、description、recruiter、sourceUrl 等展示字段，增补以下字段，不把 BOSS 的 securityId、bossId、lid 强加给其他平台：

~~~javascript
{
  platform: 'liepin',
  jobId: '平台原始岗位ID',
  jobKey: 'liepin:平台原始岗位ID',
  sourceUrl: '真实岗位链接',
  description: '取得的完整JD',
  adapterData: { jobKind: '搜索返回的原始类型' },
  actionAvailability: {
    send_message: 'unverified'
  }
}
~~~

jobKey 用于勾选、缓存、生成结果关联和批次内去重；平台参数存于 adapterData，由对应适配器解释。城市 UI 共享城市名，平台内部代码分别映射，不能继续把 BOSS 九位城市编码传给猎聘或 51job。分页起点也由适配器转换。

### 能力状态

区分三层信息：扩展安装版本实现了哪些动作、当前平台账号是否就绪、当前岗位能否执行该动作。ping 继续轻量响应；登录态与岗位预检由独立操作读取，不把全平台网络检测塞进 ping。

动作状态建议为 available、unavailable、unverified、requires_mobile、requires_external_application。只有 available 可以进入自动执行队列。未知能力不能靠按钮文案推断可用，校招或企业外站网申明确转原站处理。

### 桥接协议

现有 search_jobs、get_job_detail、preflight_greeting、send_greeting、retry_greeting_message 增加 platform，缺省继续解释为 boss，便于现有入口逐步迁移。新增 get_platform_state 读取对应平台就绪状态；需同时更新 Content Script 的动作白名单，避免后台尚未收到请求就被桥接层丢弃。扩展内部由 registry 根据 platform 分发，平台模块各自负责数据归一、预检和单次消息执行。

生成接口 job 增加 platform，响应 job_key 与 UI 的 jobKey 保持一致；Prompt 中固定“BOSS直聘”的场景改为真实平台信息。200 字继续作为产品初期的文案上限，不能声称两个新平台的原生上限也是 200；若实测更低，应按该平台限制提示修改。

### 执行结果

~~~javascript
{
  platform: 'liepin',
  jobKey: 'liepin:岗位ID',
  action: 'send_message',
  submissionState: 'not_submitted',
  outcome: 'failed',
  reasonCode: '需要人工核对的具体原因'
}
~~~

submissionState 分为 not_submitted、submitted、confirmed、unknown；outcome 同时说明失败、跳过或人工处理原因。submitted 只表示本次定制文案已点击提交；明确的业务成功响应或可靠页面消息结果才能提升为 confirmed，HTTP 200 本身不够。defaultGreetingSent 单独记录默认招呼。后续若扩展投递，应另设 applicationState、attachmentState 及实际附件身份，避免投递成功而附件失败时重复申请；不复用消息成功状态。

每次动作仅执行一次。只有明确未提交的项目允许用户重试；写操作超时且结果未知时先核对平台记录，不能在 MCP 与 DOM 之间自动切换重发。停止剩余发送只取消未开始的动作，不宣称撤回已提交消息或简历。

## 扩展与服务边界

~~~mermaid
flowchart LR
  UI[选择平台与岗位] --> REVIEW[审核文案或投递简历]
  REVIEW --> BRIDGE[现有网页扩展桥]
  BRIDGE --> REGISTRY[平台适配器]
  REGISTRY --> BOSS[BOSS 页面]
  REGISTRY --> LP[猎聘 PC 搜索与聊天]
  REGISTRY --> JOB51[前程无忧搜索与待验证消息通道]
  UI --> JD[完整 JD]
  JD --> GEN[现有招呼语生成服务]
  GEN --> REVIEW
~~~

- BOSS 使用现有浏览器适配；猎聘与前程无忧各自实现搜索、详情和消息适配，继续复用本地浏览器登录态。
- 猎聘 MCP 是可选的辅助搜索路线：只有网页搜索不满足需求时再决定接入。若采用，使用官方 TypeScript SDK 的 Streamable HTTP 客户端并做浏览器构建验证，避免自写完整 MCP 协议。[官方客户端文档](https://ts.sdk.modelcontextprotocol.io/v2/clients/connect)
- 扩展 Service Worker 在声明相应 host_permissions 后可请求远程服务；Content Script 的跨域限制不同，因此可选 MCP 网络请求放后台。[Chrome 官方网络文档](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests)
- 可选 MCP Token 通过官方授权流程取得，在扩展专用设置中管理，仅后台用于指定的只读搜索；网页只接收连接状态。其简历修改和投递工具不因接入搜索而自动暴露到消息业务。
- 新平台网页读取优先使用可见 DOM/页面公开结构化字段；需要 MAIN world 时按实际站点情况注入。脚本注入需相应站点权限。[Chrome scripting](https://developer.chrome.com/docs/extensions/reference/api/scripting)
- 候选域名包括 www.liepin.com、open-agent.liepin.com、we.51job.com、jobs.51job.com；最终权限以实际观察到的登录/岗位/聊天页面为准，不预先把所有子域和跳转站点加入。
- MCP 客户端、平台适配和页面读取脚本均须进入扩展构建产物；当前 ZIP 打包清单是固定文件列表，需要同步更新并验证下载包内模块完整。SDK 版本及许可证在采用的发行版上锁定核对。

## 现有代码的落点

| 位置 | 调整内容 |
| --- | --- |
| web-ui/src/components/JobSearchWorkspace.vue | 平台选择、对应连接状态、城市与搜索请求；保留多选和完整 JD 门禁 |
| web-ui/src/components/CareerGreetingDialog.vue | 平台消息能力门禁、单次确认锁、默认消息与定制消息的执行结果与进度 |
| web-ui/src/career-greeting-preview.js | 平台岗位键、定制消息提交阶段、编辑后校验状态失效 |
| web-ui/src/career-greeting-client.js | 生成请求增加平台、取消时停止继续调度生成 |
| web-ui/src/job-library-bridge.js | platform 参数、get_platform_state、受约束的跨边界数据 |
| web-ui/src/boss-extension-onboarding.js | 扩展连接与各平台能力分开表达，调整面向多平台的提示 |
| web-ui/src/job-city-catalog.js | 城市名与平台编码分离，不复用 BOSS 编码发起其他平台搜索 |
| web-ui/src/job-library-target-role.js | 真实来源平台名称与链接，继续复用 JD 导入能力 |
| src/career_assistant/greetings.py、web/router.py | 平台字段、Prompt 场景与响应键，修复候选人事实校验范围 |
| browser-extension/job-library/service-worker.js | 轻量分发；新平台动作与连接状态接入 registry |
| browser-extension/job-library/content-script.js | 动作白名单增加 get_platform_state，完整转发 platform 和岗位参数 |
| browser-extension/job-library/manifest.json | 经验证的精确域名与必要设置页面 |
| 新平台适配文件 | 建议 platform-registry.js、liepin-data.js、liepin-page.js、liepin-greeting.js、job51-data.js、job51-page.js、job51-greeting.js；消息文件以能力验证通过为前提，可选 MCP 另行增加 |
| scripts/package_boss_extension.py | 同步新增模块或构建产物清单，验证版本 ZIP |
| src/career_assistant/job_search_cards.py、web-ui/src/job-search-card.js | 若同时接入对话求职卡片，必须扩展当前严格字段白名单并保留平台身份 |

首期将多平台接入独立职位库和现有打招呼入口。对话中的自然语言搜索卡片若同步支持多平台，应作为明确的后续范围，不能只给前端加 platform 而遗漏后端白名单。

## 落地次序与验收

### 第一阶段 先确定真实可执行动作

在用户本人完成浏览器登录后，对猎聘企业岗位、猎头岗位和 51job 普通社招岗位分别读取一条完整 JD，核对岗位 ID、招聘者和消息入口。前程无忧需额外核对“我的消息/沟通”和详情页入口：是求职者 PC 可编辑会话、App 二维码，还是招聘者端功能，不能混用。

只读阶段不点击“聊一聊”“立即沟通”“投递”等可能建立关系、发送默认消息或提交简历的按钮。需要打开新会话或验证提交时，将其作为一次真实动作，先向用户展示具体岗位与最终文案/简历，由用户明确执行该单条验证。

验收产物是字段样例、当前招聘者/岗位会话定位方式与消息能力表。猎聘和 51job 的 send_message 分别记录可用、需要 App 或未验证；能取得岗位但不能提交定制文本，属于只读部分完成，不算平台打通。

### 第二阶段 完成共用能力层

将 BOSS 包装进相同适配接口，补平台身份、城市映射、消息审核和执行状态；同时修复当前已复现的事实检查混入 JD、重复确认入口、编辑后仍显示已校验、生成取消仅忽略结果以及 warnings 不展示的问题。

验收覆盖三个平台相同岗位 ID 不冲突、Content Script 白名单和 Service Worker 分发完整、旧 BOSS 入口正确分发、200 字产品限制、确认期间不重复进入、取消后不继续领取生成任务。只使用模拟响应即可完成这些检查。

### 第三阶段 分平台开通动作

优先接猎聘 PC 搜索、JD 和定制消息；第一条用户确认的真实文案提交通过后，再验证多岗位串行与已沟通岗位处理。前程无忧只有确认 PC 消息通道可用后才实现相同链路；如果确认仅 App 可用，则记录具体限制，转入独立执行通道评估，不用投递按钮充当发送按钮。

验收覆盖新/已沟通岗位、已有草稿、多会话/招聘者定位、平台默认消息、定制消息一致性、过期岗位、登录失效、验证页、限流、站外岗位、仅二维码沟通、点击后结果未知。插件下载 ZIP 必须与本地源码/构建产物一致。

### 第四阶段 PC 交互确认后再做手机页面适配

按项目规则先完成 PC 选岗、审核和执行交互验收，再单独适配手机页面。App 自动化属于独立执行通道，不能与 WebUI 手机适配混为一项。

## 已确认目标与未决技术路线

目标已经明确为两个平台的“获取职位信息并发送消息”，不再将“先投递、后补聊天”作为默认范围。推荐先验证猎聘 PC 聊天和前程无忧求职者网页消息能力，再按现有插件模式完成具备真实通道的平台。

如果前程无忧的 PC 入口确实只提供二维码，要实现用户目标还需要一个可控的本地 App 执行通道：我们平台仍负责选岗与最终文案，插件/本地连接负责把明确任务交给用户设备执行，执行结果回传同一进度页。设备配对、平台岗位与会话映射、精确文本输入和提交结果均需另做可行性验证，当前不将其写成已经跑通的现成能力。

### 仅 App 可发消息时的候选结构

可评估“网页 → 现有扩展 → Native Messaging 本地连接程序 → Appium UiAutomator2 → 用户已登录的前程无忧 Android App”。Chrome/Edge 的 Native Messaging 是现成的扩展与本地程序通信机制；UiAutomator2 是成熟的 Android 原生/混合应用自动化驱动。[Chrome 文档](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging)、[Edge 文档](https://learn.microsoft.com/en-us/microsoft-edge/extensions-chromium/developer-guide/native-messaging)、[UiAutomator2 官方概览](https://appium.github.io/appium-uiautomator2-driver/latest/overview/)

该结构是技术候选，不是51job专用的现成消息接口，也不能假定用户已有 Android 设备。实际最低验证项为：岗位 ID 能否进入对应 App 岗位或唯一会话、招聘者身份是否一致、审核后的中文和链接能否原样填入、单次提交状态能否辨认。若只能通过岗位名模糊搜索且无法唯一确认目标，不满足一键发送验收。

本地连接只接收当前确认的一条任务和最终文本，结果回到同一发送进度页；首期仍保持串行。采用前还需确定真实设备或模拟器、驱动版本组合及安装成本。不能把复制文案、展示二维码或只打开 App 记成发送完成。该方案会增加本地安装依赖，因此只有在网页消息通道确认不可用且用户接受此接入成本后才进入实施。

## 本次研究记录

- 已核查当前工作区的 UI、生成服务、扩展消息、发送、城市、卡片及 ZIP 打包链路。
- 已探测猎聘公开 MCP 的 initialize、tools/list，只列举工具，没有调用用户业务工具。
- 已读取官方网页和固定版本源码；没有登录平台、读取私人简历、投递岗位或发送消息。
- 研究缓存位于 .firecrawl/2026-11-05-liepin-*、.firecrawl/2026-11-05-51job-*、.firecrawl/2026-11-05-oss-sources/；目录已被项目忽略。
- 本轮新增本设计建议文档，未改业务代码、未提交 Git、未连接生产或部署。

# 移动端简历与职位导入方案

日期：2026-11-09。根据用户截图、本地源码和官方文档／GitHub 交叉核对。**现有资料 Sheet 排版、统一入口、公开职位链接与第二阶段 PWA 分享接收均已在本地实施；公开岗位搜索仍为后续方案。** 调用链、验证证据及边界见 [第一阶段实施记录](2026-11-09-material-import-implementation.md) 和 [PWA 分享接收记录](2026-11-09-pwa-share-import.md)。

## 1. 截图对应的 UI 问题与本轮修正

截图实际是资料 Sheet 的第 2 步“目标岗位”。原实现将资料弹层强制铺满可视高度，正文 textarea 保留 PC 的 13 行和最小高度；主体 flex 拉伸形成大块留白，footer 的“上一步”和嵌套的两个按钮分别参与 flex，导致操作高度、行列不一致。

本轮沿用现有主题和 API，PC 保留双列表单。紧凑宽度下：

- 弹层按内容收缩，最大高度受 VisualViewport／dvh 约束，正文区域局部滚动，保留底部操作。
- 标题缩为“选择或上传简历”“添加目标岗位”，说明文字保持可读字号。
- “上传岗位截图或文件”前置到公司／岗位字段之前，让已有入口先被看见；仍调用原 parse-file，不增加模型调用。
- 正文高度约束在 140–200px；输入较长时自行滚动，不用空白占据整页。
- 两个次操作等宽同排，确认操作独占下一排；长文件名允许换行。

涉及 `CareerContextSetupDialog.vue`、`mobile.css`，新增 `e2e/context-material-layout.spec.js` 并接入专项门禁。保持之前已完成的会话／草稿／附件保留、忙碌保护及原文件格式限制。

## 2. 移动 Chrome 的能力边界

Google 的标准 Chrome 扩展安装说明面向桌面；手机上“Add to Desktop”是把扩展加入电脑端，不能作为普通手机 Chrome 执行现有 MV3 扩展的方案。[Chrome 官方帮助](https://support.google.com/chrome/answer/2664769?hl=en)

普通网页不能直接读取另一个招聘标签页或招聘 App 的页面和登录状态；这与用户是否已在手机登录招聘站是两件事。应由用户主动交付文件、图片、文字或 URL，或由服务端读取独立可访问的公开页面。[MDN 同源策略](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Same-origin_policy)

文件选择是标准 Web 能力，不需要浏览器扩展。`input[type=file]` 可接收文件，图片入口可按支持情况提供相册／相机；多文件需要显式启用 multiple 并配套后端，不能把现有单文件解析宣称为多图导入。[MDN 文件输入](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/input/file)

## 3. 简历获取：直接复用当前能力

| 入口 | 用户操作 | 当前能力／边界 |
| --- | --- | --- |
| 已有简历 | 登录同一账户，选择此前已确认的基准简历 | candidate-profiles 已存在；复用已保存的简历资料，不依赖手机插件 |
| 手机文件 | 将 PDF／Word 放到手机可选文件位置后上传 | candidate-profiles/parse 已存在；本地文档解析后预览、修改、确认 |
| 简历图片 | 上传手机相册中的清晰简历截图 | 已有图片 accept 和服务端图片解析；OCR 结果必须经用户确认 |
| 第三方在线简历 | 用户先导出文件或提供截图／正文 | 本平台网页不能直接读取招聘 App 私有简历；不声称可无感导出 |

当前基准简历保存的是用户确认后的资料版本／摘要；原附件使用临时解析流程，不把“PC 上传过”解释为原文件已自动云同步。简历助手历史记录的原文件保存属于另一个功能，不能与基准资料混为一谈。

当前调用链：资料 Sheet → `/api/career/candidate-profiles/parse` → AttachmentParser → Docling／必要时配置的视觉复核 → 用户校正 → `/candidate-profiles` → 当前会话绑定。新方案优先复用，不在手机另装 OCR 大模型。

## 4. 职位获取：基础入口与增强入口

### 首选：截图／正文导入，兼容登录后的招聘页面

用户在 Chrome 或招聘 App 看岗位 → 复制职位正文，或保存清晰完整截图 → 本平台“添加目标岗位” → 粘贴／上传 → 检查正文、公司、岗位 → 确认后分析。

现有 `/api/career/target-role-profiles/parse-file` 已接受岗位文件／图片并返回可编辑 job_text。底层 AttachmentParser 已有 Docling OCR 与低质量时的视觉复核，缺的是入口和流程整合。无需依赖招聘网站是否允许公开抓取，也不会自动把招聘 App 的登录态交给服务端。

首期仍是单文件。多张截图的顺序、重复内容拼接、长截图切片应单独实现并验证；OCR 不完整时提示补充，不能将缺失要求当作岗位没有要求。

### 其次：粘贴职位链接，服务端尝试公开页解析

用户复制岗位分享链接 → 本平台“链接导入” → 服务端读取公开正文 → 预览 → 用户确认。

当前已复用 `FirecrawlClient` 的 `/scrape`，为 Sheet 提供独立 `/api/career/target-role-profiles/parse-url`。无 Firecrawl 配置时复用 `JobPostingExtractor`（`src/career_assistant/job_sources.py`）静态 HTML 读取并显示提示；它不是通用浏览器渲染器，达到其正文长度上限时提示改用完整正文，避免导入可能截断的内容。

parse-url 输出 job_text、来源 URL、获取时间、可提取的公司／岗位和 notices，前端先预览再调用原 `/target-role-profiles` 确认，链接解析不创建岗位或分析任务。读取失败、只返回登录页／App 引导页或没有有效 JD 时，保留链接并提示改用截图／正文，不保存伪成功结果。名称缺乏证据时留空，岗位是否仍在招标为待核实。

服务端抓取不继承手机浏览器 Cookie。BOSS、猎聘等来源应按实际公开页面逐站验证，不能仅凭 Firecrawl 支持 JS 渲染就承诺所有岗位都可读，也不能把 HTTP 200 当作拿到 JD。[Firecrawl 官方抓取与结构化输出说明](https://docs.firecrawl.dev/advanced-scraping-guide)

### 增强：Android 安装后的 PWA 分享接收

第二阶段已本地实现 manifest → Service Worker → 临时浏览器记录 → `/career/import` → 会话选择 → 原资料 Sheet 的接收与校对链路；没有上线部署或替代真实 Android 系统分享验收。账户菜单“手机分享导入”提供使用说明。详细状态规则及构建产物验证见 [实施记录](2026-11-09-pwa-share-import.md)。

把平台安装到主屏幕后，注册 Web Share Target，使支持的 Android／Chrome 用户可以在系统分享菜单中选择本平台，接收 URL、文字或文件。注意是 **share_target 接收分享**，不是仅调用 `navigator.share()` 向外分享。[Chrome 官方 Web Share Target](https://developer.chrome.com/docs/capabilities/web-apis/web-share-target)

限制：需要安装，浏览器支持不统一，不能作为普通网页或 iPhone 所有浏览器都具备的基础能力。分享来的链接可能在 text 而不是 url 字段；文件接收需 POST multipart，并继续由用户确认导入。此入口解决的是接收材料，不会带来读取其他 App 登录页的能力。[MDN share_target](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Manifest/Reference/share_target)

## 5. 如果需要手机搜索多个岗位

“导入一个已看到的岗位”与“实时搜索招聘站岗位列表”必须分开命名和验收。

- 可建设“公开岗位检索”：复用服务端搜索／抓取，只展示实际取到的公开招聘来源，并记录来源、获取时间，保留打开原链接确认的入口；不得宣称覆盖登录招聘站的完整实时在招库。
- 若需要完整商业招聘站检索，应接入来源允许的正式职位数据／合作 API，并明确可用来源和刷新机制。当前没有证据证明项目已接入这类数据授权。
- PC 插件已采集的岗位可设计为用户确认后保存到账号资料库，再供手机阅读。当前后端已保存确认的目标岗位，不能把它等同于任意扩展搜索结果都已云端同步；新增完整岗位库同步属于后续需求。

不推荐把换特殊浏览器、云端远程登录招聘账户或重做原生 App 作为第一期前提；它们会增加部署、会话和交互适配成本，且不能解决当前表单入口问题。

## 6. 可复用开源方案比较

| 方案 | 许可证／维护证据 | 本项目适配成本与取舍 |
| --- | --- | --- |
| Docling | 代码 MIT，模型单独看许可；官方仓库和 release 持续更新 | 当前已接入，复用成本最低；用于文件／截图，效果以中文简历和岗位样本验证，不宣称解析百分百正确 |
| Firecrawl | 核心 AGPL-3.0，SDK 等部分 MIT；提供托管服务与官方文档 | 当前已有服务端窄客户端；公开 URL 可复用，托管调用有额度与第三方可用性成本；不为本需求新增一整套自建抓取集群，不把用户简历交给公开网页抓取流程 |
| PaddleOCR | Apache-2.0，官方仓库与发布记录可核对 | 可作为后续中文 OCR 候选；增加模型、依赖和推理维护成本，先评估现有 Docling 真实样本缺口，再决定是否替换或补充 |

来源：[Docling 仓库及模型许可说明](https://github.com/docling-project/docling)、[发布记录](https://github.com/docling-project/docling/releases)；[Firecrawl 仓库许可证](https://github.com/firecrawl/firecrawl)；[PaddleOCR 仓库](https://github.com/PaddlePaddle/PaddleOCR)、[发布记录](https://github.com/PaddlePaddle/PaddleOCR/releases)。没有在本次升级依赖或部署新服务，维护状态核对不等于完成漏洞审计。

## 7. 推荐落地顺序和验收

1. 本轮先完成已有表单布局修正与截图／文件入口前置。
2. 第一阶段已完成“已有简历／上传文件／图片识别”和“粘贴正文／截图文件／职位链接”三个同任务入口；复用现有解析，补公开 URL 的独立预览 API；保持草稿和附件保留。先完成 PC 验证，再验证手机布局和短屏操作。
3. 第二阶段已本地增加 Android PWA 分享接收；所有用户仍保留粘贴和文件选择入口。HTTPS 上的实际安装与系统分享仍需真机验收。
4. 第三阶段按业务需要引入公开岗位搜索或正式数据源；不把实时招聘站检索混入单岗位导入验收。

新功能验收至少包含：文件／图片真正上传与解析、低质量截图提示、链接仅登录页时正确回退、导入内容可编辑、未知字段留空、同账号已有简历读取、取消／返回保留草稿。多图和 PWA 必须加真实 Android／iPhone 兼容矩阵；本次移动 UA 不能证明系统分享／相册真机可用。

## 本轮验证记录

PC 双列表单和操作可达性先验证；新增紧凑表单 360×800、390×844、390×430、666×1828 的正文高度、次操作对齐、主按钮宽度与视口可达断言。结果：

- 修前 PC 1 项通过，紧凑表单 4 项失败，证据 [material-layout-before.log](../../tmp/regression-20261109/material-layout-before.log)。
- 相关表单／工作区保留／全平台布局浏览器 **26/26 通过**，证据 [material-layout-e2e.log](../../tmp/regression-20261109/material-layout-e2e.log)。
- 最后进一步收紧正文高度后，定向 **5/5 通过**，证据 [material-layout-final.log](../../tmp/regression-20261109/material-layout-final.log)；没有重复跑绿来覆盖原始失败。
- Node **297/297 通过**，证据 [material-layout-node.log](../../tmp/regression-20261109/material-layout-node.log)；Vite 构建通过，证据 [material-layout-build.log](../../tmp/regression-20261109/material-layout-build.log)。已有大 chunk 提示保留。
- 相关文件 diff／空白检查通过；[390px 修正截图](../../tmp/regression-20261109/material-layout-390x844.png) 已人工复核，截图及短视口结果在相同目录。

本轮不声称链接导入或 PWA 已交付，也未访问用户招聘账户或上传真实简历。没有生产部署；本地项目继续运行。

## 新任务交接摘要

1. 目标：修正资料上传 Sheet 排版，调研普通移动 Chrome 无插件获取简历／职位的方法；已完成本地 UI 修正与方案。
2. 修改：CareerContextSetupDialog、mobile.css、package.json，新增 context-material-layout E2E 与本方案；同步模块／移动适配文档。
3. 验证：先 PC；相关浏览器 26/26、最终定向 5/5、Node 297/297、构建与空白检查通过。
4. 未解决：公开 URL 导入、多图、PWA 分享、完整公开岗位搜索均为待实施方案；原 Grill R01–R12 不在本轮修复范围。
5. 关键命令：`npx playwright test e2e/context-material-layout.spec.js e2e/career-workspace-retention.spec.js e2e/mobile-platform.spec.js --reporter=line`，在 web-ui 运行。
6. 下一步：优先整合同任务的文件／正文／链接入口，复用现有解析与公开抓取；再做 Android PWA 与真机测试，不连接生产。

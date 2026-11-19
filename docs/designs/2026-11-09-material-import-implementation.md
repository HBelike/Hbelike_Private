# 无插件资料导入实施记录

目标：落实已确认的第一阶段方案。简历统一已有资料／文件／截图入口；岗位统一正文／文件截图／公开链接入口，统一进入可编辑预览再确认。只在本地实施和验证；PWA、完整岗位搜索、多图与生产部署不在本阶段。

沿用 Vue、FastAPI、AttachmentParser、JobPostingExtractor 和现有 Firecrawl REST 客户端，不新增依赖。公开链接每次主动解析只调用一次 scrape，使用 maxAge=0；读取成功不等同仍在招。优先确定性读取 JobPosting 结构化字段，无法确认的公司／岗位留空供用户补充；登录／验证／下架／列表／空页返回明确回退，不写资料库。

- [x] 后端：新增独立岗位链接预览服务和 `/target-role-profiles/parse-url`，复用公网 URL 校验；在工作线程执行网络请求并关闭自建客户端。单元／路由测试覆盖正常 JD、JSON-LD、HTML 静态降级、无正文、登录页、已下架、非公网、服务失败、无隐式落库。
- [x] PC 前端：明确三类简历来源和三类岗位来源；保留统一正文预览、来源和提取提示；链接失败保留已填内容；请求序号／中止控制防迟到覆盖；导入等待阻止重复提交和错误保存。
- [x] PC 端到端：三类入口、可编辑确认、错误回退、迟到响应；先完成 PC 5 项验证，再进入手机适配。手机追加跨模块返回后的链接、正文和来源提示保留验证；原附件保留回归继续覆盖。
- [x] 手机：入口可读、键盘近似高度下可滚动、操作可达、图片／文件选择共用现有 API；沿用当前文件限制和单文件边界。
- [x] 集成：相关后端、Node、浏览器与构建；通过真实应用路径发起公开职位 Firecrawl 小请求验证配置和结果，补充模块／设计记录，不调用用户招聘登录态或自动发起岗位分析。全量结果与首次失败另记下文。

调用链：Sheet 选择来源 → 文件原解析／新增 parse-url → 临时导入预览（正文、可选名称、来源、获取时间、提示）→ 用户校对 → 原 target-role-profiles → 原 context 绑定。返回链接解析预览不创建会话、岗位或分析任务。

## 设计与实现取舍

- `src/career_assistant/job_material_import.py` 负责单个公开岗位预览；`web/router.py` 只做请求、线程调用和错误转换。独立加载 `.env.career-assistant`，不为读取链接初始化数据库、模型服务或写入主密钥。
- 复用 `FirecrawlClient.scrape`，岗位读取显式使用 `fresh=True, include_html=True`；既有面经默认调用不变。每次点击解析仅请求一次 scrape，使用 `maxAge=0, storeInCache=false`，不做自动重试或额外模型抽取。rawHtml 只用于内存中的 JobPosting JSON-LD 提取，不返回浏览器。
- 优先读取 JobPosting 的明确公司、岗位和描述；否则读取职责／要求信号充分的正文，名称只从明确 ATS 标题提取。无法提取的新名称清空旧绑定值，等待用户补充，防止新 JD 与旧公司混用。多个 JobPosting、已过截止日期、无有效正文和不可访问页面返回回退提示；未知是否在招始终显示“待核实”。
- 无 Firecrawl Key 时复用现有静态 HTML 读取，并明确降级提示；静态正文达到原读取器长度上限时拒绝作为完整 JD。正文超过 30000 字不静默裁剪。岗位定位所用的 query／hash 保留，不将 URL 规范化当作删除岗位标识的理由。
- `CareerContextSetupDialog.vue` 统一三个简历来源和三个岗位来源，结果共用可编辑表单。已有内容先确认替换；导入中可切换来源取消读取，旧响应及等待期间的新编辑均有保护。新增 URL、预览来源、时间、提示纳入原会话内草稿；失败不清空已有文字和链接。
- 没有新增依赖、数据库迁移、OCR 服务或扩展权限。图片识别继续使用 AttachmentParser／现有 Docling 与视觉复核配置，不将浏览器入口测试等同真实 OCR 质量验收。

## 本地验证与证据

| 验证 | 结果与证据 |
| --- | --- |
| 后端定向 | 56 项通过，含公共网页、上下文、简历来源完整性与新 URL 入口；[日志](../../tmp/regression-20261109/import-backend.log) |
| 导入浏览器 | 最终 7/7，含 PC 编辑后确认、失败保留、未知名称、迟到响应、图片入口及手机短屏／跨模块返回；[日志](../../tmp/regression-20261109/import-browser-focused.log) |
| 相关集成浏览器 | 32/32（当时新导入用例为 6 项，后增加未知名称与跨模块断言）；[日志](../../tmp/regression-20261109/import-browser.log) |
| Node／构建 | 本轮执行时 Node 297/297、Vite build 通过；已有大 chunk 提示保留；[Node](../../tmp/regression-20261109/import-node.log)、[构建](../../tmp/regression-20261109/import-build.log) |
| 全量浏览器首次 | 79/80，资料版本更新后旧评估轮询用例在点击视口外指标按钮处超时；[日志](../../tmp/regression-20261109/import-browser-all.log)。随后单独 1/1、连续 3/3 通过，未改原测试断言或原评估源码；[单次](../../tmp/regression-20261109/import-polling-recheck.log)、[连续](../../tmp/regression-20261109/import-polling-repeat.log)。首次失败保留，暂未稳定复现 |
| 全量浏览器最终 | 当前工作区复核 88/88（含期间加入的其他用例），保留首次失败记录；[最终日志](../../tmp/regression-20261109/import-browser-all-recheck.log)。未将自动化通过表述为原全平台 Grill 缺陷已修复 |

实际公开页探针经过 Vite → 本地 `/api/career/target-role-profiles/parse-url` → Firecrawl，最终返回 SpaceX 与明确岗位名称、6357 字正文。登录和上下文使用受控 fixture，PC 使用真实接口结果，手机重放该结果核对布局；没有点击保存、创建岗位、发起模型分析或提交申请。[摘要](../../tmp/regression-20261109/import-live-smoke.json)、[PC 截图](../../tmp/regression-20261109/import-live-pc.png)、[手机截图](../../tmp/regression-20261109/import-live-mobile.png)。来源为 [Greenhouse 公开职位页](https://job-boards.greenhouse.io/spacex/jobs/8696080002)，仅证明当时读取成功，不证明持续在招。

本轮共执行三次真实本地链接解析：首次 Firecrawl 用于发现投递表单噪声；第二次因冷启动配置未加载走了静态降级；修复独立配置加载后，第三次 Firecrawl 成功。没有将第二次静态成功记为 Firecrawl 验证；[首次摘要](../../tmp/regression-20261109/import-live-smoke-initial.json)、[静态摘要](../../tmp/regression-20261109/import-live-smoke-static.json) 保留对应证据。

关键复核命令：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_job_material_import.py tests/test_public_web_collection.py tests/test_career_context_profiles.py tests/test_candidate_resume_source_integrity.py tests/test_career_context_profiles_v2.py -q --disable-warnings --tb=short
cd web-ui
npx playwright test e2e/material-import.spec.js --project=chromium --reporter=line
npx playwright test --reporter=line
```

## 后续边界

第一阶段已本地交付。PWA 分享接收、多张图片拼接、完整招聘站搜索和生产部署未包含。登录／验证码／App 专属页面不能继承手机 Cookie，继续由截图／文件／正文回退。真实 iPhone Safari／Android Chrome 的系统文件选择、相机、软键盘和真实 OCR 样本仍需设备验收；移动 UA 自动化不代表真机。原全平台 Grill 的 R01–R12 未在本阶段处理，详见 [审查报告](../regression-report-2026-11-09-dual-ui.md)。

后续进展：用户继续授权后，第二阶段 PWA 分享接收已完成本地实施，见 [独立记录](2026-11-09-pwa-share-import.md)；上述“未包含”仍描述第一阶段范围，不再代表 PWA 功能尚未编写。

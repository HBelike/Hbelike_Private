# PC 与移动端 UI 适配实施计划

**目标：** 落实全平台双端设计，修复误发送、手机报告裁切与桌面能力误导，保持既有接口和任务行为。

**架构：** 共用 Vue 业务状态、原 API 和主题。全局布局与触控规范集中维护，复杂页面保留自己的容器响应；桌面专属能力按浏览器能力提示，不按窗口宽度判定。

**技术栈：** Vue 3、Element Plus、原生 CSS、Node test、Playwright。

**设计依据：** [检测及设计方案](2026-11-06-pc-mobile-ui-adaptation.md)。用户已授权“开始实施”，在当前会话逐项执行，无需再次选择执行方式。

**复审状态：** 后续 [压力审查](2026-11-06-dual-ui-grill-review.md) 已确认 12 项既有用例未覆盖的问题（9 项 P1、3 项 P2），尚未修复。下文勾选与 47 项通过记录保留为首轮实施结果，不能作为最终放行结论。

## 全局约束

- 只在本地修改、测试与构建，不连接生产。
- 先完成 PC 可靠性与核心流程验证，再改手机布局；不做平板专项。
- 不引入 UI 框架，不复制业务请求，不重建任务恢复机制。
- 页面标题和操作使用中文；一个路由承载一个主任务。
- 保留当前未提交修改，只对相关代码做增量修改。

## 1. 输入和抽屉可靠性

文件：`web-ui/src/composer-keyboard.js`、对应 `.test.js`、`components/CareerAssistantPage.vue`、`e2e/mobile-platform.spec.js`。

接口：`shouldSubmitComposer(event, mobileBrowser)` 返回布尔值；组合输入、修饰键、手机 Enter 不发送，普通 PC Enter 发送。移动浏览器检测独立于视口宽度，供能力提示复用。

- [x] 增加有意义的输入逻辑测试，覆盖 `isComposing`、keyCode 229、Shift、手机 Enter 与 PC Enter。
- [x] 替换模板直接发送事件；会话加载成功后关闭历史抽屉，失败保留抽屉。
- [x] 隐藏抽屉 inert，打开后约束 Tab 与关闭焦点；不影响 PC 固定侧栏。
- [x] 运行 `node --test src/composer-keyboard.test.js` 与现有 PC E2E。

测试核心断言：

```js
assert.equal(shouldSubmitComposer({ key: 'Enter', isComposing: true }, false), false)
assert.equal(shouldSubmitComposer({ key: 'Enter' }, true), false)
assert.equal(shouldSubmitComposer({ key: 'Enter' }, false), true)
```

## 2. 公共手机布局与桌面能力说明

文件：`src/mobile.css`、`src/main.js`、`src/responsive-layout-preview-main.js`、`src/components/DesktopCapabilityNotice.vue`、`src/platform-capabilities.js`、`OnlineAssessmentAssistantPage.vue`、`BrowserInterviewMasterPage.vue`、`JobSearchWorkspace.vue`、`App.vue`。

接口：`isMobileBrowser()` 只读取浏览器信息；桌面窄窗口继续允许原能力。说明组件消费标题、说明、返回地址，不发业务请求。

- [x] 统一手机留白、16px 输入字号、44px 触控和 48px 主按钮。
- [x] 聊天保持消息区单滚动面，短屏可收缩；键盘视口仅局部使用 VisualViewport，清理监听。
- [x] 移动浏览器访问桌面笔试／采集时显示能力说明，阻止无意义的扩展安装和重复连接。
- [x] 全局抽屉与账户入口使用同一套尺寸和焦点规则。
- [x] 在 390×844 与 844×390 检查主操作可见；桌面 900px 窄窗口不触发能力拦截。

## 3. 求职、岗位与匹配报告

文件：`CareerAssistantPage.vue`、`CareerMatchDialog.vue`、`CareerContextRail.vue`、`CareerJobSearchDialog.vue`、`CareerGreetingDialog.vue`、`JobSearchWorkspace.vue`。

- [x] 手机输入工具收纳为“资料与工具”，保留模型、引用和上传状态。
- [x] 匹配报告改手机全屏、单列摘要与证据；PC 保留并排核对。手机列表条目按原数据渲染，不建立第二份评分计算。
- [x] 岗位列表与详情切换保留筛选、选择和滚动；确认条不覆盖内容，手机不隐藏唯一详情入口。
- [x] 招呼流程 Sheet 主体滚动，编辑与审核操作可达；手机缺失扩展能力时显示明确原因。
- [x] E2E 验证组合输入 0 请求、PC 显式发送 1 请求、草稿跨断点保留、匹配重试 1 任务、对照／证据／原文可读。

## 4. 简历、面经、技能和公众号内容

文件：`ResumeAssistantPage.vue`、`InterviewLibraryPage.vue`、`App.vue`、`mobile.css`。

- [x] 简历历史保留关键词，日期筛选可收起；工作区继续原标签状态，不销毁编辑。
- [x] 面经默认显示查找列表，阅读详情独占主区；导入入口收纳，返回恢复树和滚动。
- [x] 技能阅读／编辑单任务呈现，保存状态可见；保留权限和原请求。
- [x] 工作台保留原子路由，文章、媒体、流程、日志独立阅读；整理窄屏横向数据容器。
- [x] 清理相关页面装饰性英文眉题，保留真实技术标识。
- [x] 验证空数据、长文本和填写后切换／返回状态。

## 5. 评测与管理

文件：`EvaluationCenterPage.vue`、`navigation-access.js`、`App.vue`、`AdminConsolePage.vue`、`ObservabilityPage.vue`、`mobile.css`。

- [x] 评测总览、数据集、实验和指标使用子路由，组件消费路由 section，沿用当前 API 和状态。
- [x] 手机证据链竖排、概览单列，按钮不逐字换行；表单、版本和实验列表可读。
- [x] 管理表单单列，模块开关可触；服务观察页保留状态与原外部入口。
- [x] 验证直接打开子路由、浏览器返回、管理员权限和手机布局。

## 6. 集成验证与交付

- [x] `npm test`：现有与新增逻辑测试全通过。
- [x] `npm run build`：成功；记录现有包体积提示。
- [x] `npx playwright test --reporter=line`：现有 PC 用例与新增手机核心用例通过。
- [x] 手机矩阵 360／375／390／430px 与横屏；PC 900／1024／1093／1280／1440／1920px 采样，检查关键容器内部溢出。
- [x] 更新 `docs/mobile_ui_adaptation.md`、`docs/responsive_layout_system.md` 和本计划结果，明确 fixture 与真实链路验收边界。
- [x] 真机与真实账户链路若当前环境无法执行，交付时明确剩余项目，不把模拟 UA 称为 Safari 真机。

## 验证记录

- 前端 Node 测试：295/295，通过日志 `tmp/ui-node-20261106.log`。
- 全量 Playwright：47/47（Chromium 45，Firefox 基础 2），日志 `tmp/ui-all-e2e-20261106.log`。
- Vite production build：通过，5.67 秒；保留既有大 chunk 提示，日志 `tmp/ui-build-20261106.log`。
- PC 核心路由 6 组尺寸共 42 个采样；另验紧凑笔试工作区、PC 输入、岗位和招呼等原业务用例。
- 手机覆盖 19 个主／子路由，聊天 6 组尺寸、认证竖横屏、匹配证据与原文、资料／模型／导入弹层焦点、保存失败与权限返回；宽表格仅局部滚动。
- 回归入口 `npm --prefix web-ui run test:e2e:responsive` 已纳入新的 PC 输入和手机平台用例。
- 本地测试数据截图：[求职对话](../../tmp/ui-implementation-20261106/mobile-career.png)、[匹配报告](../../tmp/ui-implementation-20261106/mobile-match-report.png)、[评测总览](../../tmp/ui-implementation-20261106/mobile-evaluations.png)。
- 真实后端／账户／扩展、实际 iOS／Android 的软键盘、复制下载、后台切换尚未验收；本轮没有运行真实公众号写操作，没有部署生产。

## 新任务交接摘要

1. 目标：全 Web 平台 PC／手机双端改造，PC 先行，不做平板专项；本地代码与自动化已完成。
2. 公共修改：App.vue、mobile.css、main.js、平台能力与焦点／输入辅助模块、index.html、导航解析和回归命令。
3. 页面修改：求职、上下文、匹配报告与证据、岗位、简历、面经、评测、观察页和笔试工作区；保留原 API 与权限。
4. 已验证：Node 295/295、Playwright 47/47、构建通过，PC 6 组与手机多尺寸检查；证据日志在 tmp/ui-*-20261106.log。
5. 文档：mobile_ui_adaptation.md、responsive_layout_system.md 和对应模块文档已同步；原检测报告保留为实施前证据。
6. 未解决：真实 iOS／Android、真实账户后端及招聘扩展的联调验收；现有大 chunk 提示仍在。
7. 关键命令：npm --prefix web-ui test；npm --prefix web-ui run build；在 web-ui 运行 npx playwright test --reporter=line。
8. 下一步：准备本地真实服务与账户后按双端验收表联调，再安排真机；生产部署需要另行明确授权。

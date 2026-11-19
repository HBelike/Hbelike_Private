# 求职工作区跨模块保留

用户已确认审查 D01：保留会话、草稿和附件。范围为当前登录会话的页面内导航，不包含刷新、关闭标签页或跨设备恢复；不顺带修复 R01–R12。

设计：App 使用内存快照接收求职页卸载前的会话、资料、正文、引用和附件 File；重新进入时先恢复，再通过原 API 校准会话。保持现有组件卸载时的 SSE／定时器／监听清理，不让隐藏工作区继续占用全局事件。能力说明页普通点击返回使用 App 导航，保留链接的新标签页操作。退出登录清空，且拒绝接收退出后组件卸载发出的旧快照。

调用链：CareerAssistantPage 卸载 → preserve-workspace → App 内存 → initialWorkspace prop → 求职页初始化 → 原会话读取。没有后端、新依赖或浏览器持久化；已有附件入口和文件限制保持不变。

实施与验证：

- [x] PC 跨模块返回保留会话、非空输入、资料和引用；先观察旧实现失败。
- [x] 手机线上笔试／面试说明页的链接返回与浏览器返回保留状态，且不触发整页加载。
- [x] 退出后登录不恢复上个登录会话的工作区；恢复读取失败保留草稿。
- [x] 正式门禁补入用例，运行相关浏览器、Node 和构建；记录实际结果与附件验证边界。

## 实现细节

- `App.vue` 用 shallowRef 保存当前登录会话的快照，监听 `preserve-workspace`，退出登录清空并防止旧子组件卸载后回写。
- `CareerAssistantPage.vue` 通过 refs 字段表捕获和恢复会话、正文、资料绑定、历史页码、引用及附件；恢复会话不依赖它恰好在当前分页列表中，详情读取失败保留快照和输入。模型可用性仍沿用原加载逻辑。
- 资料 Sheet 的待解析文件原先只在弹层内部，故 `CareerContextSetupDialog.vue` 同时捕获其文件、解析预览和已编辑字段。关闭 Sheet 保留临时草稿；重新打开恢复，不自动上传或解析。按会话／资料绑定版本隔离，成功保存后清空，不把另一会话的附件带过来。
- `DesktopCapabilityNotice.vue` 发出站内导航事件，App 和独立职位库接入；Ctrl／Meta／Shift 点击仍沿用链接操作。原生浏览器返回也恢复工作区。

## 验证记录

新增 `web-ui/e2e/career-workspace-retention.spec.js`，纳入 `test:e2e:responsive`。先观察 PC 原实现丢失会话的红测试，再通过 7 项定向测试：PC 主导航与 browser back、PC／手机待解析附件、两类手机能力说明页返回、503 恢复失败、退出重新登录隔离。

附件测试从现有资料 Sheet 选择合成 PDF，跨模块后重新打开并显式解析；受控上传 API 检查文件名和原始字节一致，证明没有只恢复名称。没有调用真实 OCR／模型或写入真实用户资料。

验证结果：

- PC 红测试先复现原会话丢失：[workspace-before.log](../../tmp/regression-20261109/workspace-before.log)。
- 定向浏览器 **7/7 通过**：[workspace-e2e.log](../../tmp/regression-20261109/workspace-e2e.log)。
- 全量浏览器 **68/68 通过**（Chromium 66、Firefox 基础 2）：[workspace-e2e-all.log](../../tmp/regression-20261109/workspace-e2e-all.log)。
- Node **297/297 通过**：[workspace-node.log](../../tmp/regression-20261109/workspace-node.log)。
- Vite build **通过**，原有大 chunk 提示保留：[workspace-build.log](../../tmp/regression-20261109/workspace-build.log)。
- 相关已跟踪文件 diff 检查及新增文件空白检查通过。

本轮不修复原审查 R01–R12，也不改 T01 的旧断言。此轮全量通过不否认审查时已经捕获的选择器时序歧义；未宣称全平台问题清零。服务继续在本地运行，未部署生产。

## 新任务交接摘要

1. 目标：按用户确认保留求职会话、未发送草稿和附件；已完成本地实现。
2. 修改：App、CareerAssistantPage、CareerContextSetupDialog、DesktopCapabilityNotice、JobSearchWorkspace、package.json；新增工作区浏览器用例，同步模块／适配／审查文档。
3. 验证：PC 先验证；定向 7/7、全量浏览器 68/68、Node 297/297、构建及空白检查通过。
4. 未解决：R01–R12、T01 仍按审查报告保留；刷新、关页、跨设备恢复不在本次范围。
5. 关键命令：在 web-ui 执行 `npx playwright test e2e/career-workspace-retention.spec.js --reporter=line`、`npx playwright test --reporter=line`、`npm test`、`npm run build`。
6. 下一步：如继续修复全平台问题，按审查优先级单独处理；真实设备／真实文档解析需另行验收，没有生产部署授权。

# 双端适配压力审查

日期：2026-11-06。本报告保留修复前的审查与复现证据，当时确认 12 项问题：9 项 P1、3 项 P2。**G01–G12 已完成本地修复**，旧绑定迟到响应疑点也已补齐复现与修复；修复后的验证和边界见 [修复记录](2026-11-06-dual-ui-grill-fixes.md)。下文“实际”均描述修复前状态。

前一轮 47 项浏览器用例覆盖了主要成功路径，但缺少非空草稿、请求乱序、异步任务失败、系统返回、实际表单和超长正文等状态，因此完成判断偏早。以下问题均有本地 Chromium 浏览器复现；模型、后端和扩展响应使用受控 fixture，不涉及真实发布或招呼发送。

## P1 功能与状态问题

### G01 短屏 PC 丢失四个主要入口

- 复现：桌面浏览器 `900×420`，进入已有求职会话。
- 实际：职位检索、一键打招呼、线上笔试、面试大师全部不可见；“资料与工具”也不可见。相同会话调整到 `900×600`，四个入口恢复。
- 位置：[mobile.css:161](../../web-ui/src/mobile.css:161)，命中 `max-width:900px` 与 `max-height:500px` 后直接隐藏快捷操作，未提供替代入口。
- 建议：只收纳到可操作的菜单，不移除能力；在最低 PC 验收尺寸完成一次真实选会话后的操作检查。

### G02 长 URL 将整个消息区域撑宽

- 复现：390px 手机视图，助手回复包含一个连续长 URL 或无空格标识。
- 实际：消息区可用宽度 `340px`，`scrollWidth=4505px`；正文段落宽 `4474px`。普通消息变成大范围横向滚动，根页面无溢出检查仍会通过。
- 位置：[CareerMessageContent.vue:163](../../web-ui/src/components/CareerMessageContent.vue:163)，grid 子项没有可换行约束；父组件 scoped 的正文规则未约束子组件段落。
- 建议：普通段落、列表和引用允许断长词；仅代码块和表格保留自己的局部横向滚动。验收必须检查消息区内部。

### G03 会话读取失败也会清空当前草稿与资料

- 复现：已有会话输入未发送内容，打开历史并再次点击当前会话；该详情 GET 返回 503。
- 实际：会话标题仍是原会话，但草稿由非空变成空串，`has-context-rail=false`。错误提示可见，原输入和资料状态没有恢复。
- 位置：[CareerAssistantPage.vue:1280](../../web-ui/src/components/CareerAssistantPage.vue:1280)，在新数据成功返回前调用 `resetConversationDraft()` 并清空上下文。
- 建议：读取成功后提交会话切换；重复选择当前会话不重新加载并重置；失败保留原会话、草稿、附件和引用。

### G04 重选同一技能静默覆盖未保存修改

- 复现：技能库打开技能并编辑，未保存 → 点“已安装” → 再点同一技能。
- 实际：编辑框从自定义草稿变回服务器原文，确认对话框次数为 0。
- 位置：[App.vue:772](../../web-ui/src/App.vue:772) 与 [App.vue:786](../../web-ui/src/App.vue:786)，详情加载不区分重复选择和未保存状态，直接覆盖编辑文本。
- 建议：同一技能返回时保留现有编辑；切换其他技能时提供保存／放弃／取消，或按技能保存当前会话内草稿。

### G05 快速选择技能，迟到请求覆盖最新选择

- 复现：技能 A 详情延迟 450ms，B 立即返回；列表依次点 A、B。
- 实际：先显示 B，随后迟到 A 将标题和编辑内容改回 A。用户可能继续编辑或保存错误对象。
- 位置：[App.vue:784](../../web-ui/src/App.vue:784)，没有详情请求序号、取消或当前选择校验。
- 建议：只接受最新选择对应的响应；错误响应也不能覆盖最新页面状态。补两项以上数据的乱序响应回归。

### G06 简历新建任务跨模块返回后丢资料

- 复现：`/resume-assistant/new` 填写岗位介绍，经主导航进入求职助手，再浏览器返回。
- 实际：URL 回到 `/resume-assistant/new`，但岗位介绍变为空，确认次数为 0。新建工作区只恢复模式，没有恢复资料。
- 位置：[ResumeAssistantPage.vue:99](../../web-ui/src/components/ResumeAssistantPage.vue:99)、[ResumeAssistantPage.vue:291](../../web-ui/src/components/ResumeAssistantPage.vue:291)，以及 App 切换页面时卸载组件的路径。
- 建议：离开未提交任务时提供保护或保留草稿；至少覆盖岗位介绍、额外要求、已选文件与已生成但未保存的内容。

### G07 手机浏览器返回没有先关闭详情或弹层

- 复现：从 `/career` 导航到面经库，打开面经阅读详情，再执行浏览器返回。
- 实际：直接从 `/interviews` 回到 `/career` 并卸载详情，未先回面经列表。详情、辅助抽屉和弹层仅保存本地 ref，未建立对应返回层级。
- 位置：[App.vue:466](../../web-ui/src/App.vue:466)、[InterviewLibraryPage.vue:29](../../web-ui/src/components/InterviewLibraryPage.vue:29)。设计要求见原方案第 96 行附近的手机返回约定。
- 建议：为实际可返回的页面状态建立一致的 history 约定；浏览器返回与页面“返回列表／关闭”的行为一致。单独覆盖有草稿时的返回。

### G08 分析排队成功后再失败，上一份报告丢失

- 复现：初始报告有 `75–100%`；重新分析 POST 返回 queued，后续轮询返回 failed。
- 实际：失败提示出现，但摘要变为空串，要求对照表从有内容变为 0 行。当前只处理了 POST 直接失败时保留报告。
- 位置：[CareerAssistantPage.vue:1606](../../web-ui/src/components/CareerAssistantPage.vue:1606)、[CareerAssistantPage.vue:1035](../../web-ui/src/components/CareerAssistantPage.vue:1035)、[CareerMatchDialog.vue:88](../../web-ui/src/components/CareerMatchDialog.vue:88)。真实仓储的 [重试重置](../../src/career_assistant/persistence/job_assessment_repository.py:205) 和 [失败保存](../../src/career_assistant/persistence/job_assessment_repository.py:191) 均清空结果，故该 fixture 与服务响应语义一致。
- 建议：按资料绑定／版本保留最后成功报告，等待或失败时明确标为旧结果，不能显示为当前新结果；新报告成功后再替换。

### G09 评测总览的“创建实验”点击后无可见结果

- 复现：在 `/evaluations` 点击页头“创建实验”。
- 实际：URL 仍为 `/evaluations`，表单数量 0，错误提示为空；之后手动切到“实验记录”，之前设置的表单才出现。
- 位置：[EvaluationCenterPage.vue:163](../../web-ui/src/components/EvaluationCenterPage.vue:163) 只设置 `showExperimentForm`，表单在 [experiments 分支](../../web-ui/src/components/EvaluationCenterPage.vue:438)。
- 建议：创建动作先进入实验子路由，并让表单与当前主任务一致；用例要从总览入口发起。

## P2 显示和反馈问题

### G10 手机评测表单没有变单列

390px 下新建数据集／实验仍为 `147.5px 147.5px` 双列。原因是 [mobile.css:73](../../web-ui/src/mobile.css:73) 写成了仓库中不存在的 `.evaluation-form-grid`，真实类名为 [`.evaluation-form`](../../web-ui/src/components/EvaluationCenterPage.vue:393)，继续继承 [styles.css:1574](../../web-ui/src/styles.css:1574) 的双列。应修正选择器并验证实际打开的三类表单。

### G11 技能保存成功反馈被重新加载立即清空

实测内容保存成功、编辑器也显示新内容，但 `.alert.success=[]`。[App.vue:906](../../web-ui/src/App.vue:906) 设置成功文案后调用 `loadSkills`，后续 `loadSkillDetail` 在 [775 行](../../web-ui/src/App.vue:775) 将文案清空。应在重载完成后设置成功提示，或区分读取动作与保存动作的反馈生命周期。

### G12 聊天与技能阅读实际正文仍为 14px

父容器为 16px，但 [CareerMessageContent.vue:163](../../web-ui/src/components/CareerMessageContent.vue:163) 显式设置 14px，聊天和手机技能阅读实测均为 14px。应让子组件消费手机正文字号，避免只改父级后误判已生效。

## 未计入确认数量的疑点

同一会话更新资料绑定后，旧评估 GET 晚返回可能覆盖新绑定：[CareerAssistantPage.vue:1029](../../web-ui/src/components/CareerAssistantPage.vue:1029) 只核对会话 ID，未核对绑定或请求版本。本轮未走完完整用户流程，不标为已复现；修复异步报告分支时应增加此项延迟响应测试。

## 复现证据与测试补齐

- [第一组结果](../../tmp/ui-grill-20261106/results.json)：短屏入口、技能草稿、浏览器返回、长正文与字号。
- [第二组结果](../../tmp/ui-grill-20261106/extra-results.json)：技能请求乱序、保存反馈、简历草稿、会话失败、报告异步失败、评测入口与表单。
- 截图：[短屏 PC](../../tmp/ui-grill-20261106/pc-900x420-actions.png)、[长 URL](../../tmp/ui-grill-20261106/long-chat-content.png)、[异步分析失败](../../tmp/ui-grill-20261106/assessment-async-failed.png)、[手机双列表单](../../tmp/ui-grill-20261106/evaluation-double-column.png)。

```powershell
npm --prefix web-ui run dev
# 另一终端从项目根目录运行，只使用本地 fixture
node tmp/ui-grill-20261106.mjs
node tmp/ui-grill-extra-20261106.mjs
```

修复时先把 P1 复现迁为断言预期行为的正式 E2E，再修对应代码；同时保留原 47 项。覆盖至少两项技能数据、非空草稿、两阶段任务失败、浏览器返回与已选会话的短屏状态。根页面 scrollWidth 检查应补上消息与表单内部的检查。

## 审查完成时的交接摘要（修复前历史）

1. 目标：对全平台双端改造执行压力审查，找遗漏和显示 bug；本轮只读审查。
2. 新增：本报告、tmp 下两份复现脚本、两份 JSON 和截图；业务源码未改。
3. 已验证：10 组受控浏览器探针确认 12 项问题，9 项 P1、3 项 P2；各项证据见上文。
4. 未解决：12 项均待修复；另有旧绑定请求晚返回的竞态待补实测。
5. 关键命令：启动本地 Vite 后运行两份 `node tmp/ui-grill-*.mjs` 脚本；既有回归仍为 `npx playwright test`。
6. 下一步：先补 P1 回归并修复状态丢失、请求竞态和功能入口，再处理三项视觉／反馈问题，最后重跑完整门禁；真实设备与真实服务联调边界仍保留。

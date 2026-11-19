# 双端压力审查问题修复计划

目标：修复 [压力审查](2026-11-06-dual-ui-grill-review.md) 的 G01–G12，并补齐原有 47 项未覆盖的状态。用户已明确授权修复，在当前会话直接实施。仅本地修改、测试和构建。

架构：沿用现有 Vue 状态、API 和主题；新增小型手机 history 层管理详情／弹层返回，技能草稿只保留在当前登录会话内，简历工作区使用 KeepAlive 保留文件和编辑。报告缓存按会话及资料绑定版本隔离，不写入后端或新增任务系统。

技术栈：Vue 3、原生 CSS／History API、Node test、Playwright。不增加依赖、不做平板专项。

## 1. 正式回归与 PC 修复

- [x] 新增 `web-ui/e2e/grill-regression.spec.js`，迁入短屏工具、长文本、会话失败、技能乱序／草稿／保存、报告异步失败和评测入口的复现；先观察旧代码失败。
- [x] 修改 `CareerAssistantPage.vue`：当前会话重选直接返回；新会话读取成功后才提交状态切换；失败保留草稿与上下文。
- [x] 修改 `App.vue`：技能详情请求序号，按 ID 保留当前会话编辑；保存完成后设置成功反馈，退出登录清除草稿。
- [x] 修改 `CareerMessageContent.vue`、`mobile.css`、`EvaluationCenterPage.vue`：长词正确换行、局部代码／表格滚动、正文 token、短屏保留入口、正确表单选择器、创建实验进入对应子路由。
- [x] 运行 PC 新用例、原 PC 门禁与 Node 测试。

关键断言：

```js
await expect(input).toHaveValue(draft)
await expect(page.locator('.skill-editor-panel h2')).toHaveText('技能 B')
expect(await messages.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
await expect(page).toHaveURL(/\/evaluations\/experiments$/)
```

## 2. 评估旧报告与异步边界

- [x] 新增 `career-assessment-snapshot.js` 的资料版本 key，覆盖不同会话／绑定／简历与岗位版本隔离的 Node 测试。
- [x] `CareerContextRail.vue` 保留当前绑定最后成功结果，`CareerMatchDialog.vue` 分开接收显示报告与当前任务状态，明确“上一次成功报告”及版本。
- [x] `CareerAssistantPage.vue` 的评估读取／重试返回时核对资料 key，丢弃旧绑定响应。
- [x] 验证 queued→failed 保留旧报告，queued→ready 替换并移除旧结果提示，绑定改变不混用旧报告。

## 3. 手机返回与工作区保留

- [x] 新增 `use-history-layer.js`：打开手机详情／弹层增加同 URL history 层；浏览器返回关闭一层，显式关闭消费对应层；主导航先清理当前层，再加入目标路由。
- [x] 接入全局导航、会话辅助抽屉、匹配报告、共用弹层焦点工具、面经详情和技能视图。PC 窗口宽度不决定设备能力。
- [x] `App.vue` 保持简历 KeepAlive 容器跨路由存在，`ResumeAssistantPage.vue` 激活时恢复路由，非当前模块的 popstate 不触发请求；明确开始新任务时确认清空现有编辑。
- [x] 验证手机 browser back 先关闭详情／Sheet，再返回前路由；显式关闭不留下无效返回层；简历 JD、要求、文件与分析内容跨模块仍在。

## 4. 集成与文档

- [x] 全量 Node 测试、构建、原有与新增 Playwright 回归通过；PC 先验证，再核对手机尺寸。
- [x] 更新审查状态、双端说明与模块文档，保留实施前证据。
- [x] 真实设备和真实服务的未验收边界继续明确，不部署生产。

## 验证记录

2026-11-06：G01–G12 本地修复完成，旧绑定迟到响应已增加浏览器验证。先通过 18 项 PC 浏览器检查与 297 项前端测试，再实施手机返回／缓存；集成时发现短屏快捷栏恢复后挤出发送按钮，已改为标题／快捷栏分行并重新验证。

| 问题 | 修复结果 | 回归证据 |
| --- | --- | --- |
| G01 短屏入口 | 保留四项快捷入口，标题单行省略，发送按钮在视口内 | PC 900×420；手机 844×390 横屏及多尺寸输入用例 |
| G02 长正文 | 普通长词断行，仅代码／表格局部滚动 | 连续 URL、代码与真实宽表格内部 scrollWidth 检查 |
| G03 会话草稿 | 同项重选不重置，新数据成功后才切换，失败保留 | 非空草稿＋详情 503＋资料仍在 |
| G04 技能草稿 | 当前编辑与跨技能草稿按 ID 保留 | A→A、A→B→A，正文及编辑框断言 |
| G05 技能乱序 | 只有最新详情请求可以提交状态 | 阻塞 A、立即返回 B，再释放 A |
| G06 简历任务 | KeepAlive 保留当前会话工作区，首页可继续任务，新建可取消 | 文件、JD、要求、审核建议、生成内容跨模块返回 |
| G07 系统返回 | 移动浏览器同 URL 层逐层返回，显式关闭和导航先清层 | 面经详情、资料 Sheet、嵌套报告、保存等待与后续路由返回 |
| G08 旧报告 | 同会话／绑定／资料版本保留最后成功报告，标明旧结果，成功替换 | queued→failed→ready；Node 测试版本与会话隔离 |
| G09 创建实验 | 从总览进入实验路由并显示表单 | URL 与实际 form 可见性 |
| G10 手机表单 | 使用真实选择器，按钮文字不压断 | 实验、数据集、版本、案例四类实际表单单列与内部宽度 |
| G11 保存提示 | 重载结束后设置成功文案 | 保存内容与 role=status 的成功反馈 |
| G12 正文字号 | 正文子组件消费手机字号变量 | 聊天和技能阅读实测 16px |
| 旧绑定响应 | 轮询和重试响应核对会话及资料版本 key | 更新为岗位 v2 后释放旧 v1 轮询，当前报告仍为新岗位 |

正式新增 14 项浏览器回归，纳入 `npm run test:e2e:responsive`。最终完整检查：

- `npm test`：**297/297 通过**，日志 [ui-grill-fixes-node.log](../../tmp/ui-grill-fixes-node.log)。
- `npm run build`：**通过**，已有大 chunk 提示保留，日志 [ui-grill-fixes-build.log](../../tmp/ui-grill-fixes-build.log)。
- `npx playwright test --reporter=line`：**61/61 通过**（Chromium 59、Firefox 基础 2），日志 [ui-grill-fixes-e2e-final.log](../../tmp/ui-grill-fixes-e2e-final.log)。
- 受影响手机及压力用例单独复核：**28/28 通过**，日志 [ui-grill-fixes-followup.log](../../tmp/ui-grill-fixes-followup.log)。
- 相关已跟踪源码／文档 `git diff --check` 通过。视觉复核 [短屏 PC](../../tmp/ui-grill-fixes-20261106/pc-900x420-actions.png)、[长正文](../../tmp/ui-grill-fixes-20261106/mobile-long-content.png)、[手机表单](../../tmp/ui-grill-fixes-20261106/mobile-evaluation-form.png)、[旧报告](../../tmp/ui-grill-fixes-20261106/mobile-previous-report.png)，指标见 [results.json](../../tmp/ui-grill-fixes-20261106/results.json)。

PC 视口沿用 900×420 至 1920×1080；手机沿用 360／375／390／430px、844×390 横屏及 390×430 键盘近似高度。测试使用受控 API／模型／扩展 fixture；没有调用真实模型、发送招呼或部署生产。真实 iPhone Safari／Android Chrome 的软键盘、上传／下载与后台恢复，以及真实账户／后端／招聘扩展链路仍需单独验收；刷新或关闭标签页后恢复草稿不在范围。

## 新任务交接摘要

1. 目标：完成全平台双端压力审查 G01–G12 的本地修复；已完成。
2. 修改：App、求职对话／资料／报告、简历、面经、评测、正文组件与 mobile.css；新增 history 层、报告快照、Node／浏览器回归；各模块文档已同步。
3. 验证：Node 297/297、Playwright 61/61、构建及相关 diff 检查通过，四组截图已复核。
4. 未解决：本轮确认问题无遗留；真实设备、真实服务及扩展联调尚未验收。
5. 关键命令：在 web-ui 执行 `npm test`、`npm run build`、`npx playwright test --reporter=line`；专项入口 `npm run test:e2e:responsive`。
6. 下一步：按真实设备／真实业务账户补验；未获当次投产授权，不连接生产或部署。

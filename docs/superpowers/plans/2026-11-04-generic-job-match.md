# 通用岗位匹配弹窗实施计划

**目标：** 将现有岗位指标弹窗升级为通用 JD—简历证据对照弹窗，完成服务端规则复算和 PC 验证。

**架构：** 复用当前评估队列、Judge 模型、JSON 持久化和上下文绑定。新增 v2 规则模块，保留 v1 读取；Vue 独立弹窗组件承载筛选、原文和计算说明。

**技术栈：** Python、现有 OpenAICompatibleChatClient、Vue 3、原生 dialog、Node test、unittest、Playwright。

**设计：** `docs/designs/2026-11-04-generic-job-match.md`；用户最终确认使用大尺寸弹窗，覆盖原设计中的独立路由方案。当前会话直接实施，不另行委派。

## 约束

- 仅本地修改、测试、构建，不部署，不修改两份参考技能。
- 当前已授权实施，不再增加审批步骤；已有未提交工作保留。
- 先 PC，不进行手机或平板专项适配。
- JD 决定维度；硬性条件、核心要求、加分项独立；原文绑定；未知和冲突不等于不满足。

## 任务与接口

- [x] 新建 `src/career_assistant/job_match_rules.py`：提供 `build_match_tool()`、`match_system_prompt()`、`validate_match(raw, jd_lines, cv_lines, as_of=None)`；输出 v2 三层摘要、条件树、维度上下限和原文索引。条件树支持 ALL/ANY/AT_LEAST_N，数字、学历、月数与年龄按源文本复核，院校排名未核验则待确认。
- [x] 新建 `tests/test_job_match_rules.py`：验证未知区间、直接反证、父级权重、逻辑组合、冲突、跨岗位维度、日期重叠、学历取得、未核验院校、原文引用；使用 `python -m unittest tests.test_job_match_rules` 验证。
- [x] 接入 `job_assessment.py`：新调用使用 v2 Schema/Prompt；`validate_and_score` 按版本分发；保留旧版回归。设置 prompt_version 为新版本，保存时同步 schema_version。检查简历解析完整性并移除评估编号的静默截断。
- [x] 扩展 `career-assessment-view.js` 并添加纯函数测试：三层卡片、区间格式、旧版标记、弹窗统一证据记录、维度筛选。
- [x] 新建 `CareerMatchDialog.vue`：原生模态 dialog 实现焦点约束和 Escape；摘要、维度条、筛选对照、证据栏、原文定位、计算页、旧版重新分析入口。`CareerContextRail.vue` 打开新组件并传入点击卡片 ID；`CareerJobCanvas.vue` 展示新卡片和区间。
- [x] 添加 `web-ui/e2e/job-match-dialog.spec.js`：真实页面配合合成 API fixtures 检查弹窗打开、默认维度、筛选、原文、计算、关闭后返回焦点，以及旧版结果。使用 `npx playwright test e2e/job-match-dialog.spec.js --project=chromium`。
- [x] 执行相关 Python 回归、Node 测试和 `npm run build`，查看 PC 截图；更新设计与模块文档记录调用链、计算边界、验证结果。

## 验收命令

```powershell
.venv/Scripts/python.exe -m unittest tests.test_job_match_rules tests.test_career_job_assessment
cd web-ui
node --test src/career-assessment-view.test.js
npx playwright test e2e/job-match-dialog.spec.js --project=chromium
npm test
npm run build
```

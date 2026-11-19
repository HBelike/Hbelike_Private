# 岗位摘要告警与招呼语 200 字限制实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.
> 用户已明确要求修复卡片告警并统一 200 字限制，本轮直接本地实施；分配生成、前端/插件、摘要与浏览器三个独立子任务，不调用 using-superpowers。

**Goal:** 真实岗位长标识不再导致搜索摘要保存告警，招呼语生成与提交均遵守 200 字硬上限。

**Architecture:** 卡片标识与展示文字独立校验，摘要优先使用 jobId 并按展示字段界限保存；不改变岗位实际检索标识。招呼语由模型 System/User/纠正提示词明确上限，后端生成结果验证、前端编辑与发送检查、插件提交前检查使用同一字符口径。

**Tech Stack:** Python/FastAPI、Vue、JavaScript Unicode code point、Manifest V3、pytest、Node test、Playwright。

**Spec:** 当前会话用户截图和“招呼语限制在 200 字以内，包括提交和生成都告诉大模型”的要求。

## Global Constraints

- 仅本地修改、测试、打包，不连接生产、不执行真实 BOSS 发送。
- 保留已有用户未提交改动和前一轮插件连接修复。
- 200 字按 Unicode 字符计数，中文、英文、数字、标点及空格均计入；与 Python len 和 JavaScript Array.from 口径一致。
- 超长招呼语要求修改或重新生成，不机械截断后发送。

## Task 1：卡片数据契约

**Files:** `src/career_assistant/job_search_cards.py`、`web-ui/src/job-search-card.js`、对应 Python/Node 测试。

- [x] 用 400 字符 securityId 复现原校验失败。
- [x] 摘要优先 jobId；标识上限 8192，标题/公司 240、薪资/城市 120，展示摘要按 Unicode 截取；最多十条且保留总数。
- [x] Python 验证字段特定错误及事务不部分保存；覆盖搜索摘要和确认岗位摘要。
- [x] 浏览器回归模拟十五条真实形状长标识，验证保存与确认、历史恢复和原连接链路。

## Task 2：模型生成约束

**Files:** `src/career_assistant/greetings.py`、`tests/test_career_greetings.py`。

- [x] System、生成/重生成 User、超长纠正提示词明确 200 字上限和计数口径。
- [x] 后端允许 200，拒绝 201；超长进入既有纠正流程，仍超长返回失败，保留事实/证据约束。
- [x] 测试边界字符和提示词、失败后不返回可发送文本。

## Task 3：编辑与实际提交约束

**Files:** `CareerGreetingDialog.vue`、`career-greeting-preview.js`、`browser-extension/job-library/boss-greeting.js` 及测试。

- [x] 编辑显示 200 字计数，生成/编辑超限不能提交，发送前再检查，覆盖失败重发路径。
- [x] 插件 normalizeGreetingPayload 在 preflight/send/retry 前拒绝超长正文，不截断。
- [x] 插件和下载版本统一 0.4.2，重新打包校验；用户需重新加载插件生效。

## Task 4：验证与交付

- [x] 执行相关 Python、前端和插件 Node、Chromium 回归及 Vite 构建。
- [x] 更新模块文档，记录根因、限制口径、验证及真实浏览器边界。


## 验证记录

- Python：`python -m pytest tests/test_career_greetings.py tests/test_career_greeting_api.py tests/test_job_search_card_payloads.py tests/test_chat_job_search_response.py tests/test_job_search_intent.py -q`，111 passed（两条现有 Starlette 弃用提示）。
- Node：web-ui `npm test` 287 passed；扩展 `npm test` 53 passed。
- Chromium：`npx playwright test e2e/job-search-card.spec.js e2e/chat-job-search-flow.spec.js e2e/boss-extension-connection.spec.js e2e/greeting-message-limit.spec.js --project=chromium --reporter=line`，21 passed。
- `npm run build` 通过，保留既有大 chunk 提示。
- 真实本地 PostgreSQL + FastAPI PATCH：count=15、十条 1200 字符 ID 保存成功，确认和历史重读成功；8193 字符 ID 更新被原子拒绝。测试会话已清理。
- 0.4.2 ZIP 共九个条目，29613 字节，与源码逐字节一致、CRC 正常；SHA-256 `5d0ce2e618b1fa7aa7f22d7cfd143f636e1f67737f68c0ab9a78cc551b5ea8e4`。本地下载 HTTP 200 且哈希一致，API 健康检查 200。
- 浏览器测试使用模拟生成、定位及扩展响应，未读取用户真实 GPS、调用真实模型或执行 BOSS 发送；未连接生产或部署。插件端限制需用户更新/重新加载 0.4.2 后生效。

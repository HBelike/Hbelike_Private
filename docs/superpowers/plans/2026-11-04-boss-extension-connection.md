# BOSS 插件连接误判修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.
> 用户已确认按分析结论修改。本轮在当前会话直接执行，并行分配插件、网页桥接和浏览器回归；不调用 using-superpowers，不重复请求授权。

**Goal:** 已安装插件的忙碌、超时或桥接重载不再触发安装弹窗。

**Architecture:** 插件保留业务串行队列，ping 在来源检查后独立响应。网页桥接合并检测请求、区分业务超时和连接异常；连接控制器忽略旧结果并响应 ready/disconnected。安装说明只由用户主动打开，确认缺能力仍可提示升级。

**Tech Stack:** Manifest V3、JavaScript、Vue 3、Node test、Playwright。

**Spec:** 当前会话用户确认的根因分析及修复范围。

## Global Constraints

- 仅本地实施、测试和打包，不连接生产或部署。
- 保留 BOSS 请求串行、节流、来源验证及既有岗位/面经/题面/打招呼能力。
- 不存储用户级“已安装”标记，不增加后台常驻保活。
- 保留无关未提交改动；PC 优先后检查手机。

## Task 1：插件检测独立响应

**Files:** `browser-extension/job-library/service-worker.js`、`content-script.js`、`manifest.json`、`tests/*.mjs`、`README.md`。

- [x] 增加“业务阻塞仍立即响应 ping、非法来源仍拒绝”测试。
- [x] 让 ping 绕过 requestQueue，业务队列保持原样。
- [x] 桥接发送携版本的 ready 和上下文失效 disconnected 通知，错误不再混同安装状态。
- [x] 扩展升级 0.4.1 并运行扩展全量 Node 测试。

## Task 2：网页桥接及连接控制

**Files:** `job-library-bridge.js`、`job-library-connection.js` 及各自测试。

**Interfaces:** `subscribeJobLibraryConnection(listener) -> unsubscribe`；`createJobLibraryConnection({ping,onChange})` 提供 check/handleSignal/dispose。

- [x] ping 同时调用复用在途 Promise；超时可重试，业务超时保持独立错误。
- [x] ready/disconnected 只接受当前 window 同源事件，通知前清理旧检测缓存。
- [x] 控制器按序号丢弃过期结果，卸载后不更新界面。
- [x] 用可控 Promise 覆盖旧失败晚于新成功、并发检测和重载恢复。

## Task 3：界面与回归

**Files:** `JobSearchWorkspace.vue`、`CareerGreetingDialog.vue`、`boss-extension-onboarding.js` 及测试、`e2e/boss-extension-connection.spec.js`。

- [x] 超时显示“暂时无法连接”，提供重新检测和查看安装说明，检测不自动弹安装。
- [x] 顶部连接按钮重新检测，ready 信号重新验证后台连接，不把脚本存在当作业务可用。
- [x] 发送链路的连接错误同样不弹安装；已确认旧版缺能力的升级提示保留。
- [x] 运行前端 Node、Chromium 回归及构建，PC 后检查手机宽度。

## Task 4：打包与文档

- [x] 执行 `python scripts/package_boss_extension.py`，逐项核对 0.4.1 ZIP 与源码、manifest 和下载地址。
- [x] 更新 `docs/job_library_module.md`，记录根因、调用链、验证和旧版本升级边界。


## 验证记录

- `npm test`（`browser-extension/job-library`）：50 项通过。
- `npm test`（`web-ui`）：273 项通过。
- `npx playwright test e2e/boss-extension-connection.spec.js e2e/job-search-card.spec.js e2e/chat-job-search-flow.spec.js --project=chromium --reporter=line`：16 项通过（含连接回归 7 项）。
- ZIP 0.4.1：29511 字节，九个条目逐字节匹配源码，CRC 正常；SHA-256 `424535168403bddaf14f39b4723719a28fbb9eeeab248423c7fed974c31bd2b1`。
- 浏览器回归采用模拟扩展/定位响应，未操作用户真实 BOSS 登录态、投递或打招呼；未部署生产。用户需要在浏览器扩展管理页更新/重新加载插件并刷新页面，使已安装实例运行 0.4.1。

- `npm run build`：通过；保留既有大 chunk 提示，未扩大为打包优化任务。0.4.1 本地下载 URL 返回 200，内容与本地 ZIP 哈希一致。

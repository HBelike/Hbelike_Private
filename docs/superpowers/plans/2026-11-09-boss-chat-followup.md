# BOSS 沟通复审修正实施计划

**Goal:** 按已批准设计补齐源分页、统一账号暂停、单活动执行端和独立发送操作，本地完成 PC 与扩展闭环验证。

**Architecture:** 复用 Vue、MV3、FastAPI 和 PostgreSQL。历史三表保留原语义；账号记录增加执行资格与暂停，发送意图存入独立操作表。页面先保存操作再分发扩展；扩展执行前回到发起页面同源检查服务端资格，取消不进入业务队列。

**Tech Stack:** Vue 3、Chrome MV3、FastAPI、SQLAlchemy/PostgreSQL、Node test、pytest、Playwright。

**Spec:** `docs/designs/2026-11-05-boss-chat-bridge-design.md`，用户已确认两轮建议并授权开始修改。

## 全局约束

- 仅本地修改、测试、构建，不连接生产，不真实发送消息。
- 当前混杂工作区原样保留，不重置或提交无关文件。
- PC 优先；页面标题、操作与注释使用中文。
- 不导出 Cookie/securityId，不把 HR 内容写入 LLM 历史。
- 真实账号验收未完成时准确记录待验；身份和源分页信号不可靠时显示缺口或停止发送。

## 模块与契约

后端接口统一前缀 `/api/career/external-chat`：

```text
POST /accounts/{id}/execution              {executor_id,takeover:false}
POST /accounts/{id}/execution/check        {executor_id,revision}
POST /accounts/{id}/execution/release      {executor_id,revision}
POST /accounts/{id}/pause                  {code,message}
POST /accounts/{id}/resume                 {executor_id,revision}
POST /conversations/{id}/operations        {client_operation_id,executor_id,revision,text}
GET  /accounts/{id}/operations
GET  /operations/{client_operation_id}
POST /operations/{client_operation_id}/transition
     {executor_id,revision,status,platform_message_id?,error?}
POST /operations/{client_operation_id}/cancel {reason}
```

执行接口返回 `{execution:{executor_id,revision,expires_at,paused,pause_code,pause_reason}}`，业务错误包含 `detail:{code,message}`。执行资格有界且按服务端时间核验；非当前端收到 `execution_busy` 或 `execution_lost`，暂停收到 `account_paused`。手动接管失效旧待发操作；仍可能提交的旧操作未收尾时不授予新提交资格。服务端状态持久化，不能依赖 Worker 内存。

操作接口返回 `{operation:{client_operation_id,account_id,conversation_id,text,status,created_at,expires_at,platform_message_id,error}}`；列表返回 `{operations:[]}`。状态为 pending/executing/submitting/submitted/accepted/failed/unknown/cancelled/expired。执行期限 30 秒；同 ID 同目标正文返回原记录，不再执行，改正文冲突。取消 pending 可直接取消；执行与提交不确定时先标记取消或待核对，只有执行端证明确未点击才取消。未知不重发。

网页 bridge 新增：

```javascript
listBossConversations({ accountId, page = 1, execution } = {})
getBossMessages({ accountId, peerId, securityId, page = 1, count = 50, execution })
replyBossMessage({ accountId, peerId, securityId, text, clientOperationId, execution })
cancelBossChatOperation({ clientOperationId, execution })
// execution = {account_id,executor_id,revision}
```

扩展新增 boss_chat_reply、boss_chat_cancel，分页原样传递；state 仅探测本人身份，不取得会话正文。列表/正文返回 `has_more:true|false|null`，null 表示源未给出可靠结束信号。发送由源页独立函数定位身份并保护人工草稿，不能直接调用首招 friend/add 或按正文查重。

## 任务一 后端执行资格与发送操作

**Files:** 新增迁移 `migrations/versions/20261109_37_external_chat_execution.py`、独立仓储 `src/career_assistant/persistence/external_chat_execution_repository.py`、独立路由 `src/career_assistant/web/external_chat_execution_router.py`、`tests/test_external_chat_execution.py`；修改原路由安装和清理。

- [x] 用真实 loopback PostgreSQL 隔离库验证跨用户归属、两个执行端互斥、手动接管、暂停重载保留。
- [x] 实现条件更新与数据库事务，非 owner 不能续期或执行；暂停不随接管解除。
- [x] 创建操作前冻结目标与正文；UUID 幂等、30 秒到期、状态转移、取消竞态和查询恢复。
- [x] 清理操作正文与历史使用相同 30 天规则；不向日志输出消息文本。

验证：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_external_chat.py tests/test_external_chat_execution.py -q
```

关键断言：第二执行端无法领取；接管后旧 revision 拒绝；同操作 ID 不产生第二次尝试；submitting 后失联进入 unknown；cancel 不把可能点击的操作伪装为 cancelled。

## 任务二 扩展分页、共享暂停和独立回复

**Files:** 修改 `browser-extension/job-library/boss-chat.js`、`boss-chat-page.js`、`service-worker.js`、`content-script.js`；新增独立回复/执行辅助模块和对应 Node 测试。

- [x] 真实分发器测试先复现 page/count 丢失与限流后第二页面继续执行。
- [x] 完整透传分页；has_more 缺失保留 null，不当成结束。
- [x] chrome.storage 保存账号暂停；服务端检查和本地暂停共同阻止已排队的后续读取/发送。
- [x] 在发起页同源执行资格/操作 API 检查，扩展执行时核对可见性和资格；取消走即时控制分支。
- [x] 单独实现持续回复，分别在填写和点击前核验当前账号、招聘者身份、草稿、验证状态；填入后失效不点击。
- [x] 只将点击记为 submitted；没有业务证据时状态保留 unknown，不按历史正文判重。

验证：

```powershell
node --test browser-extension/job-library/tests/*.test.mjs
node --check browser-extension/job-library/service-worker.js
```

## 任务三 PC 编排与交互

**Files:** 修改 `web-ui/src/external-chat-client.js`、`job-library-bridge.js`、`components/CareerMessagesPage.vue`，新增执行/发送客户端模块与测试，扩充 `web-ui/e2e/boss-inbox.spec.js`。

- [x] 首次只探测账号、注册空历史账号、申请空闲执行权；冲突时仅查历史，显示“在此接管”。
- [x] 轮询续期与执行检查；暂停统一写服务端，手动检测后恢复；后台停发请求，切回补齐。
- [x] 分页同步列表摘要，正文仅选中会话读取；每页保存，保留缺口提示与源翻页状态；重复页不循环。
- [x] 增加文本输入、明确发送、待核对状态、取消与来源核对入口；每条操作先服务端创建再分发。
- [x] 切会话/离页/隐藏/超时失效尚未提交的操作；迟到消息不能覆盖新会话。
- [x] 旧操作查询恢复不重发，相同正文的新消息必须新确认。

验证：

```powershell
# 在 web-ui 目录执行
node --test src/*.test.js
npx playwright test e2e/boss-inbox.spec.js e2e/boss-inbox-route.spec.js --project=chromium --reporter=line
npm run build -- --logLevel warn
```

## 任务四 集成、发行包与文档

**Files:** `manifest.json`、`scripts/package_boss_extension.py`、下载版本常量与指南、`tests/test_boss_extension_distribution.py`、方案与模块文档。

- [x] 发行版本升级到 0.6.0，新增模块全部进入 ZIP，源码字节校验通过。
- [x] 本地 mock 验证分页超过单页、两个页面暂停、接管旧端失效、取消插队与未知不重发。
- [x] 如可取得当前已登录浏览器，只做只读契约核验；没有会话时继续本地验证并记录真实待验。
- [x] 同步文档目标、调用链、依赖、验证及边界，不将模拟结果描述为真实已打通。

```powershell
.\.venv\Scripts\python.exe scripts/package_boss_extension.py
.\.venv\Scripts\python.exe -m pytest tests/test_boss_extension_distribution.py -q
```

计划按已批准设计执行，不额外请求实施确认；使用并行模块任务隔离文件所有权，集成与复核由主代理完成。不自动提交混杂工作区。


## 实施结果与剩余验收

本地模块、回归、构建及0.6.0发行包均已完成。扩展94、前端362、Python专项57、发行/完整迁移4、PC E2E10项通过；源页导航和人工草稿保护、取消插队、未知不重发、源证据提升和30天缓存清理均已覆盖。当前数据库已核对loopback并升级37，没有生产操作。

- [ ] 真实求职账号只读接口、分页/身份字段和已读行为验收：浏览器通道因无法可靠确认现有窗口URL自动停止，未取得真实账号证据。
- [ ] 具体已有会话和最终正文经确认后的真实单条收发闭环：本轮未触发真实发送。

真实验收先于真实使用；不能把上述本地结果宣称为双向聊天已经打通。

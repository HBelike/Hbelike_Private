# BOSS 入站消息接收实施计划

**Goal:** 用户在我们平台查看 BOSS 求职者会话和 HR 回复，由本地已登录浏览器读取，保存近 30 天历史；本轮不触发对外发送。

**Architecture:** 现有 MV3 扩展新增求职端只读动作，网页桥归一化后导入独立 PostgreSQL 外部聊天三表。独立招聘沟通子路由显示列表摘要和按需正文，页面可见时有限频率刷新，平台级错误暂停。

**Tech Stack:** Vue 3、Chrome MV3、FastAPI、SQLAlchemy/PostgreSQL、Node test、pytest、Playwright。

**Spec:** docs/designs/2026-11-05-boss-chat-bridge-design.md。

## 接口契约

- 扩展动作 boss_chat_state、boss_chat_list、boss_chat_messages；能力 boss_chat_read。
- 网页 bridge 方法 getBossChatState()、listBossConversations()、getBossMessages({accountId, peerId, securityId})。
- 扩展 state 返回 {account:{platform:'boss',platform_account_id,display_name},observed_at}。
- list 返回 {account,conversations,has_more,observed_at}。会话字段 platform_conversation_id、peer_id、peer_name、company、job_title、unread_count（可空）、last_message_at（可空）、last_message_preview、security_id（仅浏览器瞬态）、source_url。
- messages 返回 {account,conversation:{platform_conversation_id,peer_id},messages,has_more,observed_at}。消息字段 platform_conversation_id、platform_message_id、sender_id、direction（incoming/outgoing）、message_type（text/image/file/audio/unknown）、text、sent_at（ISO UTC）、source_url。
- POST /api/career/external-chat/sync 输入 {account,conversations:[],messages:[]}，会话中的 security_id 不下发服务器。返回 {account,imported_conversations,imported_messages}。
- GET /api/career/external-chat/accounts 返回 {accounts:[]}。
- GET /api/career/external-chat/accounts/{id}/conversations 返回 {conversations:[]}。
- GET /api/career/external-chat/conversations/{id}/messages?limit=100 返回 {messages:[],has_more:false}，支持 before=<最早消息内部UUID> 翻页，按(sent_at,id)稳定游标；服务端消息字段增加内部 id。
- 服务端会话字段增加内部 id、account_id、last_synced_at，账号增加内部 id、last_synced_at。

## 全局约束

复用当前登录用户 actor_id，只查询本人外部会话；不混入 LLM 消息表。平台稳定 UID 与消息 ID 按字符串保存；不能按姓名或正文去重。近 30 天按源消息原始 UTC 时间计算，导入和查询过滤，到期清理同时更新摘要。媒体仅展示类型并跳到源平台。PC 优先，无生产连接和真实发送。

## 任务一 扩展只读接收

- [x] 新建 boss-chat.js：端点参数、平台错误、账号/会话/消息归一化；boss-chat-page.js：本地已登录页面的受限读取。
- [x] 增加 boss_chat_read 能力与三个动作的 Content Script 白名单及 Service Worker 分发，ping 不等待业务队列。
- [x] 账号身份变化、环境拒绝/验证码/限流返回明确错误；不读取或导出 Cookie，不建立沟通关系。
- [x] Node 测试覆盖求职者路由、mid 去重依据、收发方向、秒/毫秒时间、非文本、账号不符、验证码/限流与实际分发。
- [x] 将新增运行文件加入版本 ZIP 并校验完整性。

## 任务二 历史持久化

- [x] 新增外部账号/会话/消息三表 Alembic 迁移、独立规范化服务和仓储、external_chat_router。
- [x] 按接口契约提供同步和历史 GET，使用轻量数据库服务，身份只取服务端请求 Actor。
- [x] 过滤近 30 天消息和摘要；未知源时间不冒充新消息；以真实消息 ID UPSERT。
- [x] API 生命周期启动补清及每 60 秒清理，停机取消，未配置数据库不启动；事务内删除并修正摘要。
- [x] pytest 覆盖用户隔离、重复同步、30 天边界、旧消息再导入、未知时间、摘要到期和历史翻页。

## 任务三 PC 收件页面

- [x] 新建 CareerMessagesPage.vue 和 external-chat-client.js，挂载 /career/messages，入口标题“招聘沟通”。
- [x] 先读取平台历史，再检测插件并同步列表；用户选择会话时读取正文、保存、刷新显示。
- [x] 页面可见且连接就绪时每 30 秒刷新列表及当前正文；验证码、登录失效、限流或账号变化暂停，手动处理后继续。关闭页面停止刷新，防止迟到响应覆盖新会话。
- [x] 显示最后同步时间、未读摘要、媒体类型、源平台入口和准确的空/离线/更新扩展状态；不把示例数据当真实收件。
- [x] Node/API mock 与 PC Playwright 覆盖 HR 新回复出现、30 天历史、会话切换、未连接/旧扩展、平台级暂停和重进历史。

## 集成验证

- [x] 运行专项 Python、前端与扩展 Node 测试、PC 浏览器回归、Vite 构建、ZIP 内容检查。
- [x] 如果本地可访问用户已登录 BOSS 浏览器，仅做经允许的只读接收验证；没有登录态时明确记录真实收件待验，不把 mock 结果声称为平台已打通。
- [x] 更新模块文档、调用链、开源归属及验证记录；不提交混杂工作区，不部署生产。

验收记录：扩展 Node 76 passed、前端 Node 324 passed、Python（含发行包）31 passed、PC Chromium 7 passed，Vite 构建通过。开发库已安全核对为本地 `127.0.0.1:54329/career_assistant` 并升级到 `20261109_35`，实际历史 API smoke 通过且无模拟聊天写入。代码链路和模拟收件已完成，真实 BOSS 求职端收件仍待已登录账号验收，不能将上述结果视为真实 HR 回复已送达。完整边界和命令见 `docs/modules/boss-inbox.md`。


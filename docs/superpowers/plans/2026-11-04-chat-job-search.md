# 对话内找工作实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.
> 本次沿用当前会话执行，独立意图、组件和定位模块分配给子代理；不调用 using-superpowers，不要求重复授权。

**Goal:** 用户表达寻找岗位任务后，在聊天内搜索和确认真实岗位。

**Architecture:** 保留现有 Turn、岗位桥接与上下文绑定链路。增加独立意图路由、消息卡片数据、对话组件和临时城市定位解析（浏览器直连 BigDataCloud）；所有卡片变化持久化并在历史读取中恢复。

**Tech Stack:** Python、FastAPI、SQLAlchemy/PostgreSQL、Vue 3、浏览器 Geolocation、Node test、pytest。

**Spec:** `docs/superpowers/specs/2026-11-04-chat-job-search-design.md`

## Global Constraints

- 中文 UI 和代码注释；英文标识符。
- 只在本地实施和验证，不部署生产。
- 保留现有未提交改动；PC 优先，手机随后；不专项适配平板。
- 不自动投递、打招呼，不持久化定位坐标。

## Task 1：意图识别

**Files:** `src/career_assistant/job_search_intent.py`、`tests/test_job_search_intent.py`。

**Interfaces:** `detect_job_search_intent(text, *, previous_card=None, history=None, classify=None) -> JobSearchIntent`，动作包含 open/update/cancel/clarify/normal，返回明确条件、证据、混合任务和自动搜索标记。

- [x] 用正反例覆盖搜索、咨询、否定、简历任务、混合任务和补充条件。
- [x] 实现规则确定路径与受约束的可选语义判断，失败回普通对话。
- [x] 执行 `python -m pytest tests/test_job_search_intent.py -q`。

## Task 2：消息数据与回复集成

**Files:** 消息迁移、`persistence/records.py`、`persistence/conversation_repository.py`、`response_runner.py`、`turn_worker.py`、`web/router.py`、新卡片负载模块与测试。

**Interfaces:** 消息可选 `ui_payload`；`PATCH /api/career/conversations/{conversation_id}/messages/{message_id}/job-search` 返回更新消息；最终回复含 `updated_messages`。

- [x] 增加可空 JSONB 字段及完整历史查询映射。
- [x] 复用所有权边界实现条件/状态/摘要更新，测试越权、非法状态和有限摘要。
- [x] 在同步与流式 Runner 使用一致的意图分流；卡片独立于普通回答，混合任务继续正常回答。
- [x] 最终事件和历史接口均传递卡片；执行相关 Python 回归。

## Task 3：城市定位

**Files:** `web-ui/src/job-search-location.js`、对应 Node 测试、`docs/chat_job_search_location.md`。

**Interfaces:** `resolveJobSearchCity({cities, requestPermission})` 返回城市和定位状态；浏览器直接使用本次设备定位坐标调用免费客户端接口，不新增后端代理。

- [x] 覆盖已授权、待授权、拒绝、定位失败和城市目录不匹配。
- [x] 已授权自动定位，点击后才请求新权限；逆地理编码失败返回可理解提示。
- [x] 验证坐标边界、外部失败以及坐标不在本项目持久化。

## Task 4：对话卡片

**Files:** `CareerJobSearchCard.vue`、`JobSearchWorkspace.vue`、`CareerAssistantPage.vue` 和前端测试。

**Interfaces:** 卡片传入 message/active/fresh/busy/error，发出 confirm(job) 和 state-change(patch)；Workspace 增加对话模式和条件输入，沿用独立模式。

- [x] 实现定位提示、搜索列表详情、设为目标岗位和收起。
- [x] 只允许实时新卡片自动检索一次；历史只读摘要不重新查询。
- [x] 接入 PATCH 与完成事件 updated_messages，准确显示分析状态。
- [x] 执行 `npm test`、`npm run build`，本地浏览器验证 PC 后再检查手机。

## Task 5：收尾

- [x] 检查新模块调用链、依赖和失败路径，更新 `docs/career_assistant_module.md`。
- [x] 记录实际验证结果与外部接口/扩展依赖边界，交付可复查文件。

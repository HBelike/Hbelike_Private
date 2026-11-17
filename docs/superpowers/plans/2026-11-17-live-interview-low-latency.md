# 面试大师低延迟 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将面试官 ASR final 到回答首字的正常网络 P95 控制在 3 秒目标内，并能区分 ASR、FIFO 排队和模型生成延迟。

**Architecture:** 保持 final-only 与 FIFO 主链不变，在 DashScope endpointing、模型请求参数、服务端单调时钟和浏览器展示四个已有边界做小幅调整。所有时延通过现有 WebSocket 事件传递，不增加数据库迁移或新服务。

**Tech Stack:** Python 3.12、FastAPI WebSocket、DashScope WebSocket、Vue 3、Node test runner、pytest

**Spec:** `docs/superpowers/specs/2026-11-17-live-interview-low-latency-design.md`

## Global Constraints

- 只对面试官 final 话语自动回答，partial 不触发。
- 保持 FIFO、四条追问上下文和本场问答结构不变。
- 默认 `max_sentence_silence=500ms`，回答 `thinking=False`、`max_tokens=1024`。
- 只在本地修改和验证，不部署生产。

---

### Task 1: DashScope 低延迟 endpointing

**Files:**
- Modify: `src/career_assistant/live_interview/asr/dashscope_realtime.py`
- Modify: `src/career_assistant/live_interview/web.py`
- Test: `tests/test_live_interview_core.py`
- Test: `tests/test_live_interview_web.py`

**Interfaces:**
- Produces: `DashScopeRealtimeAsrProvider(..., max_sentence_silence_ms=500, vad_model="near_meeting_16k")`
- Consumes: `DASHSCOPE_ASR_MODEL`、`DASHSCOPE_ASR_MAX_SENTENCE_SILENCE_MS`、`DASHSCOPE_ASR_VAD_MODEL`

- [x] **Step 1: 写入默认 3.1 模型、500ms 断句和参数校验的失败测试**
- [x] **Step 2: 运行 `python -m pytest tests/test_live_interview_core.py tests/test_live_interview_web.py -q` 并确认失败**
- [x] **Step 3: 实现 Provider 参数与环境变量解析，3.1 默认发送 `near_meeting_16k`**
- [x] **Step 4: 重跑定向测试并确认通过**

### Task 2: 实时回答输出预算

**Files:**
- Modify: `src/career_assistant/live_interview/answer_service.py`
- Modify: `src/career_assistant/live_interview/web.py`
- Test: `tests/test_live_interview_services.py`
- Test: `tests/test_live_interview_web.py`

**Interfaces:**
- Produces: `LIVE_ANSWER_OPTIONS = CompletionRequestOptions(temperature=0.2, max_tokens=1024, thinking=False)`

- [x] **Step 1: 写入非思考、1024 Token 和 800 字提示约束的失败测试**
- [x] **Step 2: 运行定向测试并确认失败**
- [x] **Step 3: 实现固定实时回答参数并传入共享模型客户端**
- [x] **Step 4: 重跑定向测试并确认通过**

### Task 3: 服务端分段延迟事件

**Files:**
- Modify: `src/career_assistant/live_interview/session_manager.py`
- Test: `tests/test_live_interview_services.py`

**Interfaces:**
- Produces: `answer.started.queue_wait_ms`、首个 `answer.delta.first_token_ms`、`answer.completed.generation_ms`

- [x] **Step 1: 使用可控单调时钟写入三个事件字段的失败测试**
- [x] **Step 2: 运行服务测试并确认失败**
- [x] **Step 3: 在队列项和回答生命周期中记录单调时间，毫秒值取非负整数**
- [x] **Step 4: 验证 FIFO 与失败后继续处理测试仍通过**

### Task 4: 浏览器首字指标与弱网背压

**Files:**
- Modify: `web-ui/src/browser-live-interview/socket.js`
- Modify: `web-ui/src/browser-live-interview/view.js`
- Modify: `web-ui/src/components/BrowserInterviewMasterPage.vue`
- Test: `web-ui/src/browser-live-interview-core.test.js`
- Test: `web-ui/src/browser-live-interview-view.test.js`

**Interfaces:**
- Produces: `formatLatency(milliseconds)`、首字延迟可见文案、`MAX_BUFFERED_BYTES=256000`

- [x] **Step 1: 写入 Flash 优先、延迟格式和 256KB 背压的失败测试**
- [x] **Step 2: 运行 `npm test -- --test-name-pattern="browser live interview|interview master"` 并确认失败**
- [x] **Step 3: 实现模型选择、指标展示和缓冲上限**
- [x] **Step 4: 重跑 WebUI 测试并确认通过**

### Task 5: 回归与记录

**Files:**
- Modify: `docs/fixes/2026-09-01-live-interview-final-trigger-and-followup.md`

**Interfaces:**
- Consumes: 前四项全部实现与测试结果

- [x] **Step 1: 运行实时面试 Python 定向测试**
- [x] **Step 2: 运行后端全量 pytest**
- [x] **Step 3: 运行 WebUI 全量测试和 production build**
- [x] **Step 4: 记录目标、调用链、取舍、依赖、实际结果与生产未部署边界**


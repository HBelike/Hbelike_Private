# 面试大师逐句触发与连续追问实施记录

## 目标

修复面试官使用陈述式或祈使式表达时不自动回答的问题，并保证短时间连续到达的问题按顺序完成；追问继续使用现有短期记忆，只把历史范围从当前问题之前的 2 条扩大到 4 条。

## 根因

旧 `RuleBasedQuestionDetector` 要求文本包含问号或固定疑问关键词。`“分析一下日股。”`、`“评价一下这个方案。”` 等真实作答任务会在检测入口被过滤。检测成功后的旧状态机又会以 `superseded` 取消正在生成的上一题，扩大触发范围后会放大丢回答风险。

旧追问 Prompt 使用 `recent_conversation[-2:]`。当前问题在检测前已经进入该列表，导致当前问题重复传入，真正可用的历史只有更早的一条。

## 实现

### 逐句触发

- 只有 `interviewer + final` 可以自动触发。
- 取消问号、疑问词和请求词门槛；无问号问句、陈述式任务和祈使式任务统一进入回答流程。
- 整句规范化后精确过滤“嗯、哦、好的、行、可以、OK”等纯填充词。
- 填充词只做整句匹配；`“好的，请继续说明缓存一致性。”` 仍会触发。
- 同一连接中，规范化文本在 3 秒内完全重复时只触发一次；不使用模糊或语义去重。
- 问题类型和追问分类继续用于回答风格，但不再决定是否生成。

### FIFO 回答状态机

- 自动问题在到达时捕获前面最近 4 条转写并进入内存 FIFO。
- 会话使用单个 worker 顺序生成，新的自动问题不取消当前答案。
- `question.detected` 只在队列项真正开始生成时发送，因此前端仍保持单活动问题模型。
- 单题失败后继续处理下一题。
- 手动回答仍可显式取消当前生成并优先执行；重新生成沿用当前 `question_version` 并递增 `attempt`。
- 连接结束时取消当前 worker 并清空内存队列，不增加跨连接恢复。

### 四条追问上下文

- 每个自动问题在当前 final 写入列表前捕获历史快照。
- `FOLLOW_UP` 最多携带当前问题之前的 4 条转写，当前问题继续单独传入。
- 非追问保持现有无历史 Prompt。
- 开启候选人麦克风时，其 final 可以进入当前连接的追问快照，但继续不持久化、不触发回答。
- 不加入模型历史答案、长期摘要或新的 Token 预算。

## 调用链

```text
ASR final
  → TranscriptAssembler
  → 捕获前面 4 条转写
  → 实时转写展示与原有持久化 hook
  → interviewer final 确定性门禁
  → 填充词/3 秒重复则停止自动处理
  → 其他文本分类并进入 FIFO
  → 单 worker 发送 question.detected
  → 根据 FOLLOW_UP 选择是否携带四条历史
  → 流式生成并持久化答案
  → answer.completed 进入现有“本场问答”
```

## 技术取舍

- 没有增加 LLM 意图识别调用，避免额外延迟、费用和模型故障面。
- 没有修改前端状态结构；通过延后 `question.detected` 保持现有单活动版本协议。
- 只做精确填充词和短窗口重复过滤，避免模糊规则压掉真实重复追问。
- 队列和历史快照只保存在当前连接内存，符合本次功能边界，不增加恢复或容灾设计。

## 验证结果

### 后端

```text
.\.venv\Scripts\python.exe -m pytest \
  tests/test_live_interview_core.py \
  tests/test_live_interview_services.py \
  tests/test_live_interview_web.py -q

53 passed, 1 warning
```

唯一 warning 来自 FastAPI TestClient 对 `httpx` 兼容层的既有弃用提示，与本次修改无关。

随后运行仓库完整 Python 回归：

```text
.\.venv\Scripts\python.exe -m pytest -q

581 passed, 5 warnings
```

5 条 warning 均为既有 Starlette/FastAPI 弃用提示，没有测试失败。

新增覆盖包括：

- `“分析一下日股。”`、`“评价一下这个方案。”`、`“展开。”` 和无标点问句触发。
- 纯填充词过滤及带填充词前缀的完整任务放行。
- 3 秒完全重复保护及窗口外再次触发。
- 两个快速问题按 FIFO 完成且不产生自动取消。
- 第一题生成失败后第二题继续。
- 追问只读取入队时的前面 4 条，不重复当前问题。

### 前端

```text
cd web-ui
npm test

216 passed
```

```text
cd web-ui
npm run build

构建成功，4113 modules transformed
```

Vite 仍提示既有主包超过 500 kB；本次没有修改前端代码或引入新依赖，警告不属于此次功能回归。

## 后续边界

- 尚未使用真实会议音频执行人工验收；需验证 ASR final 分段、填充词表现和连续问题实际等待感受。
- 首版不做语义去重、主题摘要、跨连接队列恢复或候选人文本持久化。
- 本次仅完成本地实现、自动测试和生产构建，未部署生产。

## 2026-11-17 低延迟优化

### 线上只读诊断

- 生产主机负载、内存、容器重启和 OOM 均正常，不是算力拥塞。
- 最近一场真实会话中，ASR final 到回答任务启动约为 4～6ms；三次完整回答生成分别约为 3.35s、1.76s、1.54s。
- 主要等待发生在 ASR final 之前：浏览器到香港服务的初始连接约 1.1～2.5s，香港服务到北京 DashScope 首次响应约 0.95s；原断句静音阈值为 800ms。
- 诊断过程只读，没有修改生产配置、容器或数据。

### 本地实现

- DashScope 默认模型升级为 `qwen-audio-3.1-asr-flash-streaming`，默认断句阈值从 800ms 降为 500ms，并对 3.1 模型启用 `near_meeting_16k` VAD。
- 新增 `DASHSCOPE_ASR_MAX_SENTENCE_SILENCE_MS` 与 `DASHSCOPE_ASR_VAD_MODEL` 环境变量，保留 200～6000ms 可调范围。
- 实时回答关闭 thinking，固定 `temperature=0.2`、`max_tokens=1024`，提示词要求 800 个中文字符以内并优先输出可直接开口的第一句话。
- WebSocket 事件新增 FIFO 排队、模型首字和完整生成耗时；页面显示端到端首字与模型首字耗时。
- 浏览器音频发送背压从 1MB 收紧到 256KB；无显式偏好时优先选择可用的 Flash 回答模型。
- final-only、FIFO、四条追问上下文、本场问答和持久化结构均保持不变。

实现依据为[阿里云百炼实时语音识别文档](https://help.aliyun.com/zh/model-studio/real-time-speech-recognition-user-guide)，目标为正常网络下 ASR final 到页面首字 P95 不高于 3 秒；该目标需要生产部署后的真实音频样本继续验证，不能仅由本地单元测试保证。

### 验证结果

```text
.\.venv\Scripts\python.exe -m pytest tests/test_live_interview_core.py tests/test_live_interview_services.py tests/test_live_interview_web.py -q
58 passed, 1 warning

.\.venv\Scripts\python.exe -m pytest tests -q
876 passed, 66 skipped, 19 warnings

cd web-ui
node --test src/browser-live-interview-core.test.js src/browser-live-interview-view.test.js
20 passed

npm test
373 passed, 1 failed

npm run build
构建成功，4149 modules transformed
```

WebUI 全量测试唯一失败来自工作区既有未提交的 `JobSearchWorkspace.vue` 与 `job-search-extension-gate.test.js` 断言不一致，与实时面试模块无关；定向复跑结果一致。仓库根目录直接运行 `pytest -q` 会收集既有 `tmp/production-release-20261110` 副本并产生测试模块重名，因此正式全量回归限定为 `tests/`。Vite 仍只有既有的 500KB chunk 警告。

本次仅完成本地代码、配置示例、测试与文档，尚未部署生产。

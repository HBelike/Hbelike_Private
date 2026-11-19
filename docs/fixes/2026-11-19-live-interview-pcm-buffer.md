# 面试音频短抖动缓冲修复

目标是修复已证实的 `aedaea7` 回归：每轨20帧约2秒积压或3秒发送等待就终止整场。当前改动仅覆盖本地 relay、transport 默认值及验证，尚未发布。

## 预算与取舍

- 现有输入为24kHz、单声道PCM16，每秒48,000字节。默认每轨待发送PCM上限6秒，即288,000字节，包含正在写入的帧。5秒上游停写需要240,000字节，额外100ms帧突发需要4,800字节，剩余43,200字节约900ms作为调度余量。
- 默认单次发送等待为6秒，覆盖5秒短停写；收包与每轨writer继续隔离，不把 ASR 发送重新放回 WebSocket 收包循环。
- 两轨最多保留576,000字节待发送PCM；该额度不随面试已进行时长增长。writer完成或取消当前操作后立即归还额度，并释放已发送帧引用。
- 默认不使用固定帧数上限，小帧和合并帧按实际PCM字节记账。显式 `max_frames` 仍可用于兼容调用和旧策略对照测试；它不会成为默认生产策略。
- 空PCM不能入队。没有PCM字节的commit单独限制为64条待处理控制事件，包含当前执行中的控制事件，避免绕过有限预算。
- 真正超过PCM预算时返回 `asr_audio_budget_exceeded`，超过控制预算时返回 `asr_control_budget_exceeded`，消息明确说明超界。显式帧数限制仍返回 `asr_audio_backpressure`。不静默丢帧，不把超界记为短抖动保护成功。

这些数字是已批准5秒保护范围的有限等待预算，并非无限断网保护。持续输入吞吐高于上游吞吐仍会耗尽预算。此修复不实现真实ASR断线后的未确认音频重放，不证明Provider已识别发送完成的PCM，也不证明一小时真实Provider连接已验收。

## 调用链与修改范围

`web._run_live_transport`持续读取消息，音频交给`LiveAudioRelay.enqueue`；每轨writer独立顺序写入ASR。入队先核对该轨PCM字节和控制事件预算，写入结束的`finally`归还额度并完成队列计数。Ping仍经过原有控制消息路径。

修改文件：`audio_relay.py`负责预算及记账；`web.py`仅调整对应导入和transport默认参数；`test_live_interview_transport.py`增加保序、心跳及超界验证。本隔离树不携带其他未提交改动，不修改manager、ASR Provider、前端、模型或迁移。

## 验证

先在旧默认行为上运行新用例：5秒双轨保护失败；旧显式20帧/3秒对照按预期提前断开。随后实现PCM预算并复测。

- 真实本机TCP/Uvicorn/WebSocket，两个ASR writer首包各暂停5秒；客户端额外突发100ms音频，随后继续100ms分帧和Ping。新默认须保持同一连接，两轨各70帧按序、PCM SHA256完全一致，阻塞期间仍持续回复Pong。
- 相同负载显式启用旧20帧/3秒限制，须复现提前1013断开，不能只检查常量。
- 混合尺寸帧验证in-flight计入PCM预算；100个极小帧验证不再撞任意20帧限制。
- 空PCM拒绝、控制事件有界、默认字节超界经真实TCP发出明确错误。
- 加速发送1800个2秒PCM包，即一小时等价音频总量，逐包确认成功后预算回零。这是累计记账测试，不是一小时真实墙钟/Provider压测。

最终命令：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -q -s tests/test_live_interview_transport.py tests/test_live_interview_web.py
```

来源工作区结果：38 passed、1 skipped，24.11秒（含该工作区原有其他Web测试）；本隔离树复验结果另记。跳过的是按环境变量显式启动的持续soak，不把它写成本轮已执行。只有既有Starlette/Uvicorn/websockets弃用提示；`git diff --check`通过。

同负载对照：旧显式20帧/3秒策略提前返回`asr_audio_backpressure`，两轨均尚未写完首帧，期间收到10个Pong；新默认保持同一连接，两轨各70/70帧及PCM哈希一致，共35个Pong，Pong P95为1.694ms。该指标来自本机真实TCP，ASR与存储为替身，不代表公网RTT或真实Provider长场稳定性。

零PCM、commit上限、包含in-flight的混合字节预算、默认超界经真实TCP提示、100个小帧以及1800包一小时等价PCM记账全部通过。1800次写完后的待发送字节均回到零，累计处理172,800,000字节；这不等于一小时墙钟内存或真实语音验收。

完整日志：`tmp/live-interview-pcm-buffer/transport-tests.log`。本轮未连接生产、未发付费ASR/模型请求，也未部署。


## 最小热修复隔离复验

隔离分支`codex/live-interview-pcm-hotfix`从已投产代码对应的`0137a4f`建立，只移入relay预算、web的import/transport默认值、对应测试和本记录。web的AST变化仅为audio_relay导入和`_run_live_transport`；manager、ASR Provider、answer_service、persistence、前端、迁移、构建脚本均保持基线。

未带入源工作区尚未上线的resume、SSE关闭、fatal事件、历史与Prompt候选。旧Web不依赖新协议：仍接收现有error/message与1013关闭；5秒短抖动成功时保持原连接，无需新页面。

隔离树的transport与原Web定向复验为35 passed、1 skipped，24.45秒。差额来自未携带源工作区额外的3个Web/fatal用例；不是删除本修复断言。旧20/3同负载仍失败；新默认两轨70/70帧及PCM哈希完整、35个Pong，P95为1.749ms。使用源工作区现有venv绝对路径、在隔离cwd运行，导入路径已检查确为隔离树。

日志：`tmp/live-interview-pcm-buffer/isolated-tests.log`；AST、文件摘要与未改动模块核对见`tmp/live-interview-pcm-buffer/minimal-hotfix-audit.json`。本阶段未commit、push、prepare构建或部署。

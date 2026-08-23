# Chrome 浏览器面试大师

## 设计目标

该模块把“面试大师”作为现有求职助手中的浏览器工具提供，不依赖 Electron、浏览器插件或会议平台 API。用户在 Chrome 中点击求职助手顶部的“面试大师”，系统打开一个固定命名的站内窗口；用户在 Chrome 强制显示的授权窗口中选择“整个屏幕”并开启“共享系统音频”后，页面统一捕获电脑最终播放的声音，实时转写面试官问题，并把问题直接交给现有回答模型流式生成统一中文的解题思路与参考回答。浏览器内会议也可直接选择对应标签页并共享标签页音频。

默认只采集用户授权的电脑系统音频并固定识别为面试官，不区分腾讯会议、飞书、QQ、微信或其他播放来源。应试者麦克风是可选展示通道，不参与问题回答上下文。模块不采集摄像头、不读取简历、岗位或面经资料、不保存原始音频，也不实现隐藏采集、自动替用户发言或绕过浏览器授权。

## 技术取舍

- 只支持桌面版 Chrome。系统音频使用标准 `getDisplayMedia({ audio: true, systemAudio: "include" })`，因此必须由用户手势触发并在 Chrome 原生选择器中选择“整个屏幕”、开启“共享系统音频”。网页不能绕过或代替这个授权步骤；这是浏览器安全边界，不是会议平台绑定。浏览器内会议仍可选择对应标签页。
- 不开发插件。入口与实时界面均属于现有 Vue SPA，路由为 `/career/interview-master`；采用固定命名浏览器窗口，重复点击优先聚焦同一窗口，弹窗被拦截时降级为普通新标签页。
- `AudioWorklet` 将音轨转为 24 kHz 单声道 PCM16，并按 100 ms 合并发送，避免 10 ms 小帧造成过多 WebSocket 消息。
- 浏览器通过同源 `wss://<domain>/api/career/live-interviews/{id}/stream` 连接 FastAPI。音频只从浏览器发往服务端，DashScope API Key 和回答模型凭据始终保留在服务端。
- 实时转写使用阿里云百炼 `qwen-audio-3.0-asr-flash-streaming`；回答继续使用现有求职助手中配置的文本模型。ASR 负责语音转文字，普通 LLM 负责把识别出的面试官问题生成为中文答案。
- 回答上下文以当前问题为主。只有识别到明显追问时，才使用内存中的最近两轮帮助补全指代；浏览器端应试者转写不写入数据库，原始 PCM、partial 转写和 Provider 原始错误正文均不持久化。
- 页面支持当前转写、当前问题、30 秒要点、完整流式答案、本次会话历史、暂停、重新选择分享源、手动生成、重新生成、结束面试，以及 Chrome Document Picture-in-Picture（浏览器支持时）。

## 调用链

```text
求职助手“面试大师”按钮
  → /career/interview-master 固定命名窗口
  → POST /api/career/live-interviews/sessions（client_kind=browser）
  → WSS /api/career/live-interviews/{id}/stream
  → 用户授权整个屏幕的系统音频，或浏览器会议标签页音频
  → AudioWorklet → PCM16/24kHz/100ms
  → DashScope Qwen-Audio Streaming
  → 面试官 final 转写 → 问题识别与 question_version
  → 现有文本模型 → 中文回答增量
  → 浏览器实时渲染要点与完整答案
```

新问题递增 `question_version` 并取消旧回答；前端依据 `question_version` 和 `delta_index` 丢弃旧问题或乱序迟到的答案增量。页面退出、结束面试、分享被 Chrome 停止或连接断开时，会停止所有 MediaStream Track、AudioWorklet、AudioContext 和 WebSocket。

## 依赖与生产配置

- 数据库迁移：`20260823_19`，在实时面试会话上增加 `client_kind` 与 `candidate_audio_enabled`。
- ASR：服务端环境变量 `DASHSCOPE_API_KEY`；默认模型 `qwen-audio-3.0-asr-flash-streaming`。
- Caddy：`Permissions-Policy: camera=(), microphone=(self), geolocation=()`，只允许同源页面请求可选麦克风，继续禁止摄像头和定位。
- Nginx 与 Vite：`/api/career/` 支持 WebSocket Upgrade，同时保留现有 SSE 行为。
- 浏览器限制：生产环境必须使用 HTTPS；Chrome 不允许页面静默选择分享源。Windows 桌面 Chrome 优先引导“整个屏幕 + 共享系统音频”，把不同通话软件的输出统一视为电脑音频；若浏览器或系统不提供该开关，只能改用带声音的浏览器标签页，或回到原生 loopback 方案。

## 验证结果

截至 2026-08-23：

- 后端完整测试 `189 passed`；前端完整测试 `52 passed`；Vite 生产构建通过。
- 仓库受控的 297 个 Python 文件全部通过 `py_compile`。未纳入 Git 的 `src/test.py` 语法问题不属于本模块，未修改。
- 本地数据库已升级至 `20260823_19`；真实百炼凭据已完成浏览器会话创建、Qwen-Audio WebSocket 握手、仅面试官通道 `session.ready` 与正常结束验证，过程中未输出密钥或保存原始音频。
- 生产版本已部署到提交 `94190a3`；PostgreSQL 从 `20260821_16` 顺序迁移到 `20260823_19`，API 与 Web 容器健康。
- 公网 `https://xingxingtech.cn/career/interview-master` 返回 200；健康接口返回 200；权限头为 `camera=(), microphone=(self), geolocation=()`。
- 公网 WSS 已到达 FastAPI，并按设计拒绝未登录连接；生产 API 容器确认 ASR Key 已注入，未输出 Key 内容。
- 准备页读取模型配置时展示浏览器检查、模型读取、完成准备三段进度和百分比；请求未完成前平滑推进但最多停在 92%，只有真实返回成功后才显示 100%。相关前端完整测试现为 `53 passed`，生产构建通过。
- 2026-08-24 本地 8% 卡死问题已修复：Chrome 的 `audioWorklet` 是 `AudioContext` 实例 getter，旧检测从 prototype 读取会抛出 `Illegal invocation`，导致 `onMounted` 在配置请求前中断。检测现只验证 `AudioContext` 构造器，并保证即使能力检测异常也继续加载配置、显示可操作错误。本地 Vite 代理配置接口返回 200，ASR 为 ready，真实 Qwen 会话收到仅面试官通道的 `session.ready`；前端完整测试 `54 passed`，生产构建通过。本次仅本地验证，未部署生产。
- 2026-08-24 电脑声音采集已从“标签页限定”修正为“系统音频优先”：请求显式使用 `displaySurface: "monitor"`、`monitorTypeSurfaces: "include"` 与 `systemAudio: "include"`，并允许切换共享表面。该配置只影响 Chrome 授权器的推荐与可用选项，不能跳过每次用户授权。本地 Vite 已提供新参数，旧的 `systemAudio: "exclude"` 不再存在；前端完整测试 `54 passed`，生产构建通过，未推送或部署生产。

## 尚未伪报通过的边界

- 当前 Codex 环境未安装可控制 Chrome 的扩展，因此没有自动点击 Chrome 原生分享选择器。真实桌面通话验收需要用户手动选择“整个屏幕 + 共享系统音频”；该授权无法由网页代替。
- 尚无真实问题结束到回答首字的 P50/P95、长时间通话稳定性以及中文、英文、中英混合和跨行业术语准确率数据。
- 纯浏览器方案的可靠边界是 Chrome 在当前 Windows 版本上能否为“整个屏幕”返回系统音频轨；若没有该轨道，网页没有权限退回到静默监听整机音频。

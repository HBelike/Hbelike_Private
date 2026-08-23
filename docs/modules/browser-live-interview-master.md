# Chrome 浏览器面试大师

## 设计目标

该模块把“面试大师”作为现有求职助手中的浏览器工具提供，不依赖 Electron、浏览器插件或会议平台 API。用户在 Chrome 中点击求职助手顶部的“面试大师”，系统打开一个固定命名的站内窗口；用户主动选择正在播放面试声音的标签页并勾选“分享标签页音频”后，页面实时转写面试官问题，并把问题直接交给现有回答模型流式生成统一中文的解题思路与参考回答。

默认只采集被分享标签页的系统音频并固定识别为面试官。应试者麦克风是可选展示通道，不参与问题回答上下文。模块不采集摄像头、不读取简历、岗位或面经资料、不保存原始音频，也不实现隐藏采集、自动替用户发言或绕过会议平台告知。

## 技术取舍

- 只支持桌面版 Chrome。系统音频使用标准 `getDisplayMedia({ audio: true })`，因此必须由用户手势触发并在 Chrome 原生选择器中选择标签页、开启“分享标签页音频”。网页不能绕过或代替这个授权步骤。
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
  → 用户选择 Chrome 标签页并分享标签页音频
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
- 浏览器限制：生产环境必须使用 HTTPS；Chrome 不允许页面静默选择分享源，也不保证所有平台都能分享“整个屏幕”的系统音频，因此产品文案明确引导选择标签页。

## 验证结果

截至 2026-08-23：

- 后端完整测试 `189 passed`；前端完整测试 `52 passed`；Vite 生产构建通过。
- 仓库受控的 297 个 Python 文件全部通过 `py_compile`。未纳入 Git 的 `src/test.py` 语法问题不属于本模块，未修改。
- 本地数据库已升级至 `20260823_19`；真实百炼凭据已完成浏览器会话创建、Qwen-Audio WebSocket 握手、仅面试官通道 `session.ready` 与正常结束验证，过程中未输出密钥或保存原始音频。
- 生产版本已部署到提交 `94190a3`；PostgreSQL 从 `20260821_16` 顺序迁移到 `20260823_19`，API 与 Web 容器健康。
- 公网 `https://xingxingtech.cn/career/interview-master` 返回 200；健康接口返回 200；权限头为 `camera=(), microphone=(self), geolocation=()`。
- 公网 WSS 已到达 FastAPI，并按设计拒绝未登录连接；生产 API 容器确认 ASR Key 已注入，未输出 Key 内容。

## 尚未伪报通过的边界

- 当前 Codex 环境未连接可控制的 Chrome 会话，因此没有自动点击 Chrome 原生分享选择器，也没有把真实微信/会议标签页音频送入生产链路。该项必须在已登录用户的 Chrome 中手动选择“标签页 + 分享标签页音频”完成实听验收。
- 尚无真实问题结束到回答首字的 P50/P95、长时间通话稳定性以及中文、英文、中英混合和跨行业术语准确率数据。
- Chrome 标签页捕获无法通用覆盖所有原生桌面通话软件；纯浏览器方案的可靠边界是可被 Chrome 选择且能提供共享音频轨的标签页。


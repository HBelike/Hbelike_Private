# 实时面试连接心跳超时修复与发布

## 现象与定位

用户线上会话约 70 秒后提示 `keepalive ping timeout`。线上确认 API 镜像为 `sha256:5a8fcc1f2f0e8a7802709e9b2f080af10e9fc2b8fe7b34727ba7d0d57d78afe6`，仍为 11 月 10 日快照 `baa5f320b873`；没有 OOM/重启导致断连的证据。旧代码缺少断连原因日志，因此不能从既有日志断言每次断连的唯一原因。

源码与本地真实 WebSocket 复现指向背压传播：浏览器音频到达后，入口直接 await 上游 ASR send；上游写入卡住时不再读取浏览器消息，协议入站队列满，Ping/Pong 处理被延后，从而产生与截图相同的超时。[websockets 官方说明](https://websockets.readthedocs.io/en/stable/topics/keepalive.html)也说明未及时读取入站消息会造成心跳超时。

## 设计与调用链

浏览器收包 → 解析 → 每音轨有界 FIFO → 独立 ASR writer。非音频控制消息与心跳继续被处理；每音轨最多排队 20 帧（正常约 2 秒），单次 ASR 发送最多等待 3 秒。积压满或写入失败时返回稳定错误码，终止转发并释放任务，不静默丢弃音频，也不靠延长 ping_timeout 掩盖问题。

主 WebSocket 同时监控音频 writer 和事件 sender，任一后台任务失败都会结束连接，避免只有后台失效、前台仍显示正在采集。现有 final-only 回答、四条追问历史与 FIFO 回答不变。

## 发布范围

当前 Git 基线是 9 月版本，线上另有 11 月功能与数据库 revision 37。为保留线上功能，使用 `docker/Dockerfile.live-interview-hotfix` 在已核对的当前生产 API 镜像上仅覆盖五个实时面试文件，固定 Git 提交、父镜像 ID、补丁哈希和新镜像 ID。前端继续运行现有 11 月镜像；配置、数据卷、模型凭据、数据库和 Scheduler 状态保持原值。

补丁同时包含前轮已验证的后端低延迟参数（ASR 3.1、500ms 断句、非 thinking 的 1024 Token 回答、分段延迟事件）。新前端首字指标暂不随旧基线整站发布，避免回退其他页面。

发布工具仍执行正式隔离检查和标准部署门禁。精确补丁的构建入口为 `scripts/prepare_live_interview_hotfix.py`，它要求真实成功的检查指纹，从固定 Git 提交读取补丁，核对父镜像；部署使用项目 `wechat-release` 的标准 `deploy`。

## 验证记录

- 本地真实 TCP/Uvicorn/WebSocket：原串行调用链在慢 ASR 时复现 `1011 keepalive ping timeout`；修后短阻塞 0.65 秒可恢复、超时/满队列返回明确错误、事件 sender 失败可释放。
- 75 秒持续连接：689 音频帧、68 应用心跳，P95 2.242ms，0 断连；8 并发 3 秒：224 帧、16 心跳，P95 5.814ms；32 并发 3 秒：896 帧、64 心跳，P95 8.546ms。所有音频按顺序完整到达，结束后 writer 归零。Provider 和存储为内存替身，传输为真实本机 TCP。
- 正式隔离发布检查：Python `628 passed, 1 skipped`（长压测为按需用例，已显式单独执行）；Web `233 passed`；扩展 `44 passed`；生产构建通过。
- 之前偶发的 Worker 并发测试以 `threading.Event` 确定首次 8 槽位同时进入，移除依赖 Windows 调度的 20ms sleep。全部业务断言不变，整个文件重复 5 次通过。
- 检查证据：`tmp/production-releases/checks/skill_release_677dc31991304578a6cba8e6697ff548/`；持续压测：`tmp/regression-20261118-144536/transport-soak.log`。

生产切换和真实 Provider WSS 验收待后续记录。

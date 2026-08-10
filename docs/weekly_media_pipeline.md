# 周榜媒体生成工作流

## 目标与边界

本模块把每周 GitHub 热门项目的图文摘要扩展为可审核的短视频素材，服务于公众号草稿发布。目标是让“文案、五张技术教学插图、视频分镜、统一旁白、最终视频”在同一个 `content_id` 下可追溯、可重试。

本次实现不直接调用任何付费生成 API。当前默认配置仍保持 `video.submit_enabled: false`，因此本地可以验证任务编排和视频装配，但不会创建 Seedance 付费任务。

不在本次范围内：自动公开发布、视频内硬字幕烧录、云端对象存储的实际开通。公众号仍保留人工审核后进入草稿箱的边界。

## 运行调用链

```text
Application
  -> SearchTask
  -> SummaryTask
  -> ShortVideoPromptTask
  -> ImageTask
  -> VideoClipPlanTask
  -> AudioTask
  -> StorageTask
  -> SeedanceClipTask
  -> SeedanceClipStatusTask
  -> VideoAssemblyTask
  -> PreviewTask
  -> ArticleLayoutTask
  -> DeliverTask
  -> CatTask
```

周五 08:00 的内容生产 Job 负责生成文章、图片、分镜、旁白并提交 Seedance 分片任务。周五 09:00 的草稿 Job 先轮询分片状态；所有片段完成后再装配最终视频、刷新预览并推送公众号草稿箱。

`VideoStatusTask` 仅为历史单段视频资产保留兼容入口；新链路不再依赖它完成分片视频。

## 视频状态流转

```text
video_clip_plan: planned
  -> SeedanceClipTask
video_clip_task: submitted / processing
  -> SeedanceClipStatusTask
video_clip: created
  -> VideoAssemblyTask
video: created
  -> StorageTask / PreviewTask / DeliverTask
```

- 单个 Seedance 分片查询或下载失败时，只更新为 `processing` 并记录原因，下一次调度会继续重试。
- 明确的远端失败状态才会把该分镜与任务标记为 `failed`，供 CatTask 和人工审核定位。
- 已下载的片段会按 `clip_plan_id` 复用，重复运行不会再次下载或重复创建成片。
- 旧的本地幻灯片兜底视频不会阻止新生成的 Seedance 成片进入装配流程。

## 音频与画面一致性

Seedance 每段只生成教学风动态画面：提示词明确禁止旁白、口型与内嵌字幕。`AudioTask` 只生成一条完整中文旁白，`VideoAssemblyTask` 通过 ffmpeg 统一静音、规范化、拼接七段画面，再混入这一条旁白。

这样避免七段视频各自生成不同音色、语速或文案。当前 `VideoClipPlanTask` 会生成 7 段、每段 15 秒的计划，最终理论时长约 105 秒；这是此前采用“七段十五秒”方案的结果。配置中的 `video.duration_seconds: 60` 仍是旧版整体目标时长，不参与当前装配裁剪，后续应由产品决定统一为 60 秒或 105 秒后再收敛配置。

## 本地开发与上线配置

| 场景 | 推荐配置 | 行为 |
| --- | --- | --- |
| 本地开发 / 无付费调用 | `video.submit_enabled=false`、`storage.provider=local` | 生成分镜和旁白；音频可退回 Windows SAPI / edge-tts；不提交 Seedance。可用装配验证脚本验证 ffmpeg。 |
| 生产云端 | `video.submit_enabled=true`、公开对象存储 | 图片先上传到 R2/TOS 等公开可读地址，再提交 Seedance；使用豆包 TTS；完成后由定时任务下载、装配并进入公众号审核。 |

生产环境最小必需变量：

```text
VOLCENGINE_ARK_API_KEY=
DOUBAO_TTS_API_KEY=
# 可选；未填则使用 app.yaml 的默认音色
DOUBAO_TTS_VOICE_TYPE=

# Seedance 引用图需要公开 URL，R2 是当前已实现的对象存储 Provider
CLOUDFLARE_R2_ACCOUNT_ID=
CLOUDFLARE_R2_ACCESS_KEY_ID=
CLOUDFLARE_R2_SECRET_ACCESS_KEY=
CLOUDFLARE_R2_BUCKET=
CLOUDFLARE_R2_PUBLIC_BASE_URL=
```

本地 `storage.provider=local` 只有在设置 `STORAGE_LOCAL_PUBLIC_BASE_URL` 且该地址实际能被火山方舟访问时，才能作为 Seedance 的图片引用来源。生产 Compose 会由 Caddy 只读服务 `outputs/public`，因此应填写 `https://你的域名/media`，而不是 `127.0.0.1` 或裸域名。

## Kimi 的评估结论

没有把“Kimi K3 本地部署”加入运行链路：截至本次调研，Moonshot 官方模型列表明确提供的是 Kimi K2.5 / K2.6 API，未找到可作为生产依赖的 Kimi K3 本地部署官方资料。RTX 5070 的本地模型也不应成为未来个人网站上线时的硬依赖。

若后续需要提高分镜、标题或提示词质量，可把 Kimi K2.6 作为可选的云端“脚本审校 Provider”，而不是替代豆包 TTS 或 Seedance 视频生成。官方文档：

- [Kimi 模型列表](https://platform.kimi.ai/docs/models)
- [Kimi K2.6 快速开始](https://platform.kimi.ai/docs/guide/kimi-k2-6-quickstart)
- [Kimi API 概览](https://platform.kimi.ai/docs/api/overview)

## 验证结果

已在本地完成以下无外部 API 验证：

```powershell
.\.venv\Scripts\python.exe scripts\verify_video_assembly.py
```

该脚本生成 3 个测试片段和一条测试音频，验证 ffmpeg 可以规范化、拼接并混音，输出 MP4 文件。配置加载与新任务模块的 Python 编译检查也已通过。

豆包短文本旁白的分段与拼接可额外通过以下命令验证；它只生成本地测试音频，不会读取或消耗豆包凭证：

```powershell
.\.venv\Scripts\python.exe scripts\verify_doubao_tts_chunking.py
```

豆包语音改为新版 API Key 后，还可先执行下列离线协议验证。它会模拟 V3 NDJSON
响应，不会发送真实旁白或消耗语音额度：

```powershell
.\.venv\Scripts\python.exe scripts\verify_doubao_tts_v3_contract.py
```

上线前仍需在已开通模型、TTS、对象存储的账户中，手动开启 `video.submit_enabled` 后完成一次真实 Seedance 提交、轮询和下载验收；这一步会产生对应服务商费用。

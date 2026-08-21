# 周榜媒体生成工作流

## 目标与边界

本模块把每周 GitHub 热门项目的图文摘要扩展为可审核的短视频素材，服务于公众号草稿发布。目标是让“文案、五张技术教学插图、视频分镜、统一旁白、最终视频”在同一个 `content_id` 下可追溯、可重试。

本次实现不直接调用任何付费生成 API。当前默认配置仍保持 `video.submit_enabled: false`，因此本地可以验证任务编排和视频装配，但不会创建 Seedance 付费任务。

不在本次范围内：自动公开发布、视频内硬字幕烧录、云端对象存储的实际开通。公众号仍保留人工审核后进入草稿箱的边界。

## 内容职责分层

为避免一次 DeepSeek 调用同时生成长文章、五张图 Prompt、七段视频脚本和旁白而触发输出截断，当前链路按产物所有权拆分：

- `SummaryTask`：只生成标题、摘要与深度文章，并从已校验文章中编译五份共享 `ContentBrief`。
- `ShortVideoPromptTask`：基于 `ContentBrief` 生成渐进式讲稿、统一旁白与七段视频分镜，并回写 `video_script`、`voiceover_text`。
- `ImageTask`：基于同一 `ContentBrief` 生成五张图片各自的 Ark 最终 Prompt，再调用 Seedream。
- `VideoClipPlanTask`：把分镜翻译为 Seedance 分片计划，不重新总结项目事实。
- `AudioTask`、`SeedanceClipTask`：只消费已生成的旁白与分片 Prompt，不承担内容创作。

因此文章、图片、视频均引用同一份项目事实合同，但每个 Task 只生成自己负责的产物。`content_id` 继续作为全部正文、Prompt、分镜与媒体素材的隔离键。

## 运行调用链

```text
Application
  -> SearchTask
  -> SummaryTask（文章 + ContentBrief）
  -> ShortVideoPromptTask（讲稿 + 旁白 + 分镜）
  -> ImageTask（图片最终 Prompt + 原始图片）
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

## 工作台生命周期展示边界

- **设计目标**：五阶段主任务生命周期只在 `/review/pipeline` 详情页展示，避免工作台及素材、历史等页面重复出现同一组状态。
- **技术取舍**：删除 `App.vue` 审核路由壳层中的顶部资源状态栏，在任务流程页以“内容 → 图片 → 音频 → 视频 → 审核”的横向流程集中展示；原有底层 Task 列表保留为“执行任务明细”，不新增接口。
- **调用链**：预览与素材 API → `content` / `mediaAssets` / `approval` → `lifecycleStages` → `/review/pipeline` 主任务生命周期；任务运行记录 API → `tasks` → `taskCards` → 工作台任务流程预览 / 详情页执行任务明细。

## 提示词预览视口

- **设计目标**：`/review/prompts` 在 PC 端固定在工作台当前视口内，避免生图与视频提示词将整页无限向下撑开。
- **交互取舍**：生图提示词和视频提示词保持左右两列，两列内容各自独立纵向滚动并保留可见滚动槽；手机端回退为单列页面自然滚动。
- **信息边界**：页面仅保留“生图提示词”“视频提示词”两个中文标题，删除只读状态说明和英文装饰眉题，不改变提示词数据及接口调用链。

## 工作台首屏收敛

- **设计目标**：`/review` 在 PC 端固定在当前工作区高度内，由工作台内容容器独立滚动；健康概览、管理员运行区和模块入口采用紧凑间距，避免大卡片把常用入口推到多屏之后。
- **信息取舍**：删除 CatTask、Manual Run、Article、Pipeline、Assets、Archive 等英文装饰眉题，以及“健康”“管理员操作”“ready”“只读归档”等重复状态胶囊；保留真实计数、错误状态和运行中状态。
- **模块布局**：宽屏五个入口等高并排，较窄 PC 自动切换三列或两列；模块预览限制高度，手动运行记录在卡片内滚动。手机端回退为单列页面自然滚动。
- **依赖与边界**：不修改后端任务、状态存储、审核动作和媒体资源数据，只收敛前端展示位置；其他业务页面不再展示主任务生命周期。
- **验证方式**：前端生产构建通过，并确认五阶段生命周期只在 `/review/pipeline` 模板分支渲染。

## 媒体素材展示边界

- **设计目标**：`/review/assets` 只展示用户可直接查看或播放的图片、音频、成片和视频片段，避免任务句柄与 JSON 报告占用大尺寸预览卡片。
- **数据取舍**：`/api/media-assets` 的 `items` 与计数、工作台媒体概览以及执行历史素材数只包含 `image`、`audio`、`video`、`video_clip`；`video_clip_task`、`video_quality_report`、`video_narration_timeline` 继续保存在 `media_assets` 中供任务追溯，但不进入素材库。
- **前端兜底**：素材页对接口结果再次按上述四种类型过滤，兼容旧服务或缓存返回的流程记录；标题统计使用“媒体文件”，不再把数据库记录数描述为实际文件数。
- **图片查看交互**：素材库图片缩略图和正文预览中的排版图片均使用原生链接包裹，点击或通过键盘回车后在新标签页打开原图，并提供“点击查看原图”提示；不修改音频、视频或后端接口。
- **验证方式**：单元测试覆盖素材类型边界和正文图片链接包装行为，前端生产构建验证页面模板与样式；PC 端浏览器验收确认素材库 6 张图片和正文 5 张排版图片均生成指向原图的 `target="_blank"` 原生链接，并确认原图接口返回图片内容。

## 音频与画面一致性

Seedance 每段只生成教学风动态画面：提示词明确禁止旁白、口型与内嵌字幕。`AudioTask` 只生成一条完整中文旁白，`VideoAssemblyTask` 通过 ffmpeg 统一静音、规范化、拼接七段画面，再混入这一条旁白。

这样避免七段视频各自生成不同音色、语速或文案。当前 `VideoClipPlanTask` 会生成 7 段、每段 15 秒的计划，最终理论时长约 105 秒；这是此前采用“七段十五秒”方案的结果。配置中的 `video.duration_seconds: 60` 仍是旧版整体目标时长，不参与当前装配裁剪，后续应由产品决定统一为 60 秒或 105 秒后再收敛配置。

当前为了控制成本，本地和生产均关闭音视频生成：`VIDEO_SUBMIT_ENABLED=false`、`AUDIO_ENABLED=false`。一次性任务与 Scheduler 不再进入视频蓝图、分镜、Seedance、视觉质检、旁白、TTS 和装配阶段；`AudioTask` 与 `SegmentedAudioTask` 在真实调用入口还会再次检查音频开关。历史音视频仅保留用于只读预览，不再上传到对象存储或微信公众号。

## 本地开发与上线配置

| 场景 | 推荐配置 | 行为 |
| --- | --- | --- |
| 本地开发 | `video.submit_enabled=false`、`audio.enabled=false` | 只生成文章、图片、排版和图文草稿，不生成或上传音视频。 |
| 生产云端 | `VIDEO_SUBMIT_ENABLED=false`、`AUDIO_ENABLED=false` | API 与 Scheduler 均跳过完整音视频链路，不产生 Seedance、视频质检或 TTS 费用。 |

生产环境最小必需变量：

```text
VOLCENGINE_ARK_API_KEY=
DOUBAO_TTS_API_KEY=
# 可选；未填则使用 app.yaml 的默认音色
DOUBAO_TTS_VOICE_TYPE=

# 仅在 video.reference_images_enabled=true 时需要；R2 是当前已实现的对象存储 Provider
CLOUDFLARE_R2_ACCOUNT_ID=
CLOUDFLARE_R2_ACCESS_KEY_ID=
CLOUDFLARE_R2_SECRET_ACCESS_KEY=
CLOUDFLARE_R2_BUCKET=
CLOUDFLARE_R2_PUBLIC_BASE_URL=
```

本地 `storage.provider=local` 只有在设置 `STORAGE_LOCAL_PUBLIC_BASE_URL` 且该地址实际能被火山方舟访问时，才能作为 Seedance 的图片引用来源。当前 `generation_type=text-to-video` 且 `reference_images_enabled=false`，因此它不是视频提交前置条件；生产 Compose 仍会由 Caddy 只读服务 `outputs/public`，供媒体预览与未来参考图模式使用。

公众号草稿的正文图片数量不再固定为 5。`ArticleLayoutTask` 会把本次有效项目数写入 `layout_stats.expected_image_count`，`DeliverTask` 的前置检查和真实上传共同读取这个值；旧排版记录则由 `embedded_image_count + missing_image_count` 推导。因此 Top1 内容只要求 1 张项目图，Top5 内容仍要求 5 张，且报错会显示本次真实目标数。

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

部署后需确认 `.env.production` 保持 `VIDEO_SUBMIT_ENABLED=false`、`AUDIO_ENABLED=false`，重建 `career-api` 与 `pipeline-scheduler`，并通过 `scripts/check_media_production_readiness.py` 验证关闭状态。后续如需恢复音视频，必须由管理员显式调整开关并重新进行付费链路验收。

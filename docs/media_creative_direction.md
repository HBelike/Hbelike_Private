# 周榜内容创作质量升级

## 目标与边界

本次升级把“项目摘要 → 五张插图 → 七段动态教学视频”的过程改为一份可追溯的创作合同，避免项目间共用泛化提示词、图片同质化和视频片段在提交前被重新压缩。

- 每个项目保留独立的 `visual_brief`、`video_brief`、原始摘要意图和最终 Ark 图片 Prompt。
- 图片始终由火山方舟直接输出原始图像；禁止本地叠字、遮罩、拼贴、覆盖或把其他图片合成到模型输出上。
- 成片固定为开场、五个项目段、结尾共七段。每段是逐步讲解的动态信息图，而不是五张静态图硬切。
- Seedance 负责动态视觉片段；TTS、中文字幕、转场与合成由 HyperFrames 蓝图在人工审核通过后再确定性装配。
- 当前不执行付费生图、视频提交、视频渲染或公众号发布；`video.submit_enabled` 保持为 `false`。

## 技术取舍

| 组件 | 作用 | 采用原因 |
| --- | --- | --- |
| ByteDance DeerFlow 的结构化视觉思想 | 将摘要约束为结构、关系、阅读顺序和色板 | 避免模型只收到一句抽象描述后生成同质化画面。 |
| 火山方舟 Seedream | 生成文章的五张原始插图 | 保留现有已配置的服务与审核流程；按 16:9、32 对齐配置为 `2048x1152`，首次受控真实调用仍需验收提供方返回的实际尺寸。 |
| Seedance 2.0 Prompt 规则 | 为每段生成分时动作、运镜、入场和离场状态 | 使七段片段可按连续叙事衔接，而不是依赖笼统总 Prompt。 |
| HyperFrames | 保留后期装配蓝图 | HTML 时间轴可确定性地处理配音、字幕、转场和验收；不替代 Seedance 的原始动态生成。 |

本地已存在 Seedance 2.0 和 HyperFrames Skill，因此本次不重复下载未经审查的同名包。调研时排除了 GPL/AGPL 的本地 ComfyUI、A1111 路线，避免把生产视频能力绑到服务器 GPU 与强 copyleft 依赖上。

## 数据合同

`SummaryTask` 的模型调用只生成 `title`、`digest` 和深度 `article_markdown`。文章通过完整性校验后，任务再从文章中确定性编译每个热门仓库的共享 `ContentBrief`：

```text
repository_full_name / rank
summary_text / project_summary_text
project_analysis_markdown
prompt_stage = content_brief_v1
visual_brief
  -> diagram_type / teaching_goal / visual_thesis
  -> nodes / relationships / reading_order / chinese_labels
  -> palette_key / negative_constraints
video_brief
  -> narrative_claim / mechanism / motion_metaphor
  -> camera / transition / audio_directive
```

为兼容现有数据库，`ContentBrief` 暂时仍保存于 `generated_contents.image_prompts_json`，但它不是可直接提交给图片模型的最终 Prompt。它只承载文章、图片和视频共同依赖的事实、机制与视觉意图，避免三个任务各自重新理解项目后产生内容漂移。

`ImageTask` 读取 `ContentBrief`，将其编译成 `ark_final_v2` Prompt，并把最终 Prompt 与同一 `content_id` 的媒体素材 metadata 一起保存。预览页仅回填该 `content_id` 下的最终 Prompt；GitHub 或本地兜底素材不会伪装成 Ark 最终提示词。

`ShortVideoPromptTask` 读取同一 `ContentBrief`，独立生成渐进式讲稿、统一旁白、七段 `scene_contract` 和每段可直接交给 Seedance 的 `seedance_scene_prompt`，再把讲稿与旁白回写当前内容记录。`VideoClipPlanTask` 必须原样保留这份详细 Prompt，只追加连续性与全局负面约束，不能再次压缩成旧的通用模板。

## 调用链

```text
main.py
  -> SummaryTask
     -> DeepSeek（仅标题、摘要、深度文章）
     -> MediaCreativeBriefService.normalize_visual_brief()
     -> MediaCreativeBriefService.normalize_video_brief()
     -> GeneratedContentRepository
  -> ShortVideoPromptTask
     -> DeepSeek（渐进式讲稿与七段分镜）
     -> GeneratedContentRepository.update_media_plan()
     -> MediaCreativeBriefService.build_hyperframes_blueprint()
     -> VideoStoryboardRepository
  -> ImageTask
     -> ContentBrief
     -> ImagePromptDesignService.build_project_architecture_prompt()
     -> Ark Seedream（仅在显式运行图片任务时）
     -> MediaAssetRepository
  -> VideoClipPlanTask
     -> VideoClipPlanRepository
  -> 人工审核通过后
     -> Seedance 片段生成 / TTS / HyperFrames 装配 / 微信草稿箱
```

## 质量门槛

1. 五张图的结构类型和调色板必须可区分；不允许统一回退为蓝绿霓虹、蓝白三栏或黑板背景。
2. 每张图最多六个核心节点、七条关系和八个中文短标签；禁用仓库 URL、长英文、代码截图、真实产品 Logo、乱码与伪文字。
3. 每段视频明确时长、进入状态、三拍动作、离开状态、运镜和转场；文本不要求在视频里逐字上屏。
4. `content_id` 是素材、最终 Prompt、文章和视频蓝图的隔离键；不可混用其他任务的媒体资产。
5. HyperFrames 只在审核后进行 `lint → check → snapshot → 人工确认 → render`，确保付费视频和发布前可查看。

## 离线验证

以下验证不读取密钥、不请求 DeepSeek、Ark、Seedance、HyperFrames 或微信：

```powershell
.\.venv\Scripts\python.exe -m compileall src scripts
.\.venv\Scripts\python.exe scripts\verify_media_creative_contract.py
Set-Location web-ui
npm run build
```

验证脚本会检查五个项目的最终图片 Prompt 不相同、无 URL、包含原始图像约束，并检查七段分镜、详细 Seedance Prompt 与待审核 HyperFrames 蓝图完整保留。

## 后续人工验收

在首次真实生图前，需要在审核台确认一份五图预览，重点看中文短标签的可读性和 16:9 横版安全区。确认后再执行一次受控的 Ark 调用，确认服务端接受 `2048x1152` 并返回预期横版尺寸；随后再启用 Seedance 片段任务，而不是直接打开全链路定时发布。

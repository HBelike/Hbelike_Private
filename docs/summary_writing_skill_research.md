# GitHub 项目文章 Skill 与 SummaryTask 接入记录

## 目标

在不重写周榜流水线、不增加模型调用次数的前提下，提高工作台动态 Top N 文章的技术含量与自然度。文章需要像有实战经验的技术讲师在拆项目：既说明项目做什么，也解释架构、模块、执行流程、关键实现、运行方式、取舍和适用边界。

本次只处理公开 GitHub 仓库。私有仓库、需要 Token 才能读取的仓库、非 GitHub 地址和无法确认公开性的地址不在处理范围内。

## 技术取舍

- 保留现有 `SummaryTask` 和单次 DeepSeek ChatCompletions 调用，不改成五轮循环，也不引入 Agent Loop。项目数量继续读取工作台配置形成的周榜记录数，代码不假定 Top 5。
- 新增一个自包含的 `github-project-blog` Skill。它吸收 `blog-post-writer` 的技术文章组织方式和 `humanizer` 的去 AI 化规则，但线上运行不再依赖这两个 Skill，也不使用已经取消的 `technical-review`。
- 工作台只把 Skill 正文挂载到同一次模型请求的 `system` message。JSON 字段、固定章节、事实数字和项目顺序仍由 `SummaryTask` 合同控制，Skill 不能覆盖这些约束。
- 取消标题、摘要、全文、单个项目章节和六个标签解释的全部输出长度限制。生成结果不会再因为过短或过长失败，篇幅由仓库证据和讲解需要自然决定；文章质量由后续人工审核把关。
- `DeepSeekProvider` 对 `deepseek-v4-*` 的结构化任务显式关闭 thinking，避免内部推理与最终 JSON 共享输出预算而截断 `article_markdown`；若服务仍以 `finish_reason=length` 结束，会报告明确的 Token 截断错误，而不是暴露成普通 JSON 语法错误。
- 不扩大当前事实采集范围。工作台继续使用周榜元数据与 README 摘录；没有提供的源码路径、架构细节、运行命令必须写成未知，不能由模型补全。

## Skill 能力

`github-project-blog` 覆盖以下十三类分析：项目定义、问题、关注价值、技术栈、整体架构、核心模块、执行流程、3 到 5 个可学习实现、关键源码、运行方式、优点、不足和适合人群。

工作台模式把这些问题映射到现有六个项目标签：

- `本周判断`：项目定义、关注价值和榜单数据；
- `问题与代价`：旧做法的摩擦、成本和失败模式；
- `机制拆解`：技术栈、架构、模块、流程、可学习实现和源码线索；
- `落到工作流`：运行方式、接入位置、适合人群和下一步；
- `使用边界`：不足、限制、证据缺口和误用场景；
- `工程启发`：可迁移到其他工程的设计判断。

独立 Agent 只收到公开 GitHub URL 时，Skill 会要求先只读查看 README、目录、依赖与构建清单、真实入口、核心源码、测试和文档，再生成 Markdown 综述。工作台已经提供 `source_evidence` 时不重复联网或克隆仓库。

## 调用链

```text
工作台 Top N 配置
  → SearchTask 生成 N 条 weekly_rankings
  → SummaryTask 获取 N 条公开仓库证据
  → SkillLibraryService 读取 github-project-blog
  → ArticleSkillPromptLoader 去除 frontmatter 并校验正文
  → Skill 正文 + 动态 Top N 数据进入同一次 DeepSeek 请求
  → 原有 JSON 解析、质量合同、ContentBrief 与后续媒体链路
```

项目本地 Skill 位于 `.agents/skills/github-project-blog`，生产镜像使用 `deploy/skill-seeds/github-project-blog`。导出清单由现有 portable skill 脚本维护。

## 失败与边界

- Skill 缺失、正文为空或 Skill 指令文件自身超过 32,000 个字符时，`SummaryTask` 直接失败，不静默退回旧提示词，避免线上文章质量在不知情时降级。这里的 32,000 字符只保护挂载的 Skill 指令文件，不限制模型生成的文章长度。
- 模型首次输出未通过 JSON 或质量合同，仍沿用原有的一次质量修复重试；这不是按项目循环生成。
- 质量合同只保留非长度规则：结果非空、JSON 字段正确、固定章节和项目顺序完整、六个标签存在且顺序正确、stars / 本周增长数字准确、正文不包含 URL，并继续遵守证据边界。
- 当前工作台证据主要是最多 3,200 字符的 README 摘录，因此可以提高分析框架和表达质量，但不能保证每个项目都有真实源码级结论。后续若需要更深的源码分析，应单独扩展证据采集层，而不是让写作 Skill 猜测。
- `blog-post-writer` 与 `humanizer` 只作为本 Skill 的设计来源。部署时只需要 `github-project-blog`，避免三个 Skill 的发现顺序、版本和运行时可用性影响生成结果。

## 验证结果

- Skill 结构通过 `skill-creator` 的 `quick_validate.py`。
- 生产 Skill 导出后通过 `verify_skill_portability.py`。
- `ArticleSkillPromptLoader` 覆盖正常加载、名称大小写、缺失、空正文和超长拒绝。
- `verify_summary_depth_contract.py` 同时验证 Top 5 与 Top 3，确认项目数动态、Skill 已挂载、极短标签和超长项目正文均可通过、缺少分析标签仍会失败。
- `DeepSeekProvider` 定向测试确认 V4 请求携带 `thinking={"type":"disabled"}`，并能识别 `finish_reason=length`；真实本地运行中，六项目文章首次请求以 `finish_reason=stop` 完成并进入后续媒体链路。
- 相关 Python 文件通过编译检查，现有 SummaryTask JSON 字段和后续 ContentBrief / 短视频证据传递保持不变。

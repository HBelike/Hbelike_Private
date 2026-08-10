# ImageTask 生图 Prompt 升级记录

本文档记录本项目使用 `$find-skills` 查询生图类 skill 后，沉淀到 `ImageTask` 和 `ShortVideoPromptTask` 的规则。

## 已查询并安装的 skill

1. `skills-collective/skills@ai-image-generation`
   - 安装量约 119K。
   - 已安装到 `~/.agents/skills/ai-image-generation`。
   - 被采用的规则：
     - prompt 不要堆 tag，要像给设计师写 brief；
     - Subject first，scene second，modifiers last；
     - Seedream 更适合写实和画面感，不适合复杂图内文字；
     - prompt 过长会稀释重点。

2. `inference-sh/skills@og-image-design`
   - 安装量约 517。
   - 已安装到 `~/.agents/skills/og-image-design`。
   - 被采用的规则：
     - 固定模板比临场发挥更稳；
     - 暗色背景、强对比、清晰安全区更适合社交流和公众号；
     - 少文字、少元素、固定版式能降低混乱概率。

3. `cdeistopened/skill-stack@image-prompt-generator`
   - 安装量约 243。
   - 本次只读取方法，没有安装为核心依赖。
   - 被采用的规则：
     - 一张图最多 2 到 3 个核心元素；
     - 避免灯泡、齿轮、握手、拼图等陈词滥调；
     - 不要让图片模型承担文字排版任务；
     - 先描述概念，再描述构图、颜色、质感和禁忌项。

## 本项目的新生图策略

此前的问题是：prompt 要求模型画“技术架构图”，但又允许仓库名、中文标签、代码和复杂模块同时出现。Seedream 很容易把这些理解成图内文字或随机 UI，导致图片混乱、乱码、节点过密。

现在改为两层策略：

1. `SummaryTask` 和 `ShortVideoPromptTask` 只负责输出业务语义：
   - 这个项目要表达什么机制；
   - 画面要突出输入、核心模块、输出还是使用场景。

2. `ImageTask` 在真正调用 Seedream 前，用 `ImagePromptDesignService` 统一包装为视觉 brief：
   - 主体：单项目技术架构信息图；
   - 构图：中心核心模块、左侧输入、右侧输出、底部适用场景；
   - 限制：最多 6 个节点、2 条主箭头；
   - 禁止：任何可读文字、中文、英文、代码、仓库名、logo、水印；
   - 风格：深色科技背景、蓝绿色高亮、PPT 教学课件风；
   - 安全区：中心 80% 留白，适合公众号裁切。

## 为什么不让模型直接生成带文字架构图

当前使用的 Seedream 更擅长画面风格，不擅长稳定渲染精确中文或复杂技术标签。让模型直接画标签，会出现乱码、错词、重复文字、布局拥挤。更稳的做法是：

- 图片模型负责无文字的结构图、节点关系和视觉质感；
- 公众号排版层负责标题、项目名、说明文字；
- 后续如果需要精确图中文字，再考虑改用更擅长文字的模型，或直接用 HTML/SVG/Canvas 程序化绘图。

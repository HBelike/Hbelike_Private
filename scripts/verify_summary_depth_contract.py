"""离线验证 SummaryTask 的长文合同、共享 ContentBrief 与视频证据传递。

本脚本不读取 API Key、不调用 GitHub 或 DeepSeek。它只验证：
1. 动态 Top N 的每个项目都必须包含固定分析标签和真实 stars / 本周增长数字；
2. 项目章节不再要求至少 500 个中文字符，技术深度改由结构与事实合同保证；
3. SummaryTask 只请求文章字段，并在 system message 挂载项目写作 Skill；
4. 短视频任务会读取每个项目的证据卡，并生成同源旁白。
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import logging
import sqlite3
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.providers.github_client import GitHubRepositoryEvidence
from src.repositories.generated_content_repository import (
    GeneratedContentForStoryboard,
    GeneratedContentRepository,
)
from src.repositories.weekly_ranking_repository import WeeklyRankingRecord
from src.tasks.short_video_prompt_task import ShortVideoPromptTask
from src.tasks.summary_task import SummaryTask


def build_rankings(count: int = 5) -> list[WeeklyRankingRecord]:
    """按动态数量构造与真实周榜字段一致的离线样本。"""

    return [
        WeeklyRankingRecord(
            repository_id=index,
            rank=index,
            full_name=f"example-org/project-{index}",
            html_url=f"https://github.com/example-org/project-{index}",
            description=f"用于验证第 {index} 个项目的工程说明。",
            language="Python",
            current_stars=10000 + index,
            star_growth=800 + index,
            growth_rate=0.12,
            score=0.9,
            reason="离线合同验证样本",
        )
        for index in range(1, count + 1)
    ]


def build_evidence(rankings: list[WeeklyRankingRecord]) -> dict[str, GitHubRepositoryEvidence]:
    """构造 README 已可用的公开证据样本。"""

    return {
        item.full_name: GitHubRepositoryEvidence(
            full_name=item.full_name,
            description=item.description,
            topics=("agent", "workflow"),
            default_branch="main",
            license_name="MIT",
            readme_excerpt=(
                "该项目把输入整理、任务编排、执行反馈和人工校验拆为可组合模块，"
                "README 明确说明了主流程与边界。"
            ),
            evidence_status="readme",
        )
        for item in rankings
    }


def build_project_section(item: WeeklyRankingRecord, index: int) -> str:
    """构造结构与事实完整、但不足 500 个中文字符的项目正文。"""

    labels_and_text = [
        (
            "本周判断",
            f"{item.full_name} 当前 stars 为 {item.current_stars}，本周新增 {item.star_growth}。"
            "这个增速说明开发者正在关注把零散能力放进可重复执行的工程环节，而不是继续堆叠聊天入口。",
        ),
        (
            "问题与代价",
            "很多团队的问题不是缺少模型，而是输入、状态、人工确认和结果回写散落在不同工具里。"
            "如果每一步都靠人工复制，成本会在重试、审计和协作交接时迅速显现。",
        ),
        (
            "机制拆解",
            "README 把流程拆成明确的职责边界：前一层收集并规范输入，中间层依据规则组织任务，"
            "后一层把结果和反馈重新放回可追踪的上下文。这样的分层让错误不必由整条链路一起承担。",
        ),
        (
            "落到工作流",
            "实际接入时，应先挑选一个输入稳定、结果可复核的小流程，例如资料归类、候选项筛选或任务分发。"
            "先定义输入格式和完成标准，再把该项目放在最容易观察收益的位置，而不是替换全部系统。",
        ),
        (
            "使用边界",
            "它不适合在需求还没定义、数据来源持续变化且没有人工兜底的场景里直接自动决策。"
            "当 README 没有展开权限、成本或失败恢复机制时，生产接入前仍应自行补足这些约束。",
        ),
        (
            "工程启发",
            "第一个可复用的价值不是功能数量，而是把一次执行留下的状态、原因和下一步动作变成团队可以检查的对象。"
            "因此评估同类项目时，应优先看边界是否清楚、反馈是否能回流，以及最小接入点能否单独验证。",
        ),
    ]
    body = "\n\n".join(f"**{label}** {text}" for label, text in labels_and_text)
    assert SummaryTask._count_chinese_characters(body) < 500
    return f"#### 项目 {index}：{item.full_name}\n\n{body}"


def build_article(rankings: list[WeeklyRankingRecord]) -> str:
    """按输入项目数量拼出满足固定标题顺序的离线文章。"""

    mainline = (
        "本周的共同信号不是某个模型突然变强，而是越来越多仓库把输入、流程、反馈和人工判断拆成可观察的工程对象。"
        "这类变化对开发者更有价值，因为工具是否能进入团队，取决于它能否被接入、复核、回滚和持续维护。"
    )
    conclusion = (
        "把这些项目放在一起看，最值得带走的不是立刻替换现有工具，而是先确认团队缺失的是哪一段可观察的流程。"
        "只要输入、责任边界和失败处理还不清楚，再强的模型也只会把不确定性搬到更难排查的地方。"
    )
    return "\n\n".join(
        [
            "### 本周主线",
            mainline,
            f"### Top {len(rankings)} 项目拆解",
            *[build_project_section(item, index) for index, item in enumerate(rankings, start=1)],
            "### 工程启发",
            conclusion,
        ]
    )


def main() -> None:
    """执行合同验证并打印精简结果。"""

    rankings = build_rankings()
    evidence = build_evidence(rankings)
    task = object.__new__(SummaryTask)
    task.logger = logging.getLogger("verify_summary_depth_contract")
    article = build_article(rankings)

    project_sections = task._validate_article_depth(
        article_markdown=article,
        rankings=rankings,
        ranking_evidence=evidence,
    )
    assert len(project_sections) == len(rankings)
    assert all(
        task._count_chinese_characters(section) < 500
        for section in project_sections.values()
    )

    # 工作台可配置任意 Top N；用非默认数量验证标题、章节和校验器都不依赖 5。
    compact_rankings = build_rankings(count=3)
    compact_evidence = build_evidence(compact_rankings)
    compact_sections = task._validate_article_depth(
        article_markdown=build_article(compact_rankings),
        rankings=compact_rankings,
        ranking_evidence=compact_evidence,
    )
    assert len(compact_sections) == len(compact_rankings)

    # 周榜真实数值允许使用中文技术文章常见的千分位展示，不能因 143,902
    # 与数据库整数 143902 的格式差异让 SummaryTask 误失败；但近似单位
    # 仍不视为精确事实，避免质量合同被放宽。
    assert task._contains_exact_ranking_number("当前 stars 为 143,902", 143902)
    assert task._contains_exact_ranking_number("本周新增 1，281", 1281)
    assert task._contains_exact_ranking_number("本周新增 1 281", 1281)
    assert not task._contains_exact_ranking_number("当前 stars 约为 14.4 万", 143902)
    assert not task._contains_exact_ranking_number("本周新增约 1.3k", 1281)

    first_item = rankings[0]
    # 章节可以短于 500 字，但不能丢失固定分析结构。
    incomplete_article = article.replace(
        project_sections[first_item.full_name],
        (
            f"**本周判断** {first_item.full_name} 当前 stars 为 {first_item.current_stars}，"
            f"本周新增 {first_item.star_growth}，这一段故意缺少其余标签以验证结构合同。"
        ),
    )
    try:
        task._validate_article_depth(
            article_markdown=incomplete_article,
            rankings=rankings,
            ranking_evidence=evidence,
        )
    except ValueError as exc:
        assert "项目 1" in str(exc)
        assert "缺少技术拆解标签" in str(exc)
    else:
        raise AssertionError("缺少固定标签的项目正文必须被质量合同拒绝")

    article_skill_instructions = "# GitHub 项目文章\n\n只根据输入证据写作，并解释技术机制。"
    messages = task._build_messages(
        rankings=rankings,
        week_end="2026-08-14",
        highest_star_repository=rankings[0],
        ranking_evidence=evidence,
        article_skill_instructions=article_skill_instructions,
    )
    assert article_skill_instructions in messages[0].content
    main_prompt = messages[-1].content
    assert "不少于 500 个中文字符" not in main_prompt
    assert "全文中文字符数不得超过" in main_prompt
    assert "source_evidence" in main_prompt
    assert "article_markdown 1900 字以内" not in main_prompt
    assert "JSON 只能包含 title、digest、article_markdown 三个字段" in main_prompt
    assert "video_script 必须" not in main_prompt
    assert "image_prompts 必须" not in main_prompt

    retry_messages = task._build_retry_messages(
        rankings=rankings,
        week_end="2026-08-14",
        highest_star_repository=rankings[0],
        ranking_evidence=evidence,
        article_skill_instructions=article_skill_instructions,
    )
    assert article_skill_instructions in retry_messages[0].content
    retry_prompt = retry_messages[-1].content
    assert "只修正合同缺陷" in retry_prompt
    assert "不设置最低字数" in retry_prompt
    assert "1600字以内" not in retry_prompt
    assert "字段必须只有以下三个" in retry_prompt
    assert "voiceover_text:" not in retry_prompt

    normalized = task._normalize_model_output(
        parsed={
            "title": "离线长文合同验证",
            "digest": "验证文章阶段只产出事实正文，并确定性构造下游共享内容简报。",
            "article_markdown": article,
        },
        rankings=rankings,
        highest_star_repository=rankings[0],
        ranking_evidence=evidence,
    )
    assert set(normalized) == {"title", "digest", "article_markdown", "image_prompts"}
    assert len(normalized["image_prompts"]) == len(rankings)
    assert all(item["prompt_stage"] == "content_brief_v1" for item in normalized["image_prompts"])
    assert all(item["project_analysis_markdown"] for item in normalized["image_prompts"])

    content = GeneratedContentForStoryboard(
        id=1,
        week_end="2026-08-14",
        title="离线长文合同验证",
        digest="验证后续视频可读取每个项目的独立证据卡。",
        article_markdown=article,
        video_script="离线验证脚本。",
        voiceover_text="离线验证脚本。",
        image_prompts=normalized["image_prompts"],
    )
    video_task = object.__new__(ShortVideoPromptTask)
    video_prompt = video_task._build_script_messages(content=content, video_instruction="")[-1].content
    assert "project_evidence_card" in video_prompt
    assert "example-org/project-5" in video_prompt
    assert "文章正文：" not in video_prompt

    voiceover = video_task._build_voiceover_text(
        {
            "scenes": [
                {"narration": "先看本周主线。"},
                {"narration": "再解释第一个项目。"},
                {"narration": "最后收束工程启发。"},
            ],
            "progressive_script": "不应使用这条兜底文本。",
        }
    )
    assert voiceover == "先看本周主线。\n再解释第一个项目。\n最后收束工程启发。"

    # SummaryTask 创建记录时媒体字段为空；只有 ShortVideoPromptTask 回写后，
    # 后续 AudioTask / VideoTask 才能读取该内容，避免误用旧脚本。
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE generated_contents (
            id INTEGER PRIMARY KEY,
            week_end TEXT NOT NULL,
            title TEXT NOT NULL,
            video_script TEXT,
            voiceover_text TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO generated_contents (id, week_end, title, video_script, voiceover_text)
        VALUES (1, '2026-08-14', '离线媒体计划验证', '', '')
        """
    )

    class InMemoryDatabaseManager:
        @contextmanager
        def connection(self):
            yield conn
            conn.commit()

    content_repository = GeneratedContentRepository(InMemoryDatabaseManager())
    assert content_repository.latest_for_video_generation() is None
    content_repository.update_media_plan(
        1,
        video_script="渐进式视频讲稿。",
        voiceover_text="统一旁白文本。",
    )
    media_plan = content_repository.latest_for_video_generation()
    assert media_plan is not None
    assert media_plan.video_script == "渐进式视频讲稿。"
    assert media_plan.voiceover_text == "统一旁白文本。"
    conn.close()

    print(
        "摘要深度合同验证通过："
        f"projects={len(project_sections)} "
        f"article_chinese_chars={task._count_chinese_characters(article)}"
    )


if __name__ == "__main__":
    main()

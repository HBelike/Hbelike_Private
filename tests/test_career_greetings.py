from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from src.career_assistant.context_profiles import CandidateProfileRecord
from src.career_assistant.contracts import ModelCapability, ModelSelectionMode
from src.career_assistant.greetings import (
    CareerGreetingService,
    GreetingCandidateNotFoundError,
    GreetingGenerationError,
    GreetingJobInput,
    GreetingJobValidationError,
    GreetingModelUnavailableError,
)
from src.career_assistant.model_gateway import (
    ModelProfileAvailability,
    ModelReadiness,
    ModelResolution,
    ModelResolutionReason,
)
from src.career_assistant.persistence.model_profile_repository import (
    ModelCostTier,
    ModelProfileRecord,
)


ORGANIZATION_ID = uuid4()
ACTOR_ID = uuid4()
CANDIDATE_ID = uuid4()


def profile(
    provider_key: str,
    model_id: str,
    *,
    priority: int = 100,
    readiness: ModelReadiness = ModelReadiness.READY,
) -> tuple[ModelProfileRecord, ModelProfileAvailability]:
    now = datetime.now(UTC)
    record = ModelProfileRecord(
        id=uuid4(),
        organization_id=ORGANIZATION_ID,
        profile_key=f"{provider_key}-{model_id}-{priority}",
        display_name=model_id,
        provider_key=provider_key,
        model_id=model_id,
        capabilities=frozenset({ModelCapability.TEXT}),
        cost_tier=ModelCostTier.PAID,
        priority=priority,
        enabled=readiness is not ModelReadiness.DISABLED,
        api_base_url="https://api.deepseek.com",
        created_at=now,
        updated_at=now,
    )
    return record, ModelProfileAvailability(
        profile=record,
        readiness=readiness,
        credential_env_name="DEEPSEEK_API_KEY",
        blocked_reason=None,
    )


def candidate() -> CandidateProfileRecord:
    return CandidateProfileRecord(
        id=CANDIDATE_ID,
        organization_id=ORGANIZATION_ID,
        actor_id=ACTOR_ID,
        display_name="候选人",
        source_filename="resume.pdf",
        resume_outline=(
            "教育经历\n211 院校计算机专业本科\n"
            "工作经历\n在银行业务系统中参与 Java 微服务开发与投产，负责接口实现和故障排查。\n"
            "项目经历\n参与分布式任务平台建设，完成监控告警与上线交付。"
        ),
        version=1,
        created_at=datetime.now(UTC),
    )


def job() -> GreetingJobInput:
    return GreetingJobInput(
        id="job-1",
        title="Java 微服务工程师",
        company="示例科技",
        recruiter="彭女士·招聘专员",
        description=(
            "岗位职责\n负责 Java 微服务接口开发、线上故障排查和版本交付。\n"
            "任职要求\n本科及以上学历，有分布式系统项目经验。"
        ),
        skills=("Java", "微服务", "分布式"),
        source_url="https://www.zhipin.com/job-1",
    )


def output(
    *,
    message: str | None = None,
    resume_ids: list[str] | None = None,
    jd_ids: list[str] | None = None,
) -> str:
    return json.dumps(
        {
            "message": message or (
                "彭女士您好，我是 211 院校计算机本科，参与过银行业务系统的 Java 微服务开发与投产，"
                "也做过分布式任务平台的监控告警和上线交付。岗位关注服务开发与故障排查，"
                "与我的实际经历比较贴近，方便的话想进一步了解团队当前项目。"
            ),
            "resume_evidence_ids": resume_ids or ["CV-002", "CV-004", "CV-006"],
            "jd_evidence_ids": jd_ids or ["JD-002", "JD-004"],
            "warnings": [],
        },
        ensure_ascii=False,
    )


class FakeContextRepository:
    def __init__(self, record: CandidateProfileRecord | None) -> None:
        self.record = record
        self.calls: list[tuple[UUID, UUID]] = []

    def get_candidate_profile(
        self,
        actor_id: UUID,
        profile_id: UUID,
    ) -> CandidateProfileRecord | None:
        self.calls.append((actor_id, profile_id))
        return self.record

    def get_candidate_profile_by_id(
        self,
        organization_id: UUID,
        profile_id: UUID,
    ) -> CandidateProfileRecord | None:
        self.calls.append((organization_id, profile_id))
        return self.record


class FakeGateway:
    def __init__(self, items: list[ModelProfileAvailability]) -> None:
        self.items = items
        self.selection = None

    def list_availability(self, organization_id: UUID) -> list[ModelProfileAvailability]:
        assert organization_id == ORGANIZATION_ID
        return self.items

    def resolve(self, organization_id: UUID, selection):
        assert organization_id == ORGANIZATION_ID
        self.selection = selection
        selected = next(item.profile for item in self.items if item.profile.id == selection.profile_id)
        return ModelResolution(
            profile=selected,
            reason=ModelResolutionReason.USER_SELECTED,
            readiness=ModelReadiness.READY,
            credential_env_name="DEEPSEEK_API_KEY",
            credential="secret",
        )


class FakeClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def complete_json(
        self,
        profile,
        credential_env_name,
        messages,
        *,
        api_key,
        options,
        operation,
    ) -> str:
        self.calls.append(
            {
                "profile": profile,
                "credential_env_name": credential_env_name,
                "messages": list(messages),
                "api_key": api_key,
                "options": options,
                "operation": operation,
            },
        )
        return self.responses.pop(0)


def build_service(
    responses: list[str],
    *,
    candidate_record: CandidateProfileRecord | None = None,
    availability: list[ModelProfileAvailability] | None = None,
):
    if availability is None:
        availability = [profile("deepseek", "deepseek-v4-pro")[1]]
    repository = FakeContextRepository(candidate() if candidate_record is None else candidate_record)
    gateway = FakeGateway(availability)
    client = FakeClient(responses)
    service = CareerGreetingService(
        context_repository=repository,
        model_gateway=gateway,
        model_client=client,
    )
    return service, repository, gateway, client


def test_generate_uses_actor_resume_and_exact_deepseek_profile() -> None:
    flash = profile("deepseek", "deepseek-v4-flash", priority=1)[1]
    lower_priority_pro = profile("deepseek", "deepseek-v4-pro", priority=20)[1]
    preferred_pro = profile("deepseek", "deepseek-v4-pro", priority=5)[1]
    service, repository, gateway, client = build_service(
        [output()],
        availability=[flash, lower_priority_pro, preferred_pro],
    )

    result = service.generate(
        ORGANIZATION_ID,
        ACTOR_ID,
        CANDIDATE_ID,
        job(),
    )

    assert repository.calls == [(ACTOR_ID, CANDIDATE_ID)]
    assert gateway.selection.mode is ModelSelectionMode.SPECIFIC_PROFILE
    assert gateway.selection.profile_id == preferred_pro.profile.id
    assert gateway.selection.required_capabilities == frozenset({ModelCapability.TEXT})
    assert result.model_id == "deepseek-v4-pro"
    assert result.provider_key == "deepseek"
    assert [item.id for item in result.resume_evidence] == ["CV-002", "CV-004", "CV-006"]
    assert [item.id for item in result.jd_highlights] == ["JD-002", "JD-004"]
    call = client.calls[0]
    assert call["operation"] == "greeting"
    assert call["options"].temperature == 0.2
    assert call["options"].max_tokens == 800
    assert call["options"].thinking is False
    user_prompt = call["messages"][-1].content
    assert "<candidate_resume>" in user_prompt
    assert "<job_description>" in user_prompt
    assert "彭女士·招聘专员" in user_prompt
    assert "严格输出 json" in user_prompt


def test_generate_rejects_missing_candidate_for_actor() -> None:
    service, _, _, client = build_service([output()], candidate_record=SimpleNamespace())
    service._context_repository.record = None

    with pytest.raises(GreetingCandidateNotFoundError):
        service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, job())

    assert client.calls == []


def test_admin_can_generate_with_another_users_candidate_in_same_organization() -> None:
    other_users_candidate = replace(candidate(), actor_id=uuid4())
    service, repository, _, client = build_service(
        [output()],
        candidate_record=other_users_candidate,
    )

    result = service.generate(
        ORGANIZATION_ID,
        ACTOR_ID,
        CANDIDATE_ID,
        job(),
        can_manage_all=True,
    )

    assert result.message.startswith("彭女士您好")
    assert repository.calls == [(ORGANIZATION_ID, CANDIDATE_ID)]
    assert len(client.calls) == 1


def test_generate_never_falls_back_from_deepseek_v4_pro() -> None:
    service, _, _, client = build_service(
        [output()],
        availability=[profile("deepseek", "deepseek-v4-flash")[1]],
    )

    with pytest.raises(GreetingModelUnavailableError):
        service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, job())

    assert client.calls == []


def test_generate_requires_complete_job_description() -> None:
    service, _, _, _ = build_service([output()])

    with pytest.raises(GreetingJobValidationError):
        service.generate(
            ORGANIZATION_ID,
            ACTOR_ID,
            CANDIDATE_ID,
            GreetingJobInput(id="job-1", title="Java 工程师", company="示例", recruiter="", description=""),
        )


def test_generate_retries_once_after_unknown_evidence_reference() -> None:
    service, _, _, client = build_service(
        [output(resume_ids=["CV-999"]), output()],
    )

    result = service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, job())

    assert result.message.startswith("彭女士您好")
    assert len(client.calls) == 2
    correction = client.calls[1]["messages"][-1].content
    assert "CV-999" in correction
    assert "纠正" in correction


def test_generate_rejects_second_invalid_response() -> None:
    service, _, _, client = build_service(
        [output(resume_ids=["CV-999"]), output(resume_ids=["CV-999"])],
    )

    with pytest.raises(GreetingGenerationError, match="CV-999"):
        service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, job())

    assert len(client.calls) == 2


def test_generate_can_focus_on_relevant_work_without_including_every_advantage() -> None:
    academic_candidate = replace(
        candidate(),
        resume_outline=(
            "教育经历\n某大学 QS前200 计算机博士\n"
            "论文成果\n以第一作者发表 SCI论文。\n"
            "工作经历\n参与 Java 服务开发与交付。"
        ),
    )
    omitted = output(
        message=(
            "彭女士您好，我是计算机专业毕业，参与过 Java 服务开发与交付。"
            "看到岗位关注微服务接口与版本交付，这与我的实际经历有交集，"
            "方便的话想进一步了解团队目前的项目重点。"
        ),
        resume_ids=["CV-006"],
    )
    service, _, _, client = build_service(
        [omitted],
        candidate_record=academic_candidate,
    )

    result = service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, job())

    assert result.message == json.loads(omitted)["message"]
    assert [item.id for item in result.resume_evidence] == ["CV-006"]
    assert len(client.calls) == 1


def test_generate_can_omit_portfolio_link_to_keep_the_message_focused() -> None:
    linked_candidate = replace(
        candidate(),
        resume_outline=(
            "教育经历\n东华大学（211）计算机硕士\n"
            "个人成果\n个人网站：https://example.dev\n"
            "工作经历\n参与 Java 微服务开发与投产。"
        ),
    )
    omitted = output(
        message=(
            "彭女士您好，我是东华大学 211 计算机硕士，参与过 Java 微服务开发与投产。"
            "看到岗位关注服务开发、故障排查和版本交付，与我的实际经历有交集，"
            "方便的话想进一步了解团队目前的项目。"
        ),
        resume_ids=["CV-002", "CV-006"],
    )
    service, _, _, client = build_service(
        [omitted],
        candidate_record=linked_candidate,
    )

    result = service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, job())

    assert result.message == json.loads(omitted)["message"]
    assert "https://example.dev" not in result.message
    assert len(client.calls) == 1


@pytest.mark.parametrize("claim", ["我使用 Python 开发服务", "我有 5 年 Java 开发经验", "我负责将性能提升 40%"])
def test_jd_cannot_support_explicit_candidate_claims(claim: str) -> None:
    jd = replace(
        job(),
        description=(
            "岗位职责\n使用 Java 和 Python 开发服务，将性能提升 40%。\n"
            "任职要求\n有 5 年开发经验。"
        ),
    )
    unsupported = output(message=(
        f"彭女士您好，想应聘您发布的 Java 微服务工程师。{claim}，"
        "也参与过银行业务系统的接口实现和故障排查，希望继续从事相关服务开发，方便进一步沟通吗？"
    ))
    service, _, _, client = build_service([unsupported, output()])

    result = service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, jd)

    assert result.message == json.loads(output())["message"]
    assert len(client.calls) == 2
    assert "JD 不能证明本人经历" in client.calls[1]["messages"][-1].content


def test_job_title_and_code_need_not_be_candidate_skills() -> None:
    jd = replace(job(), title="Python 平台开发工程师（编号 A123）")
    message = (
        "彭女士您好，想应聘 Python 平台开发工程师（编号 A123）。"
        "我参与过银行业务系统的 Java 微服务开发与投产，负责接口实现和故障排查，"
        "想了解团队是否考虑有相关服务开发经验的候选人？"
    )
    service, _, _, client = build_service([output(message=message, resume_ids=["CV-004"])])

    result = service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, jd)

    assert result.message == message
    assert len(client.calls) == 1


def test_job_requirement_can_be_mentioned_without_claiming_personal_experience() -> None:
    jd = replace(job(), description="岗位职责\n岗位使用 Python 开发服务。")
    message = (
        "彭女士您好，看到岗位使用 Python 开发服务。"
        "我参与过银行业务系统的 Java 微服务开发与投产，负责接口实现和故障排查，"
        "想了解团队是否考虑有相关服务开发经验的候选人？"
    )
    service, _, _, client = build_service(
        [output(message=message, resume_ids=["CV-004"], jd_ids=["JD-002"])],
    )

    result = service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, jd)

    assert result.message == message
    assert len(client.calls) == 1


def test_used_academic_claim_requires_its_own_cv_support_even_if_jd_mentions_it() -> None:
    jd = replace(job(), description="岗位职责\n负责 Java 微服务。\n任职要求\n硕士及以上学历。")
    unsupported = output(message=(
        "彭女士您好，我是计算机硕士，参与过银行业务系统的 Java 微服务开发与投产，"
        "负责接口实现和故障排查。想应聘您发布的微服务工程师，方便进一步沟通吗？"
    ))
    service, _, _, client = build_service([unsupported, output()])

    result = service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, jd)

    assert result.message == json.loads(output())["message"]
    assert len(client.calls) == 2


@pytest.mark.parametrize(
    "job_reference",
    ["看到岗位要求硕士", "想应聘博士研究员岗位"],
)
def test_asking_about_academic_job_requirements_is_not_a_candidate_degree_claim(job_reference: str) -> None:
    jd = replace(
        job(),
        title="博士研究员岗位",
        description="岗位职责\n负责 Java 服务开发。\n任职要求\n硕士及以上学历。",
    )
    message = (
        "彭女士您好，我是计算机本科，参与过 Java 微服务开发与投产，负责接口实现和故障排查。"
        f"{job_reference}，想了解是否考虑有相关服务开发经验的本科候选人？"
    )
    service, _, _, client = build_service(
        [output(message=message, resume_ids=["CV-002", "CV-004"])],
    )

    result = service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, jd)

    assert result.message == message
    assert len(client.calls) == 1


def test_unreferenced_resume_fact_cannot_be_used_in_message() -> None:
    candidate_record = replace(
        candidate(),
        resume_outline=candidate().resume_outline + "\n个人项目使用 ImaginarySDK。",
    )
    unsupported = output(message=(
        "彭女士您好，我参与过 Java 微服务开发与投产，也使用过 ImaginarySDK 完成个人项目。"
        "想应聘您发布的微服务工程师，岗位关注服务交付与故障排查，方便进一步沟通吗？"
    ))
    service, _, _, client = build_service([unsupported, output()], candidate_record=candidate_record)

    result = service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, job())

    assert result.message == json.loads(output())["message"]
    assert len(client.calls) == 2


def test_each_used_portfolio_link_requires_a_cited_cv_source() -> None:
    linked_candidate = replace(
        candidate(),
        resume_outline=candidate().resume_outline + (
            "\n作品集：https://example.dev/one\n个人网站：https://example.dev/two"
        ),
    )
    unsupported = output(
        message=(
            "彭女士您好，我参与过 Java 微服务开发与投产，负责接口实现和故障排查。"
            "作品集 https://example.dev/one；个人网站 https://example.dev/two，方便进一步沟通吗？"
        ),
        resume_ids=["CV-004", "CV-007"],
    )
    supported = output(
        message=json.loads(unsupported)["message"],
        resume_ids=["CV-004", "CV-007", "CV-008"],
    )
    service, _, _, client = build_service([unsupported, supported], candidate_record=linked_candidate)

    result = service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, job())

    assert result.message == json.loads(supported)["message"]
    assert len(client.calls) == 2


def test_portfolio_url_query_is_not_a_second_question() -> None:
    url = "https://github.com/example/project?tab=readme"
    linked_candidate = replace(candidate(), resume_outline=candidate().resume_outline + f"\n项目地址：{url}")
    message = (
        "彭女士您好，我参与过 Java 微服务开发与投产，负责接口实现和故障排查。"
        f"项目成果可查看 {url}，方便进一步沟通吗？"
    )
    service, _, _, client = build_service(
        [output(message=message, resume_ids=["CV-004", "CV-007"])],
        candidate_record=linked_candidate,
    )

    result = service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, job())

    assert result.message == message
    assert len(client.calls) == 1


def test_multiple_questions_are_corrected_once() -> None:
    unsupported = output(message=(
        "彭女士您好，我参与过银行业务系统的 Java 微服务开发与投产，负责接口实现和故障排查。"
        "想应聘您发布的微服务工程师，岗位还在招聘吗？薪资范围是多少？"
    ))
    service, _, _, client = build_service([unsupported, output()])

    result = service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, job())

    assert result.message == json.loads(output())["message"]
    assert len(client.calls) == 2
    assert "最多保留一个问题" in client.calls[1]["messages"][-1].content


def test_literal_validation_accepts_supported_combined_tech_stacks() -> None:
    message = "我使用 Java/SpringBoot 开发服务，并通过 Vue3+FastAPI+LangGraph 搭建应用。"
    source_text = "技术栈包括 Java、SpringBoot、Vue3、FastAPI、LangGraph。"

    CareerGreetingService._validate_literal_tokens(message, source_text)


def test_literal_validation_preserves_special_characters_inside_tech_names() -> None:
    message = "项目使用 C++/Python、C#、.NET、Node.js 和 CI/CD。"
    source_text = "掌握 C++、Python、C#、.NET、Node.js、CI/CD。"

    CareerGreetingService._validate_literal_tokens(message, source_text)


def test_literal_validation_rejects_unsupported_component_in_combined_stack() -> None:
    message = "我使用 Java/ImaginarySDK 开发服务。"
    source_text = "项目使用 Java 和 SpringBoot。"

    with pytest.raises(GreetingGenerationError, match="ImaginarySDK"):
        CareerGreetingService._validate_literal_tokens(message, source_text)


def test_regeneration_includes_previous_message_and_rejects_same_text() -> None:
    previous = json.loads(output())["message"]
    revised = (
        "彭女士您好，我是 211 院校计算机本科，看到岗位侧重 Java 服务交付和线上问题处理。"
        "我在银行业务系统中参与过"
        "微服务接口开发、投产和故障排查，也完成过分布式任务平台的监控告警建设。"
        "这些经历与岗位方向有交集，想和您进一步聊聊团队目前的工作重点。"
    )
    service, _, _, client = build_service([output(message=previous), output(message=revised)])

    result = service.generate(
        ORGANIZATION_ID,
        ACTOR_ID,
        CANDIDATE_ID,
        job(),
        previous_message=previous,
    )

    assert result.message == revised
    assert len(client.calls) == 2
    first_prompt = client.calls[0]["messages"][-1].content
    assert previous in first_prompt


def sized_message(length: int, *, suffix: str = "") -> str:
    """复用受证据支持的正文，验证长度变化不会造成程序拒绝或截断。"""
    base = json.loads(output())["message"]
    assert len(base) + len(suffix) <= length
    return base + "好" * (length - len(base) - len(suffix)) + suffix


@pytest.mark.parametrize("length", [180, 200, 201, 240, 2_501])
@pytest.mark.parametrize("suffix", ["", "😀"])
def test_generate_accepts_valid_message_without_a_length_gate(length: int, suffix: str) -> None:
    message = sized_message(length, suffix=suffix)
    service, _, _, client = build_service([output(message=message)])

    result = service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, job())

    assert result.message == message
    assert len(result.message) == length
    assert len(client.calls) == 1


def test_other_validation_can_be_corrected_without_rejecting_a_long_message() -> None:
    message = sized_message(240)
    service, _, _, client = build_service([
        output(message=message, resume_ids=["CV-999"]),
        output(message=message),
    ])

    result = service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, job())

    assert result.message == message
    assert len(client.calls) == 2
    retry_messages = client.calls[1]["messages"]
    assert json.loads(retry_messages[-2].content)["message"] == message
    correction = retry_messages[-1].content
    assert "CV-999" in correction
    assert "200 字左右" in correction
    assert "不得截断正文或 URL" in correction


def test_internal_spaces_in_a_long_message_are_not_collapsed_or_retried() -> None:
    base = json.loads(output())["message"]
    message = base + " " * (240 - len(base) - 1) + "好"
    assert len(message) == 240
    service, _, _, client = build_service([output(message=message)])

    result = service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, job())

    assert result.message == message
    assert len(client.calls) == 1


def test_internal_spaces_are_preserved_in_accepted_text() -> None:
    base = json.loads(output())["message"]
    message = base + " " * (200 - len(base) - 1) + "好"
    service, _, _, client = build_service([output(message=message)])

    result = service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, job())

    assert result.message == message
    assert len(result.message) == 200
    assert len(client.calls) == 1


def test_initial_and_regeneration_prompts_use_a_soft_length_target() -> None:
    previous = "旧稿参考" * 75
    service, _, _, client = build_service([output(message=sized_message(200))])

    service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, job(), previous_message=previous)

    system_prompt = client.calls[0]["messages"][0].content
    user_prompt = client.calls[0]["messages"][-1].content
    assert "200 字左右" in system_prompt
    assert "200 字左右" in user_prompt
    assert previous in user_prompt
    assert "不得截断正文或 URL" in system_prompt
    assert "不得截断正文或 URL" in user_prompt
    assert "硬上限" not in system_prompt + user_prompt
    assert "60 至 200" not in system_prompt + user_prompt


def test_truthful_short_message_is_not_padded_or_retried() -> None:
    short = "您好，我参与过 Java 微服务开发，负责接口实现和故障排查，想进一步沟通。"
    assert len(short) < 60
    service, _, _, client = build_service([
        output(message=short, resume_ids=["CV-004"], jd_ids=["JD-002"]),
    ])

    result = service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, job())

    assert result.message == short
    assert len(client.calls) == 1


def test_empty_model_message_still_fails_after_one_correction() -> None:
    blank = json.loads(output())
    blank["message"] = "   "
    service, _, _, client = build_service([json.dumps(blank), json.dumps(blank)])

    with pytest.raises(GreetingGenerationError, match="没有生成招呼语"):
        service.generate(ORGANIZATION_ID, ACTOR_ID, CANDIDATE_ID, job())

    assert len(client.calls) == 2

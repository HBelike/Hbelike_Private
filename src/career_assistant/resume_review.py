"""已确认简历绑定后的单次开场初评，复用持久化 Turn 链路。"""
from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

from src.career_assistant.contracts import CareerInboundMessage, ModelSelectionRequest, ModelSelectionMode
from src.career_assistant.turn_payloads import prepare_turn_payload


RESUME_REVIEW_SOURCE = "resume_review"
RESUME_REVIEW_NOTE = "简历已确认，开始初评。"
RESUME_REVIEW_TASK = """这是确认简历后的自动开场初评。请直接与用户交流，保持在约300至500字：
先用一句话说明已阅读简历，概括1至2项有依据的优势；再给出最值得修改的3点，
每点引用或准确指向简历中的具体内容，说明为什么以及怎么改。
没有JD时只评简历内容与表达，不追问JD后才开始；有JD时结合岗位要求，明确缺口。
未提供的数字、成果、技能和经历不得编造，建议中的示例也不得引入未确认事实。
不能把仅有年份补成具体月份，不能把“增加索引”改成未经确认的复合索引或显著提速。
缺指标时建议记录真实测量结果，不要求估算绩效数字；技能不足时询问实际掌握程度，不直接写精通或熟练。
本次不要生成整份简历、不要堆匹配表、不要输出求职信或冗长面试材料。
结尾给一个具体下一步选项，例如邀请用户先修改某段项目经历。
这是后台单次初评，后续对话按用户问题正常回答，不要求继续套用本流程。"""


def enqueue_resume_review(services, context, actor_id: UUID, *, model_selection=None):
    """只使用服务端已确认绑定，按会话和简历版本幂等入队。"""
    if context is None or context.candidate is None:
        return None
    candidate = context.candidate
    activation = services.skill_runtime.resolve(
        (), f"/tailored-resume-generator {RESUME_REVIEW_TASK}",
    )
    skills = tuple(
        replace(
            skill, invocation_source="automatic", primary=False,
            instructions=f"{skill.instructions}\n\n## 本次自动开场任务\n{RESUME_REVIEW_TASK}",
        )
        for skill in activation.skills
    )
    selection = model_selection
    if selection is None:
        selection = services.conversation_repository.get_last_model_selection(actor_id, context.conversation_id)
        if selection is None or selection.mode is ModelSelectionMode.FREE_QUOTA_FIRST:
            selection = ModelSelectionRequest(mode=ModelSelectionMode.PLATFORM_DEFAULT)
    target = context.target_role
    inbound = CareerInboundMessage(
        turn_id=uuid4(), conversation_id=context.conversation_id, actor_id=actor_id,
        # 历史只保存动作说明，不保存Slash命令，防止后续会话继承自动Skill。
        text=RESUME_REVIEW_NOTE, effective_text=RESUME_REVIEW_TASK,
        model_selection=selection, activated_skills=skills,
        candidate_profile_context=candidate.resume_outline,
        target_role_context=(
            f"公司：{target.company_name}\n岗位：{target.role_name}\n{target.job_text}"
            if target is not None else ""
        ),
        context_binding_version=context.binding_version,
    )
    inbound.validate()
    payload = prepare_turn_payload(inbound, (), source=RESUME_REVIEW_SOURCE)
    return services.turn_job_repository.enqueue(
        turn_id=inbound.turn_id, conversation_id=inbound.conversation_id,
        actor_id=actor_id, model_selection=selection,
        input_kind_codes=frozenset({RESUME_REVIEW_SOURCE}), payload=payload,
        idempotency_key=f"resume_review:{candidate.id}:v{candidate.version}",
        allow_queue_when_busy=True,
    )

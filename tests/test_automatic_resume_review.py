"""确认绑定后的开场初评及其单轮Skill边界。"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, Request

from src.career_assistant.contracts import ModelSelectionMode, ModelSelectionRequest
from src.career_assistant.resume_review import enqueue_resume_review, RESUME_REVIEW_NOTE
from src.career_assistant.skill_runtime import CareerSkillRuntime
from src.career_assistant.skill_tools import SkillToolRegistry
from src.career_assistant.turn_payloads import restore_turn_input
from src.career_assistant.web import router as routes
from src.config.config_manager import AppConfig
from src.services.skill_library_service import SkillLibraryService


def setup_case(target=False):
    root = Path(__file__).resolve().parents[1]
    runtime = CareerSkillRuntime(SkillLibraryService(AppConfig(root, root/'config/app.yaml', {})))
    context = SimpleNamespace(
        conversation_id=uuid4(), binding_version=2,
        candidate=SimpleNamespace(id=uuid4(), version=3, resume_outline='负责 FastAPI 接口，未提供量化成果'),
        target_role=(SimpleNamespace(company_name='示例公司', role_name='后端', job_text='需要 Python') if target else None),
    )
    services = SimpleNamespace(
        skill_runtime=runtime,
        conversation_repository=SimpleNamespace(get_last_model_selection=Mock(return_value=None)),
        turn_job_repository=SimpleNamespace(enqueue=Mock(return_value=SimpleNamespace(id=uuid4()))),
    )
    return services, context


def test_resume_only_review_uses_confirmed_snapshot_and_does_not_inherit():
    services, context = setup_case()
    actor = uuid4()
    enqueue_resume_review(services, context, actor)
    call = services.turn_job_repository.enqueue.call_args.kwargs
    payload = call['payload']
    assert call['actor_id'] == actor
    assert call['model_selection'].mode == ModelSelectionMode.PLATFORM_DEFAULT
    assert call['allow_queue_when_busy'] is True
    assert call['idempotency_key'] == f'resume_review:{context.candidate.id}:v3'
    assert payload.input_text == RESUME_REVIEW_NOTE
    assert '/' not in payload.input_text
    assert payload.request_metadata['candidate_profile_context'] == context.candidate.resume_outline
    assert payload.request_metadata['target_role_context'] == ''
    restored = restore_turn_input(SimpleNamespace(
        id=payload.turn_id, actor_id=actor, conversation_id=context.conversation_id,
        requested_selection_mode=ModelSelectionMode.FREE_QUOTA_FIRST, requested_model_profile_id=None,
    ), payload).inbound_message
    assert restored.activated_skills[0].invocation_source == 'automatic'
    assert restored.activated_skills[0].name == 'tailored-resume-generator'
    assert '不得新增' in restored.activated_skills[0].instructions
    assert '没有JD时只评简历' in restored.activated_skills[0].instructions
    assert services.skill_runtime.resolve_for_conversation([], '聊面试准备', [payload.input_text]).skills == ()
    assert SkillToolRegistry(Mock()).definitions_for(restored.activated_skills) == ()


def test_review_with_jd_honors_current_model_and_new_resume_version():
    services, context = setup_case(target=True)
    selection = ModelSelectionRequest(mode=ModelSelectionMode.SPECIFIC_PROFILE, profile_id=uuid4())
    enqueue_resume_review(services, context, uuid4(), model_selection=selection)
    call = services.turn_job_repository.enqueue.call_args.kwargs
    assert call['model_selection'] == selection
    assert '需要 Python' in call['payload'].request_metadata['target_role_context']
    assert call['payload'].request_metadata['context_binding_version'] == 2
    key = call['idempotency_key']
    context.binding_version = 3
    enqueue_resume_review(services, context, uuid4())
    assert services.turn_job_repository.enqueue.call_args.kwargs['idempotency_key'] == key
    context.candidate.version = 4
    enqueue_resume_review(services, context, uuid4())
    assert services.turn_job_repository.enqueue.call_args.kwargs['idempotency_key'] != key


def test_no_candidate_does_not_load_skill_or_enqueue():
    services = SimpleNamespace(skill_runtime=Mock(), turn_job_repository=Mock())
    assert enqueue_resume_review(services, SimpleNamespace(candidate=None), uuid4()) is None
    services.skill_runtime.resolve.assert_not_called()
    services.turn_job_repository.enqueue.assert_not_called()


def test_binding_routes_enqueue_after_confirmation_and_preserve_binding_on_failure():
    services, context = setup_case()
    services.context_repository = SimpleNamespace(bind_conversation=Mock(return_value=context))
    request = Request({'type':'http', 'app':FastAPI()})
    body = routes.UpdateConversationContextRequest(candidate_profile_id=context.candidate.id)
    with (patch.object(routes, 'get_career_services', return_value=services),
          patch.object(routes, '_enqueue_job_assessment'),
          patch.object(routes, '_current_job_assessment', return_value=None),
          patch.object(routes, '_conversation_context_payload', return_value={'bound':True}),
          patch.object(routes, 'enqueue_resume_review', side_effect=RuntimeError('unavailable')) as enqueue):
        payload = routes.update_conversation_context(context.conversation_id, body, request, BackgroundTasks())
    assert payload['bound'] is True
    assert payload['resume_review']['status'] == 'unavailable'
    services.context_repository.bind_conversation.assert_called_once()
    enqueue.assert_called_once()

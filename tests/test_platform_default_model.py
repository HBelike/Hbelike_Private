"""平台默认模型不依赖个人连接，且不可静默改用免费模型。"""
from dataclasses import replace
from datetime import datetime, UTC
from uuid import uuid4
import pytest

from src.career_assistant.contracts import ModelCapability, ModelSelectionMode, ModelSelectionRequest
from src.career_assistant.model_gateway import ModelGateway, ModelResolutionReason
from src.career_assistant.persistence.model_profile_repository import ModelProfileRecord, ModelCostTier
from src.career_assistant.settings import ModelGatewaySettings
from src.career_assistant.web.turn_router import EnqueueTurnRequest


def profile(model, tier=ModelCostTier.PAID, priority=100):
    now=datetime.now(UTC)
    return ModelProfileRecord(uuid4(),uuid4(),model,model,'deepseek',model,frozenset({ModelCapability.TEXT}),tier,priority,True,None,now,now)


class Repository:
    def __init__(self, profiles): self.profiles=profiles
    def list_profiles(self, organization_id, *, include_disabled=False):
        return [p for p in self.profiles if p.organization_id==organization_id and (p.enabled or include_disabled)]
    def get_profile(self, organization_id, profile_id):
        return next((p for p in self.profiles if p.id==profile_id and p.organization_id==organization_id),None)
    def has_stored_credential(self,*_): return False


def test_default_resolves_pro_even_when_flash_and_free_model_have_higher_priority():
    pro=profile('deepseek-v4-pro')
    flash=replace(profile('deepseek-v4-flash',priority=1),organization_id=pro.organization_id)
    free=replace(profile('free-model',ModelCostTier.FREE_QUOTA,0),organization_id=pro.organization_id)
    gateway=ModelGateway(Repository([free,flash,pro]),ModelGatewaySettings(True,False,{'deepseek':'DEEPSEEK_API_KEY'}),{'DEEPSEEK_API_KEY':'synthetic-shared-key'})
    result=gateway.resolve(pro.organization_id,ModelSelectionRequest(mode=ModelSelectionMode.PLATFORM_DEFAULT))
    assert result.profile.id==pro.id
    assert result.reason==ModelResolutionReason.PLATFORM_DEFAULT
    assert result.credential=='synthetic-shared-key'
    metadata=gateway.platform_default_descriptor(gateway.list_availability(pro.organization_id))
    assert metadata['profile_id']==str(pro.id)
    assert 'synthetic-shared-key' not in str(metadata)
    assert gateway.resolve(pro.organization_id,ModelSelectionRequest()).profile.id==free.id
    assert gateway.resolve(pro.organization_id,ModelSelectionRequest(mode=ModelSelectionMode.SPECIFIC_PROFILE,profile_id=flash.id)).profile.id==flash.id


def test_missing_or_unready_default_does_not_fall_back_to_another_profile():
    pro=profile('deepseek-v4-pro')
    flash=replace(profile('deepseek-v4-flash'),organization_id=pro.organization_id)
    settings=ModelGatewaySettings(True,False,{'deepseek':'DEEPSEEK_API_KEY'})
    gateway=ModelGateway(Repository([flash]),settings,{'DEEPSEEK_API_KEY':'test'})
    with pytest.raises(LookupError,match='联系管理员'):
        gateway.resolve(pro.organization_id,ModelSelectionRequest(mode=ModelSelectionMode.PLATFORM_DEFAULT))
    gateway=ModelGateway(Repository([pro,flash]),settings,{})
    with pytest.raises(PermissionError,match='联系管理员'):
        gateway.resolve(pro.organization_id,ModelSelectionRequest(mode=ModelSelectionMode.PLATFORM_DEFAULT))
    assert gateway.platform_default_descriptor(gateway.list_availability(pro.organization_id))['readiness']=='credential_required'


def test_text_api_omitted_selection_uses_platform_default_and_rejects_conflicting_id():
    request=EnqueueTurnRequest(text='请点评简历')
    assert request.selection_mode==ModelSelectionMode.PLATFORM_DEFAULT
    with pytest.raises(ValueError,match='平台默认'):
        ModelSelectionRequest(mode=ModelSelectionMode.PLATFORM_DEFAULT,profile_id=uuid4()).validate()

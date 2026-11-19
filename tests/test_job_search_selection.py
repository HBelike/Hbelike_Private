"""候选分块应覆盖所有岗位，预算内合并短名单而不伪造引用。"""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.career_assistant.contracts import ModelCapability
from src.career_assistant.intake_graph import ModelTurnContext
from src.career_assistant.job_search_context import snapshot_context_messages
from src.career_assistant.job_search_selection import select_search_candidates
from src.career_assistant.model_clients import ModelInvocationError
from src.career_assistant.persistence.model_profile_repository import ModelContextPolicy


def context(text="推荐这批岗位", count=100):
    batch = {"id": "batch", "batch_no": 1, "revision": 3,
             "conditions": {"keyword": "示例公司", "city": "杭州"}, "loaded_count": count}
    return ModelTurnContext(text, "", "", frozenset({ModelCapability.TEXT}), False, (), (), 0,
        redacted_resume_outline="真实项目：使用Python开发数据处理程序。",
        job_search_snapshot={"catalog": [batch], "active_batch_id": "batch", "focused_job_ref": "S1-J12",
            "batches": [{**batch, "jobs": [{"job_ref": f"S1-J{index}", "title": f"岗位{index}",
                "company": "示例公司", "skills": ["数据处理能力" * 8], "description": "完整JD不应进入初筛"}
                for index in range(1, count + 1)]}]})


def resolution():
    return SimpleNamespace(profile=SimpleNamespace(context_policy=ModelContextPolicy(
        context_window_tokens=16384, reserved_output_tokens=4096)),
        credential_env_name="SYNTHETIC_ONLY", credential="synthetic")


class SelectionClient:
    def __init__(self, invented=False):
        self.seen = []
        self.calls = []
        self.invented = invented

    def complete(self, profile, env_name, prompt, **kwargs):
        data = json.loads(prompt[1].content)
        refs = [item["job_ref"] for item in data["candidates"]]
        self.seen.extend(refs)
        self.calls.append((prompt, kwargs))
        return json.dumps({"selected_job_refs": ["S99-J999"] if self.invented else refs[-3:]})


def test_100_candidates_are_all_seen_before_shortlist_is_merged():
    original = context()
    client = SelectionClient()
    selected = select_search_candidates(original, resolution(), client, lambda usage: None)
    selection = selected.job_search_snapshot["candidate_selection"]
    assert set(client.seen) == {f"S1-J{index}" for index in range(1, 101)}
    assert selection["covered_count"] == selection["total_count"] == 100
    assert len(selection["selected_job_refs"]) <= 6
    assert "S1-J100" in selection["selected_job_refs"]
    assert "candidate_selection" not in original.job_search_snapshot
    for prompt, kwargs in client.calls:
        assert "完整JD不应进入初筛" not in str(prompt)
        assert kwargs["options"].max_tokens <= 512
    final_prompt = str(snapshot_context_messages(selected.job_search_snapshot, selected.redacted_user_text, token_budget=3000))
    assert '"covered_count": 100' in final_prompt
    assert '"total_count": 100' in final_prompt


@pytest.mark.parametrize("text", ["你好", "这个岗位适合我吗"])
def test_normal_chat_and_single_focus_do_not_run_whole_batch_selection(text):
    original = context(text)
    client = SelectionClient()
    assert select_search_candidates(original, resolution(), client, lambda usage: None) is original
    assert not client.calls


def test_invented_candidate_reference_is_rejected_before_final_answer():
    with pytest.raises(ModelInvocationError, match="未返回有效引用"):
        select_search_candidates(context(), resolution(), SelectionClient(invented=True), lambda usage: None)


@pytest.mark.parametrize("state", ["dirty", "ambiguous"])
def test_unready_or_ambiguous_scope_does_not_invoke_candidate_model(state):
    original = context("推荐这批岗位" if state == "dirty" else "比较两批岗位")
    frozen = dict(original.job_search_snapshot)
    if state == "dirty":
        frozen["uncommitted_batch_ids"] = ["batch"]
    else:
        # 只有一批时不能假装已经知道用户要求的另一批。
        assert len(frozen["catalog"]) == 1
    original = replace(original, job_search_snapshot=frozen)
    client = SelectionClient()
    assert select_search_candidates(original, resolution(), client, lambda usage: None) is original
    assert client.calls == []


@pytest.mark.parametrize("text", ["推荐10个岗位", "推荐十个岗位"])
def test_explicit_ten_recommendations_keep_at_least_ten_candidates(text):
    class TenSelectionClient(SelectionClient):
        def complete(self, profile, env_name, prompt, **kwargs):
            data = json.loads(prompt[1].content)
            refs = [item["job_ref"] for item in data["candidates"]]
            self.seen.extend(refs)
            self.calls.append((prompt, kwargs))
            return json.dumps({"selected_job_refs": refs[-10:]})

    client = TenSelectionClient()
    selected = select_search_candidates(context(text), resolution(), client, lambda usage: None)
    selection = selected.job_search_snapshot["candidate_selection"]
    assert set(client.seen) == {f"S1-J{index}" for index in range(1, 101)}
    assert selection["covered_count"] == 100
    assert 10 <= len(selection["selected_job_refs"]) <= 20
    assert "S1-J100" in selection["selected_job_refs"]


def test_legacy_excerpt_remains_incomplete_even_when_entire_excerpt_fits_budget():
    original = context("这个岗位适合我吗", count=15)
    frozen = original.job_search_snapshot
    frozen["catalog"][0]["legacy_imported"] = True
    job = frozen["batches"][0]["jobs"][11]
    job.pop("description")
    job["description_excerpt"] = "旧记录仅保存部分职责。"
    messages = snapshot_context_messages(frozen, original.redacted_user_text, token_budget=6000)
    payload = json.loads(messages[1].content.split(">", 1)[1].rsplit("<", 1)[0])
    assert payload["jobs"][0]["job_ref"] == "S1-J12"
    assert "旧记录仅保存部分职责" in payload["jobs"][0]["jd_evidence"]
    assert payload["jobs"][0]["jd_complete"] is False

"""真实岗位标识与展示摘要采用各自的长度约束。"""

from copy import deepcopy

import pytest

from src.career_assistant.job_search_cards import new_job_search_card, patch_job_search_card


def card():
    return new_job_search_card("阿里云", "上海", auto_search=True)


@pytest.mark.parametrize("field", ["result_summary", "selected_job"])
def test_long_security_identifier_can_be_saved_without_truncation(field):
    job = {"id": "security-" + "a" * 400, "title": "大模型前沿部署工程师（通用场景）-上海",
           "company": "阿里云", "salary": "20-40K·16薪", "city": "上海"}
    patch = {field: {"count": 15, "jobs": [job]} if field == "result_summary" else job}
    result = patch_job_search_card(card(), patch)
    saved = result[field]["jobs"][0] if field == "result_summary" else result[field]
    assert saved == job


def test_fifteen_jobs_only_save_ten_summaries_and_keep_total():
    jobs = [{"id": f"{index}-" + "s" * 1200, "title": "大模型部署工程师", "company": "阿里云"}
            for index in range(10)]
    result = patch_job_search_card(card(), {"status": "searched", "result_summary": {"count": 15, "jobs": jobs}})
    assert result["status"] == "searched"
    assert result["result_summary"] == {"count": 15, "jobs": jobs}


@pytest.mark.parametrize("key,maximum,label", [
    ("id", 8192, "岗位标识"), ("title", 240, "岗位标题"), ("company", 240, "公司名称"),
    ("salary", 120, "薪资"), ("city", 120, "工作城市"),
])
def test_field_specific_limits_and_errors_leave_current_card_unchanged(key, maximum, label):
    current = card()
    before = deepcopy(current)
    result = patch_job_search_card(current, {"selected_job": {key: "字" * maximum}})
    assert result["selected_job"][key] == "字" * maximum
    with pytest.raises(ValueError, match=label):
        patch_job_search_card(current, {"status": "selected", "selected_job": {key: "字" * (maximum + 1)}})
    assert current == before


def test_summary_characters_use_unicode_codepoints():
    title = "🙂" * 240
    assert patch_job_search_card(card(), {"selected_job": {"title": title}})["selected_job"]["title"] == title


def test_invalid_field_type_reports_which_field_failed():
    with pytest.raises(ValueError, match="公司名称必须是文本"):
        patch_job_search_card(card(), {"selected_job": {"company": []}})


def test_match_evidence_fields_persist_and_enforce_excerpt_limit():
    job = {"id": "job", "title": "Agent开发", "company": "网易云", "experience": "3-5年",
           "education": "本科", "skills": "LangGraph、RAG", "description_excerpt": "要求" * 1000}
    patch = {"result_summary": {"count": 15, "jobs": [job]}}
    assert patch_job_search_card(card(), patch)["result_summary"]["jobs"] == [job]
    job["description_excerpt"] += "字"
    with pytest.raises(ValueError, match="岗位描述摘录"):
        patch_job_search_card(card(), patch)

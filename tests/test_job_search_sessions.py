"""批次岗位版本的归一化与幂等契约。"""
import pytest
from src.career_assistant.job_search_sessions import normalize_job, normalize_conditions, operation_digest


def test_full_jd_and_stable_platform_identifier_are_preserved():
    job = normalize_job({"id": "s" * 500, "jobId": "job-12", "title": "Agent工程师", "degree": "本科", "description": "前" * 2300 + "必须博士"})
    assert job["bridge_key"] == "job-12"
    assert job["education"] == "本科"
    assert job["description"].endswith("必须博士")


def test_snapshot_normalization_rejects_silent_jd_truncation():
    with pytest.raises(ValueError, match="30000"):
        normalize_job({"id": "id", "description": "长" * 30001})


def test_conditions_are_explicit_and_operation_hash_does_not_depend_on_key_order():
    assert normalize_conditions({"keyword": " 网易云 ", "city": "杭州"})["keyword"] == "网易云"
    assert operation_digest({"page": 1, "jobs": []}) == operation_digest({"jobs": [], "page": 1})
    assert operation_digest({"page": 1}) != operation_digest({"page": 2})

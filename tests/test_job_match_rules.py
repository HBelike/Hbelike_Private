"""通用岗位规则的事实边界和计算回归。"""
import unittest
from copy import deepcopy
from datetime import date

from src.career_assistant.job_match_rules import validate_match


JD = {"JD-001": "至少3年全职经验；能够完成策划和复盘。", "JD-002": "本科及以上。", "JD-003": "年龄不超过30岁。"}
CV = {"CV-001": "2023.01—2024.12，全职；2024.01—2025.12，全职。独立策划活动。",
      "CV-002": "硕士在读。", "CV-003": "出生日期：1997-11-05。", "CV-004": "尚未承担过复盘。"}


def leaf(verdict="supported", **extra):
    return {"label": "活动策划", "jd_source_ids": ["JD-001"], "cv_source_ids": ["CV-001"],
            "kind": "semantic", "verdict": verdict, "relation": "direct", "reason": "简历有明确策划经历。", **extra}


def payload(rule=None, tier="core", dimension="活动与复盘"):
    return {"schema_version": "career-job-assessment-v2", "role_summary": "依照当前 JD 分析",
            "job_sections": [{"category": "required_qualification", "title": "岗位要求", "jd_source_ids": ["JD-001"]}],
            "dimensions": [{"id": "delivery", "label": dimension, "description": "对应JD的具体工作活动"}],
            "items": [{"id": "one", "dimension_id": "delivery", "tier": tier, "title": "独立策划并复盘",
                       "jd_source_ids": ["JD-001"], "rule": rule or leaf()}]}


def score(raw, jd=None, cv=None):
    return validate_match(raw, jd_lines=jd or JD, cv_lines=cv or CV, as_of=date(2026, 11, 4))


class MatchRulesTests(unittest.TestCase):
    def test_unknown_is_interval_not_false_failure_or_free_points(self):
        rule = {"label": "活动", "jd_source_ids": ["JD-001"], "operator": "ALL", "children": [
            leaf(), leaf("needs_confirmation", label="复盘", cv_source_ids=[], reason="未描述复盘")
        ]}
        report = score(payload(rule))
        self.assertEqual((report["summaries"]["core"]["score"], report["summaries"]["core"]["score_upper"]), (50, 100))
        self.assertEqual(report["items"][0]["display_status"], "partial")
        self.assertEqual(report["items"][0]["weight"], 1)

    def test_any_and_minimum_rules(self):
        for operator, minimum, expected in [("ANY", 1, 100), ("AT_LEAST_N", 2, 50)]:
            rule = {"label": "要求", "jd_source_ids": ["JD-001"], "operator": operator, "minimum": minimum,
                    "children": [leaf(), leaf("needs_confirmation", label="另一项", cv_source_ids=[])]}
            report = score(payload(rule))
            self.assertEqual(report["summaries"]["core"]["score"], expected)
            self.assertEqual(report["summaries"]["core"]["score_upper"], 100)

    def test_hard_condition_cannot_be_offset_by_bonus(self):
        raw = payload(leaf("unsupported", cv_source_ids=["CV-004"]), tier="hard")
        raw["items"].append({**deepcopy(raw["items"][0]), "id": "bonus", "title": "加分", "tier": "bonus", "rule": leaf()})
        report = score(raw)
        self.assertEqual(report["summaries"]["hard"]["verdict"], "unsupported")
        self.assertEqual(report["summaries"]["bonus"]["score"], 100)

    def test_explicit_negative_requires_evidence(self):
        with self.assertRaisesRegex(ValueError, "without_cv_evidence"):
            score(payload(leaf("unsupported", cv_source_ids=[])))

    def test_unknown_reference_rejected(self):
        with self.assertRaisesRegex(ValueError, "reference_unknown"):
            score(payload(leaf(cv_source_ids=["CV-999"])))

    def test_conflict_keeps_both_sources(self):
        report = score(payload(leaf("conflict", cv_source_ids=["CV-001", "CV-004"], reason="两段描述冲突"), tier="hard"))
        self.assertEqual(report["items"][0]["display_status"], "conflict")
        self.assertEqual(report["summaries"]["hard"]["verdict"], "needs_confirmation")

    def test_dynamic_non_technical_dimensions(self):
        for dimension in ["客户开发", "品牌设计", "账务处理", "研究方向", "课堂教学", "软件交付"]:
            self.assertEqual(score(payload(dimension=dimension))["dimensions"][0]["label"], dimension)

    def test_mention_only_does_not_prove_deep_capability(self):
        report = score(payload(leaf(relation="mention_only")))
        self.assertEqual(report["summaries"]["core"]["score"], 0)
        self.assertEqual(report["summaries"]["core"]["score_upper"], 100)

    def test_overlapping_months_are_not_double_counted(self):
        quote = CV["CV-001"]
        rule = leaf(kind="months", predicate={"jd_quote": "至少3年全职经验", "comparison": "gte", "targets": [3], "unit": "年",
                    "intervals": [{"quote": span, "cv_source_ids": ["CV-001"]} for span in ["2023.01—2024.12", "2024.01—2025.12"]]})
        report = score(payload(rule, tier="hard"))
        self.assertEqual(report["items"][0]["leaves"][0]["calculation"]["actual"], 36)
        self.assertEqual(report["summaries"]["hard"]["verdict"], "supported")

    def test_incomplete_or_future_months_are_unknown(self):
        for span in ["2023—2026", "2027.01—2028.01"]:
            rule = leaf(kind="months", predicate={"jd_quote": "至少3年全职经验", "comparison": "gte", "targets": [3], "unit": "年",
                        "intervals": [{"quote": span, "cv_source_ids": ["CV-001"]}]})
            report = score(payload(rule), cv={"CV-001": span})
            self.assertEqual(report["items"][0]["verdict"], "needs_confirmation")

    def test_degree_in_progress_is_not_attained(self):
        raw = payload(leaf(kind="degree", jd_source_ids=["JD-002"], cv_source_ids=["CV-002"], predicate={"jd_quote": "本科及以上", "cv_quote": "硕士在读"}), tier="hard")
        raw["items"][0]["jd_source_ids"] = ["JD-002"]
        self.assertEqual(score(raw)["summaries"]["hard"]["verdict"], "needs_confirmation")

    def test_age_uses_birth_date_and_reference_date(self):
        raw = payload(leaf(kind="age", jd_source_ids=["JD-003"], cv_source_ids=["CV-003"],
                          predicate={"jd_quote": "年龄不超过30岁", "cv_quote": CV["CV-003"], "comparison": "lte", "targets": [30]}), tier="hard")
        raw["items"][0]["jd_source_ids"] = ["JD-003"]
        self.assertEqual(score(raw)["items"][0]["leaves"][0]["calculation"]["actual"], 28)

    def test_unverified_institution_never_passes_from_model_memory(self):
        report = score(payload(leaf(kind="institution"), tier="hard"))
        self.assertEqual(report["summaries"]["hard"]["verdict"], "needs_confirmation")

    def test_no_core_or_no_hard_is_not_zero_or_pass(self):
        raw = payload(tier="bonus")
        report = score(raw)
        self.assertIsNone(report["summaries"]["core"]["score"])
        self.assertEqual(report["summaries"]["hard"]["verdict"], "not_specified")

    def test_duplicate_parent_cannot_inflate_weight(self):
        raw = payload()
        raw["items"].append({**deepcopy(raw["items"][0]), "id": "duplicate"})
        with self.assertRaisesRegex(ValueError, "requirement_duplicate"):
            score(raw)

    def test_model_cannot_supply_percentage(self):
        raw = payload()
        raw["items"][0]["score"] = 2
        raw["items"][0]["weight"] = 100
        self.assertEqual(score(raw)["summaries"]["core"]["score"], 100)
        self.assertEqual(score(raw)["items"][0]["weight"], 1)

    def test_invalid_threshold_and_fabricated_quotes_are_unknown(self):
        rule = leaf(kind="number", predicate={"jd_quote": "至少3年全职经验", "cv_quote": "5年", "comparison": "gte", "targets": [3], "unit": "年"})
        self.assertEqual(score(payload(rule))["items"][0]["verdict"], "needs_confirmation")

    def test_official_university_lists_and_qs_edition(self):
        from src.career_assistant.university_evidence import verify_university
        now = date(2026, 11, 4)
        self.assertEqual(verify_university('985院校', '北京大学，本科', now)[0], 'supported')
        self.assertEqual(verify_university('211院校', '苏州大学，本科', now)[0], 'supported')
        self.assertEqual(verify_university('985院校', '北京大学附属学校，本科', now)[0], 'needs_confirmation')
        self.assertEqual(verify_university('本科985院校', '北京大学，硕士', now, '北京大学，硕士')[0], 'needs_confirmation')
        self.assertEqual(verify_university('QS世界大学综合榜前50', 'Massachusetts Institute of Technology (MIT)，硕士', now)[0], 'supported')
        self.assertEqual(verify_university('QS2025世界大学综合榜前50', 'Massachusetts Institute of Technology (MIT)，硕士', now)[0], 'needs_confirmation')
        self.assertEqual(verify_university('QS世界大学综合榜前50', '某未收录大学，硕士', now)[0], 'needs_confirmation')

    def test_integer_numeric_threshold_is_recomputed(self):
        rule = leaf(kind='number', predicate={'jd_quote': '至少3次', 'cv_quote': '2次', 'comparison': 'gte', 'targets': [3], 'unit': '次'})
        report = score(payload(rule), jd={'JD-001': '至少3次活动'}, cv={'CV-001': '独立开展活动2次'})
        self.assertEqual(report['items'][0]['verdict'], 'unsupported')

    def test_fulltime_experience_does_not_count_internship(self):
        rule = leaf(kind='months', predicate={'jd_quote': '至少3年全职经验', 'comparison': 'gte', 'targets': [3], 'unit': '年',
                    'intervals': [{'quote': '2023.01—2025.12', 'cv_source_ids': ['CV-001']}]})
        report = score(payload(rule), cv={'CV-001': '2023.01—2025.12 实习'})
        self.assertEqual(report['items'][0]['verdict'], 'needs_confirmation')

    def test_v2_result_flows_through_existing_assessment_service(self):
        from tests.test_career_job_assessment import _ServiceUnderTest, _context_records
        candidate, target = _context_records(resume=CV['CV-001'], job_text=JD['JD-001'])
        service = _ServiceUnderTest(candidate=candidate, target=target, outputs=[payload()])
        result = service.run_assessment(service.test_repository.record.id)
        self.assertEqual(result.status.value, 'ready')
        self.assertEqual(result.result['schema_version'], 'career-job-assessment-v2')
        self.assertEqual(result.result['summaries']['core']['score'], 100)

    def test_numbering_preserves_long_lines_and_late_publications(self):
        from src.career_assistant.job_assessment import number_source_lines
        text = '\n'.join(['长段落' * 500] + [f'经历{i}' for i in range(250)] + ['末尾论文'])
        numbered = number_source_lines(text, 'CV')
        self.assertEqual(len(numbered['CV-001']), 1500)
        self.assertIn('末尾论文', numbered.values())

    def test_day_precision_does_not_count_partial_start_and_end_months(self):
        rule = leaf(kind='months', predicate={'jd_quote': '至少3年全职经验', 'comparison': 'gte', 'targets': [3], 'unit': '年',
                    'intervals': [{'quote': '2023.01.31—2025.12.01', 'cv_source_ids': ['CV-001']}]})
        report = score(payload(rule), cv={'CV-001': '2023.01.31—2025.12.01 全职'})
        self.assertEqual(report['items'][0]['leaves'][0]['calculation']['actual'], 34)
        self.assertEqual(report['items'][0]['verdict'], 'unsupported')


if __name__ == "__main__":
    unittest.main()

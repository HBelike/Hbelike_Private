"""职位搜索路由的正反例、上下文和语义回调边界。"""

from __future__ import annotations

import json
import unittest
from unittest.mock import Mock

from src.career_assistant.job_search_intent import detect_job_search_intent, needs_job_search_semantics


def card(status="editing", keyword="Java 开发", city="杭州"):
    return {"type": "job_search", "status": status, "conditions": {"keyword": keyword, "city": city}}


def model_result(**changes):
    result = {"action": "open_job_search", "keyword": None, "city": None,
              "evidence": "有什么机会", "mixed_task": False, "auto_search": False}
    result.update(changes)
    return json.dumps(result, ensure_ascii=False)


class JobSearchIntentTests(unittest.TestCase):
    def test_open_without_inventing_missing_conditions(self):
        for text in ("我想找工作", "我想换工作", "准备求职", "打开找工作表单"):
            with self.subTest(text=text):
                result = detect_job_search_intent(text)
                self.assertEqual(result.action, "open_job_search")
                self.assertIsNone(result.keyword)
                self.assertIsNone(result.city)
                self.assertFalse(result.auto_search)

    def test_clear_search_extracts_conditions_and_skips_model(self):
        classify = Mock(side_effect=AssertionError("明确表达不应请求模型"))
        result = detect_job_search_intent("帮我找杭州的 Java 开发岗位", classify=classify)
        self.assertEqual((result.action, result.keyword, result.city), ("open_job_search", "Java 开发", "杭州"))
        self.assertTrue(result.auto_search)
        classify.assert_not_called()

    def test_explicit_search_waits_for_client_location(self):
        result = detect_job_search_intent("帮我找 Java 工作")
        self.assertEqual(result.keyword, "Java")
        self.assertIsNone(result.city)
        self.assertTrue(result.auto_search)

    def test_non_search_tasks_do_not_trigger(self):
        for text in ("帮我改一下求职简历", "分析一下这个岗位适不适合我", "现在找工作是不是很难？",
                     "工作太累了，不想干了", "准备一下 Java 面试", "我想换工作但不知道适合什么方向",
                     "不知道自己适合什么工作", "帮我找对象", "我适合什么工作？"):
            with self.subTest(text=text):
                self.assertEqual(detect_job_search_intent(text).action, "normal_chat")

    def test_emotion_and_third_party_are_not_commands(self):
        for text in ("我在找工作方面有点焦虑", "我正在找工作的朋友说工作很难找", "分享一些找工作经验"):
            with self.subTest(text=text):
                self.assertEqual(detect_job_search_intent(text).action, "normal_chat")

    def test_mixed_request_and_explicit_task_order(self):
        result = detect_job_search_intent("帮我找杭州 Java 岗位，顺便看看我的简历")
        self.assertEqual(result.action, "open_job_search")
        self.assertTrue(result.mixed_task)
        result = detect_job_search_intent("我想找工作，先帮我修改简历")
        self.assertEqual(result.action, "normal_chat")

    def test_search_first_is_not_overridden_by_later_resume_task(self):
        for text in ("先找工作再改简历", "先帮我找工作，再改简历", "先帮我找杭州Java岗位，然后优化简历"):
            with self.subTest(text=text):
                result = detect_job_search_intent(text)
                self.assertEqual(result.action, "open_job_search")
                self.assertTrue(result.mixed_task)
        self.assertEqual(detect_job_search_intent("我想找工作，先帮我改简历，再找岗位").action, "normal_chat")

    def test_job_search_consultation_does_not_open_search_form(self):
        for text in ("帮我分析找工作难的原因", "找工作有什么技巧", "找工作要注意什么", "求职需要注意什么", "讲讲找工作的方法"):
            with self.subTest(text=text):
                classify = Mock(side_effect=AssertionError("明确咨询不必请求模型"))
                self.assertEqual(detect_job_search_intent(text, classify=classify).action, "normal_chat")
                classify.assert_not_called()
        self.assertEqual(detect_job_search_intent("帮我找工作，顺便分析简历").action, "open_job_search")

    def test_cancel_and_negated_city_are_different(self):
        for text in ("先不找工作了", "取消找工作", "收起表单", "不要再推荐岗位了", "我不是要找工作",
                     "我不找杭州Java工作", "我不想换工作", "我想找工作但现在先不找"):
            with self.subTest(text=text):
                self.assertEqual(detect_job_search_intent(text, previous_card=card()).action, "cancel_job_search")
        result = detect_job_search_intent("不找北京的工作，只找上海Java岗位")
        self.assertEqual(result.action, "open_job_search")
        self.assertEqual(result.city, "上海")

    def test_editing_conditions_update_card_without_auto_search(self):
        result = detect_job_search_intent("城市改成上海", previous_card=card())
        self.assertEqual((result.action, result.keyword, result.city), ("update_job_search", "Java 开发", "上海"))
        self.assertFalse(result.auto_search)

    def test_search_after_results_opens_new_card_inheriting_keyword(self):
        result = detect_job_search_intent("再看看上海的", previous_card=card("searched"))
        self.assertEqual((result.action, result.keyword, result.city), ("open_job_search", "Java 开发", "上海"))
        # 延续搜索语境的“再看看”也是明确搜索请求。
        self.assertTrue(result.auto_search)

    def test_dismissed_card_cannot_silently_supply_conditions(self):
        result = detect_job_search_intent("帮我找Java工作", previous_card=card("dismissed"))
        self.assertIsNone(result.city)
        self.assertTrue(result.auto_search)

    def test_generic_intention_with_known_profile_still_does_not_search(self):
        result = detect_job_search_intent("我想找工作", previous_card=card("selected", city=None))
        self.assertEqual(result.keyword, "Java 开发")
        self.assertFalse(result.auto_search)

    def test_semantic_explicit_search_can_wait_for_location(self):
        text = "帮我找咖啡店店长岗位"
        result = detect_job_search_intent(text, classify=lambda _: model_result(keyword="咖啡店店长", city=None, evidence=text, auto_search=True))
        self.assertEqual(result.keyword, "咖啡店店长")
        self.assertIsNone(result.city)
        self.assertTrue(result.auto_search)

    def test_semantic_condition_edit_cannot_enable_search(self):
        text = "城市改成义乌"
        result = detect_job_search_intent(text, previous_card=card(), classify=lambda _: model_result(
            action="update_job_search", keyword="Java 开发", city="义乌", evidence=text, auto_search=True))
        self.assertEqual(result.action, "update_job_search")
        self.assertEqual(result.city, "义乌")
        self.assertFalse(result.auto_search)

    def test_multiple_cities_require_clarification(self):
        result = detect_job_search_intent("帮我找杭州和上海的Java工作")
        self.assertEqual(result.action, "clarify")
        self.assertFalse(result.auto_search)

    def test_quoted_material_cannot_trigger(self):
        for text in ("他说“我想找工作”，你怎么看？", "> 帮我找杭州Java岗位", "```text\n我想找工作\n```",
                     "<resume>我想找工作</resume>"):
            with self.subTest(text=text):
                self.assertEqual(detect_job_search_intent(text).action, "normal_chat")

    def test_ordinary_chat_does_not_call_classifier(self):
        classify = Mock()
        self.assertEqual(detect_job_search_intent("你好", classify=classify).action, "normal_chat")
        classify.assert_not_called()

    def test_ambiguous_expression_can_use_structured_classifier(self):
        classify = Mock(return_value=model_result())
        result = detect_job_search_intent("有什么机会", classify=classify)
        self.assertEqual(result.action, "open_job_search")
        classify.assert_called_once()
        self.assertIn("找工作是不是很难", classify.call_args.args[0])

    def test_classifier_failure_and_hallucinations_fall_back(self):
        invalid_results = ["不是 JSON", "{}", model_result(city="北京"),
                           model_result(evidence="不是用户说的"), model_result(mixed_task="false"),
                           model_result(action="execute_shell")]
        for result in invalid_results:
            with self.subTest(result=result):
                self.assertEqual(detect_job_search_intent("有什么机会", classify=lambda _: result).action, "normal_chat")
        self.assertEqual(detect_job_search_intent("有什么机会", classify=Mock(side_effect=TimeoutError())).action, "normal_chat")

    def test_history_cannot_by_itself_trigger(self):
        result = detect_job_search_intent("谢谢", history=[{"role": "user", "content": "帮我找杭州Java岗位"}])
        self.assertEqual(result.action, "normal_chat")

    def test_model_cannot_use_assistant_guessed_city_as_user_condition(self):
        result = detect_job_search_intent("有什么机会", history=[{"role": "assistant", "content": "北京Java岗位"}],
                                          classify=lambda _: model_result(city="北京"))
        self.assertEqual(result.action, "normal_chat")

    def test_unknown_role_still_uses_semantic_extraction_for_known_action(self):
        text = "帮我找杭州咖啡店店长岗位"
        fallback = detect_job_search_intent(text)
        self.assertEqual(fallback.action, "open_job_search")
        self.assertTrue(needs_job_search_semantics(fallback, text))
        classify = Mock(return_value=model_result(keyword="咖啡店店长", city="杭州", evidence=text, auto_search=True))
        result = detect_job_search_intent(text, classify=classify)
        self.assertEqual((result.keyword, result.city, result.auto_search), ("咖啡店店长", "杭州", True))
        classify.assert_called_once()

    def test_unknown_condition_failure_retains_usable_form(self):
        text = "帮我找杭州咖啡店店长岗位"
        result = detect_job_search_intent(text, classify=Mock(side_effect=TimeoutError()))
        self.assertEqual(result.action, "open_job_search")
        self.assertEqual(result.city, "杭州")
        self.assertIsNone(result.keyword)
        self.assertFalse(result.auto_search)

    def test_generic_request_does_not_require_semantics(self):
        for text in ("我想找工作", "帮我找工作", "帮我找杭州的工作", "帮我找一份杭州的工作"):
            with self.subTest(text=text):
                intent = detect_job_search_intent(text)
                self.assertFalse(needs_job_search_semantics(intent, text))

    def test_city_unknown_to_rules_can_be_extracted_semantically(self):
        text = "帮我找义乌Java工作"
        self.assertTrue(needs_job_search_semantics(detect_job_search_intent(text), text))
        result = detect_job_search_intent(text, classify=lambda _: model_result(keyword="Java", city="义乌", evidence=text, auto_search=True))
        self.assertEqual((result.keyword, result.city), ("Java", "义乌"))

    def test_semantic_extraction_cannot_remove_known_action_or_override_current_city(self):
        text = "帮我找杭州咖啡店店长岗位"
        result = detect_job_search_intent(text, classify=lambda _: model_result(action="normal_chat", evidence=text))
        self.assertEqual(result.action, "open_job_search")
        result = detect_job_search_intent(text, previous_card=card("searched", city="上海"),
                                         classify=lambda _: model_result(city="上海", keyword="咖啡店店长", evidence=text))
        self.assertEqual((result.action, result.city), ("open_job_search", "杭州"))


if __name__ == "__main__":
    unittest.main()

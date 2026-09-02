from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from src.career_assistant.contracts import ModelCapability
from src.career_assistant.cloud_vision import CloudVisionResult
from src.career_assistant.model_clients import ModelInvocationError
from src.career_assistant.online_assessment.contracts import (
    AssessmentExecutionReport,
    AssessmentProblem,
    AssessmentSolution,
    AssessmentTestCase,
    CompileStatus,
    CapturedProblemInput,
    ExecutionFinalStatus,
    InterfaceKind,
    ProblemKind,
    ProgrammingLanguage,
    TestCaseKind as AssessmentTestKind,
    TestStatus as AssessmentTestStatus,
)
from src.career_assistant.online_assessment.model_output import (
    OnlineAssessmentModelOutputError,
    parse_json_object,
)
from src.career_assistant.online_assessment.solution_service import (
    ModelCallTarget,
    OnlineAssessmentService,
)


@dataclass
class FakeProfile:
    profile_key: str = "answer"
    capabilities: frozenset[ModelCapability] = frozenset({ModelCapability.TEXT})


class FakeResolver:
    def resolve(self, profile_key: str) -> ModelCallTarget:
        return ModelCallTarget(FakeProfile(profile_key), None, "secret")


class FakeModelClient:
    def __init__(self, outputs: list[object]) -> None:
        self.outputs = list(outputs)
        self.calls: list[str] = []
        self.messages: list[object] = []

    def complete_json(self, profile, credential_env_name, messages, **kwargs) -> str:
        del profile, credential_env_name
        self.messages.append(messages)
        self.calls.append(str(kwargs.get("operation", "")))
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)


class FakeExecutionProvider:
    def __init__(self, statuses: list[ExecutionFinalStatus]) -> None:
        self.statuses = list(statuses)
        self.calls = 0

    def execute(self, solution, tests, *, problem=None) -> AssessmentExecutionReport:
        del problem
        del solution
        self.calls += 1
        status = self.statuses.pop(0)
        passed = status is ExecutionFinalStatus.PASSED
        return AssessmentExecutionReport(
            compile_status=CompileStatus.PASSED,
            tests=[
                {
                    "test_id": tests[0].test_id,
                    "kind": tests[0].kind,
                    "status": AssessmentTestStatus.PASSED if passed else AssessmentTestStatus.FAILED,
                    "actual_output": tests[0].expected_output if passed else "wrong",
                    "error_summary": "" if passed else "实际输出与期望输出不一致",
                }
            ],
            passed_count=1 if passed else 0,
            failed_count=0 if passed else 1,
            duration_ms=1,
            final_status=status,
        )


def problem(*, confidence: float = 0.9) -> AssessmentProblem:
    return AssessmentProblem(
        source_platform="generic",
        title="两数之和",
        statement="读取两个整数，输出它们的和。输入格式为一行两个整数，输出格式为一个整数。",
        language=ProgrammingLanguage.PYTHON,
        interface_kind=InterfaceKind.STDIN_STDOUT,
        confidence=confidence,
        incomplete_reasons=[] if confidence >= 0.65 else ["题面疑似截断"],
        examples=[AssessmentTestCase(input_payload="1 2", expected_output="3")],
    )


def solution_payload(code: str = "a,b=map(int,input().split());print(a+b)") -> dict[str, object]:
    return {
        "approach_markdown": "直接相加。",
        "code": code,
        "language": "python",
        "time_complexity": "O(1)",
        "space_complexity": "O(1)",
        "assumptions": [],
    }


def test_parse_json_object_removes_markdown_fence() -> None:
    assert parse_json_object("```json\n{\"ok\": true}\n```") == {"ok": True}


def test_low_confidence_stops_before_solution_model_call() -> None:
    client = FakeModelClient([])
    service = OnlineAssessmentService(
        resolver=FakeResolver(),
        model_client=client,
        problem_extractor_profile_key="extractor",
        answer_profile_key="answer",
        execution_provider=FakeExecutionProvider([ExecutionFinalStatus.PASSED]),
    )

    result = service.solve(problem(confidence=0.5))

    assert result.status == "needs_confirmation"
    assert result.solution is None
    assert client.calls == []


def test_solution_schema_failure_is_corrected_once() -> None:
    client = FakeModelClient([
        {**solution_payload(), "assumptions": {"invalid": True}},
        solution_payload("class Solution:\n    def searchInsert(self, nums, target):\n        return 0"),
    ])
    service = OnlineAssessmentService(
        resolver=FakeResolver(),
        model_client=client,
        problem_extractor_profile_key="extractor",
        answer_profile_key="answer",
        execution_provider=FakeExecutionProvider([ExecutionFinalStatus.PASSED]),
    )

    events = list(service.solve_events(problem()))
    result = events[-1].solution

    assert [event.type for event in events] == ["correcting", "solution"]
    assert result is not None
    assert result.code.startswith("class Solution")
    assert client.calls == [
        "online_assessment_solution",
        "online_assessment_solution_correction",
    ]


def test_solution_invalid_json_is_corrected_once() -> None:
    client = FakeModelClient(["答案如下，不是 JSON", solution_payload()])
    service = OnlineAssessmentService(
        resolver=FakeResolver(),
        model_client=client,
        problem_extractor_profile_key="extractor",
        answer_profile_key="answer",
        execution_provider=FakeExecutionProvider([ExecutionFinalStatus.PASSED]),
    )

    result = service.solve(problem())

    assert result.solution is not None
    assert len(client.calls) == 2


def test_solution_schema_failure_stops_after_one_correction() -> None:
    invalid = {**solution_payload(), "code": ""}
    client = FakeModelClient([invalid, invalid])
    service = OnlineAssessmentService(
        resolver=FakeResolver(),
        model_client=client,
        problem_extractor_profile_key="extractor",
        answer_profile_key="answer",
        execution_provider=FakeExecutionProvider([ExecutionFinalStatus.PASSED]),
    )

    with pytest.raises(OnlineAssessmentModelOutputError, match="连续两次"):
        service.solve(problem())

    assert len(client.calls) == 2


def test_solution_model_invocation_error_is_not_corrected() -> None:
    client = FakeModelClient([ModelInvocationError("额度不足")])
    service = OnlineAssessmentService(
        resolver=FakeResolver(),
        model_client=client,
        problem_extractor_profile_key="extractor",
        answer_profile_key="answer",
        execution_provider=FakeExecutionProvider([ExecutionFinalStatus.PASSED]),
    )

    with pytest.raises(ModelInvocationError, match="额度不足"):
        service.solve(problem())

    assert client.calls == ["online_assessment_solution"]


def test_solution_assumptions_accept_null_and_single_string() -> None:
    for assumptions, expected in ((None, []), ("仅使用整数", ["仅使用整数"])):
        client = FakeModelClient([{**solution_payload(), "assumptions": assumptions}])
        service = OnlineAssessmentService(
            resolver=FakeResolver(),
            model_client=client,
            problem_extractor_profile_key="extractor",
            answer_profile_key="answer",
            execution_provider=FakeExecutionProvider([ExecutionFinalStatus.PASSED]),
        )

        result = service.solve(problem())

        assert result.solution is not None
        assert result.solution.assumptions == expected
        assert len(client.calls) == 1


def test_sql_answer_is_generated_but_never_sent_to_code_executor() -> None:
    client = FakeModelClient([{
        "approach_markdown": "使用子查询排除最高薪资。",
        "answer_markdown": "返回第二高薪资；不足两档时返回 null。",
        "code": "SELECT MAX(salary) FROM Employee WHERE salary < (SELECT MAX(salary) FROM Employee)",
        "language": "sql",
        "time_complexity": "取决于数据库执行计划",
        "space_complexity": "取决于数据库执行计划",
        "assumptions": [],
    }])
    provider = FakeExecutionProvider([ExecutionFinalStatus.PASSED])
    service = OnlineAssessmentService(
        resolver=FakeResolver(),
        model_client=client,
        problem_extractor_profile_key="extractor",
        answer_profile_key="answer",
        execution_provider=provider,
    )
    sql_problem = AssessmentProblem(
        title="第二高薪资",
        statement="编写 SQL 查询 Employee 表中的第二高薪资。",
        problem_type=ProblemKind.SQL,
        language=ProgrammingLanguage.SQL,
        interface_kind=InterfaceKind.UNKNOWN,
        confidence=1,
    )

    result = service.solve(sql_problem)

    assert result.solution is not None
    assert result.solution.language is ProgrammingLanguage.SQL
    assert service.generate_tests(sql_problem) == ()
    with pytest.raises(ValueError, match="不进入代码执行器"):
        service.execute(sql_problem, result.solution, ())
    assert provider.calls == 0


def test_execute_and_repair_stops_after_two_repairs() -> None:
    client = FakeModelClient(
        [
            solution_payload("print('first')"),
            {"tests": [{"input_payload": "0 0", "expected_output": "0", "explanation": "覆盖零值"}]},
            solution_payload("print('second')"),
            solution_payload("print('third')"),
        ]
    )
    provider = FakeExecutionProvider(
        [ExecutionFinalStatus.FAILED, ExecutionFinalStatus.FAILED, ExecutionFinalStatus.FAILED]
    )
    service = OnlineAssessmentService(
        resolver=FakeResolver(),
        model_client=client,
        problem_extractor_profile_key="extractor",
        answer_profile_key="answer",
        execution_provider=provider,
        max_repair_rounds=2,
    )

    solved = service.solve(problem())
    generated_tests = service.generate_tests(problem())
    repaired = service.execute_and_repair(problem(), solved.solution, generated_tests)

    assert provider.calls == 3
    assert repaired.report.repair_rounds == 2
    assert repaired.solution.code == "print('third')"
    assert generated_tests[-1].kind is AssessmentTestKind.GENERATED


def test_analyze_sends_screenshot_only_to_vision_capable_profile() -> None:
    class VisionResolver:
        def resolve(self, profile_key: str) -> ModelCallTarget:
            return ModelCallTarget(
                FakeProfile(profile_key, frozenset({ModelCapability.TEXT, ModelCapability.VISION})),
                None,
                "secret",
            )

    client = FakeModelClient([{}])
    service = OnlineAssessmentService(
        resolver=VisionResolver(),
        model_client=client,
        problem_extractor_profile_key="extractor",
        answer_profile_key="answer",
        execution_provider=FakeExecutionProvider([ExecutionFinalStatus.PASSED]),
    )

    service.analyze(CapturedProblemInput(
        source_title="两数之和",
        visible_text="给定两个整数，返回它们的和。输入格式与输出格式均已给出。",
        screenshot_data_url="data:image/png;base64,AAAA",
    ))

    content = client.messages[0][-1].content
    assert isinstance(content, list)
    assert content[-1]["image_url"]["url"] == "data:image/png;base64,AAAA"


def test_low_confidence_capture_reuses_platform_vision_before_text_structuring() -> None:
    class VisionClient:
        def analyze_image_with_prompt(self, media_type, image_bytes, prompt):
            assert media_type == "image/png"
            assert image_bytes == b"image"
            assert "当前视口" in prompt
            return CloudVisionResult(
                analysis_text="视觉看到：Python 函数题，签名 searchInsert(nums, target)",
                provider_key="qwen",
                model_id="qwen3.6-flash",
            )

    client = FakeModelClient([{}])
    service = OnlineAssessmentService(
        resolver=FakeResolver(),
        model_client=client,
        problem_extractor_profile_key="extractor",
        answer_profile_key="answer",
        execution_provider=FakeExecutionProvider([ExecutionFinalStatus.PASSED]),
        vision_client=VisionClient(),
    )

    service.analyze(CapturedProblemInput(
        visible_text="题面",
        screenshot_data_url="data:image/png;base64,aW1hZ2U=",
    ))

    content = client.messages[0][-1].content
    assert isinstance(content, str)
    assert "低置信度视口视觉复核" in content
    assert "searchInsert" in content

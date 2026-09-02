# 线上笔试助手多平台识别与可靠作答 Implementation Plan

> **For agentic workers:** 按任务顺序执行；每个任务遵循测试先行。当前共享工作区包含用户未提交改动，不创建 worktree、不暂存、不提交，只修改本计划列出的文件。

**Goal:** 修复答案契约失败，并把题面采集升级为已知平台 Adapter、未知平台通用识别、低置信度视觉补全和题型路由的统一链路。

**Architecture:** 扩展通过声明式 Adapter Registry 与只读编辑器采集生成向后兼容的 Capture Contract V2；后端先执行确定性完整度检查，必要时调用独立视觉连接，再按题型选择答案流程。算法答案模型输出严格校验，格式错误只纠正一次。

**Tech Stack:** Chrome MV3、JavaScript、Vue 3、FastAPI、Pydantic v2、PostgreSQL、OpenAI-compatible Chat Completions、Qwen CloudVision、Piston、Node test runner、pytest。

**Spec:** `docs/superpowers/specs/2026-09-02-online-assessment-multi-platform-design.md`

## Global Constraints

- 只读取用户主动触发时的当前视口；不自动滚动、填写或提交第三方页面。
- 扩展保持 `activeTab`，不得增加 `<all_urls>`。
- Adapter 随扩展打包且为声明式纯数据，不远程执行代码。
- 旧扩展缺少 V2 字段时后端仍须接受。
- 原始截图、HTML、模型未校验回答不持久化。
- 每个答案生成任务最多一次首次调用和一次格式纠正；代码修复仍最多两轮。
- PC 端完成并验证后再考虑移动端；本计划不做平板专项适配。

---

### Task 1: 答案 Schema 归一化与单次纠正

**Files:**
- Modify: `src/career_assistant/online_assessment/solution_service.py`
- Modify: `src/career_assistant/online_assessment/model_output.py`
- Test: `tests/test_online_assessment_services.py`

**Interfaces:**
- Produces: `normalize_solution_payload(payload: dict[str, object]) -> dict[str, object]`
- Produces: `OnlineAssessmentService.solve_events(problem) -> Iterator[SolveProgressEvent]`
- Preserves: `OnlineAssessmentService.solve(problem) -> SolveResult`

- [ ] **Step 1: Write failing service tests**

Add tests whose fake model outputs are:

```python
invalid = {
    "approach_markdown": "二分查找",
    "code": "class Solution: ...",
    "language": "python",
    "time_complexity": "O(log n)",
    "space_complexity": "O(1)",
    "assumptions": {"wrong": True},
}
valid = solution_payload("class Solution: ...")
```

Assert the operation sequence is `online_assessment_solution` then `online_assessment_solution_correction`, the final result is ready, and a third model call never occurs. Add separate tests for invalid JSON, language mismatch, two invalid attempts, `ModelInvocationError` without correction, `assumptions=None`, and `assumptions="无额外假设"`.

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_online_assessment_services.py -q
```

Expected: new correction tests fail because `solve()` currently raises immediately.

- [ ] **Step 3: Implement strict normalization and validation diagnostics**

In `model_output.py`, normalize only `assumptions`:

```python
def normalize_solution_payload(payload: dict[str, object]) -> dict[str, object]:
    normalized = dict(payload)
    assumptions = normalized.get("assumptions")
    if assumptions is None:
        normalized["assumptions"] = []
    elif isinstance(assumptions, str):
        normalized["assumptions"] = [assumptions] if assumptions.strip() else []
    return normalized
```

Add a safe formatter that returns field location and Pydantic error type only; never include input values.

- [ ] **Step 4: Implement at-most-once correction**

Use the same resolved model target. Correct non-empty invalid JSON, Pydantic failures, empty code and language mismatch. The correction prompt must include the exact allowed fields and safe errors, use operation `online_assessment_solution_correction`, and include at most 32,000 characters of the previous answer. `ModelInvocationError` must escape without correction.

- [ ] **Step 5: Keep `solve()` compatible**

Implement `solve()` as a consumer of the progress iterator and return the final `SolveResult`; low-confidence problems still return `needs_confirmation` without a model call.

- [ ] **Step 6: Run focused tests**

Expected: all online assessment service tests pass and call counts are exactly asserted.

---

### Task 2: 真实 `correcting` 流事件与 PC 状态

**Files:**
- Modify: `src/career_assistant/online_assessment/web.py`
- Modify: `web-ui/src/components/OnlineAssessmentAssistantPage.vue`
- Modify: `web-ui/src/online-assessment/state.js`
- Test: `tests/test_online_assessment_web.py`
- Test: `web-ui/src/online-assessment-state.test.js`
- Test: `web-ui/src/online-assessment-view.test.js`

**Interfaces:**
- Consumes: `solve_events()` from Task 1
- Produces: NDJSON phase `correcting`

- [ ] **Step 1: Add failing event-order tests**

Assert complete sequences:

```text
generating, solution, done
generating, correcting, solution, done
generating, correcting, error
```

Every event must carry the request `run_version`.

- [ ] **Step 2: Update the route**

Iterate service progress and yield `phase=correcting` before the second model request begins. Preserve `Cache-Control: no-store` and `X-Accel-Buffering: no`.

- [ ] **Step 3: Update PC state**

Add `correcting` to `ASSESSMENT_PHASES`, `busy`, phase labels and the “生成代码” pipeline step. Handle `event.type === 'phase'`; display “正在纠正答案格式”。

- [ ] **Step 4: Run backend and WebUI focused tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_online_assessment_web.py -q
cd web-ui
npm test -- --test-name-pattern="online assessment|纠正"
```

Expected: corrected and normal flows pass without stale `run_version` updates.

---

### Task 3: 语言权威、跨语言清理和 Harness 函数入口

**Files:**
- Modify: `browser-extension/job-library/assessment-capture.js`
- Modify: `src/career_assistant/online_assessment/problem_extractor.py`
- Modify: `src/career_assistant/online_assessment/execution.py`
- Modify: `src/career_assistant/online_assessment/solution_service.py`
- Modify: `web-ui/src/components/OnlineAssessmentAssistantPage.vue`
- Test: `browser-extension/job-library/tests/assessment-capture.test.mjs`
- Test: `tests/test_online_assessment_contracts.py`
- Test: `tests/test_online_assessment_execution.py`
- Test: `web-ui/src/online-assessment-state.test.js`

**Interfaces:**
- Produces: language inference from selector → editor mode → starter code
- Produces: `_function_name()` support for `name = function(...)`

- [ ] **Step 1: Add failing language and entrypoint tests**

Cover `searchInsert = function(nums, target)`, `def searchInsert(...)`, Java method, C++ method, `Node.js`, `Python3`, `GNU C++17`, and unknown language. Unknown must add `编程语言未识别` and must not receive high-confidence auto solve.

- [ ] **Step 2: Fix starter-code inference**

Add deterministic language inference. Do not default unknown input to Python. Normalize recognized aliases only.

- [ ] **Step 3: Fix Harness method name**

Ensure JavaScript assignment returns `searchInsert`, never the keyword `function`. Add language consistency checks to both `execute()` and `execute_and_repair()`.

- [ ] **Step 4: Make user language authoritative**

When the PC language control changes, clear solution, report, tests and incompatible starter code. Python/JavaScript may retain canonical function name and parameter names; Java/C++ without types set a warning requiring platform language switch and re-recognition.

- [ ] **Step 5: Run focused tests**

Expected: Python and JavaScript Harness tests execute public cases; mismatch is rejected before Piston.

---

### Task 4: 声明式 Adapter Registry 与当前视口通用采集

**Files:**
- Create: `browser-extension/job-library/assessment-adapters.js`
- Modify: `browser-extension/job-library/assessment-capture.js`
- Modify: `browser-extension/job-library/service-worker.js`
- Modify: `scripts/package_boss_extension.py`
- Test: `browser-extension/job-library/tests/assessment-adapters.test.mjs`
- Test: `browser-extension/job-library/tests/assessment-capture.test.mjs`

**Interfaces:**
- Produces: `ASSESSMENT_PLATFORM_ADAPTERS: readonly AdapterDefinition[]`
- Consumes: JSON-serializable Adapter definitions in `extractAssessmentFromPage(adapterDefinitions)`

- [ ] **Step 1: Add registry tests**

Each Adapter must have a safe slug, host suffixes, version and selector groups. Test root domains, subdomains and attacks such as `leetcode.com.evil.example`.

- [ ] **Step 2: Add viewport-boundary tests**

Build DOM fixtures where a large container contains offscreen text. Assert hidden/offscreen nodes, navigation, form controls and whole-page `main.innerText` do not enter the primary candidate.

- [ ] **Step 3: Implement pure-data registry**

Create LeetCode, HackerRank and NowCoder definitions. Do not add action callbacks or RegExp objects; regex rules are bounded strings interpreted by the common runtime.

- [ ] **Step 4: Pass registry through Chrome serialization**

Import the registry in Service Worker and pass it through `executeScript.args`. Keep all DOM helper functions inside the serialized capture function.

- [ ] **Step 5: Preserve package integrity**

Add `assessment-adapters.js` to the package script. Run serialization-isolation and full extension tests.

---

### Task 5: Capture Contract V2 and deterministic completeness gate

**Files:**
- Modify: `browser-extension/job-library/assessment-capture.js`
- Modify: `web-ui/src/online-assessment/bridge.js`
- Modify: `web-ui/src/online-assessment/state.js`
- Modify: `src/career_assistant/online_assessment/contracts.py`
- Modify: `src/career_assistant/online_assessment/problem_extractor.py`
- Test: `browser-extension/job-library/tests/assessment-capture.test.mjs`
- Test: `web-ui/src/online-assessment-bridge.test.js`
- Test: `tests/test_online_assessment_contracts.py`

**Interfaces:**
- Adds optional capture fields: `problem_type_hint`, `candidate_languages`, `field_evidence`, `capture_warnings`, `adapter_version`, `primary_problem_candidate`

- [ ] **Step 1: Add old-client compatibility test**

Validate the current V1 payload without any new fields still succeeds.

- [ ] **Step 2: Add V2 round-trip test**

Assert camelCase extension values survive bridge conversion and Pydantic validation without being dropped.

- [ ] **Step 3: Implement additive fields with defaults**

Bound list sizes and string lengths. Validate platform slug with `^[a-z0-9][a-z0-9_-]{0,79}$`; unknown unsafe values become `generic`.

- [ ] **Step 4: Replace longest-text and model-confidence authority**

Use `primary_problem_candidate` or Adapter-first ordering; generic legacy captures may still choose the longest bounded candidate. Hard warnings such as language unknown, signature missing or truncated statement force `needs_confirmation` regardless of model confidence.

- [ ] **Step 5: Bind source origin**

Store `sourceOrigin` with the capture binding. Same-origin recapture is allowed; cross-origin recapture returns a stable error requiring a new extension click.

---

### Task 6: Low-confidence visual review

**Files:**
- Modify: `src/career_assistant/cloud_vision.py`
- Modify: `src/career_assistant/online_assessment/solution_service.py`
- Modify: `src/career_assistant/online_assessment/web.py`
- Modify: `src/career_assistant/online_assessment/contracts.py`
- Modify: `web-ui/src/components/OnlineAssessmentAssistantPage.vue`
- Test: `tests/test_online_assessment_services.py`
- Test: `tests/test_online_assessment_web.py`

**Interfaces:**
- Produces: a CloudVision structured review method that accepts screenshot bytes, DOM problem and missing field names

- [ ] **Step 1: Add trigger and degradation tests**

Assert visual is not called for complete high-confidence DOM; it is called for hard warnings, Canvas/iframe or confidence below 0.65. Missing key, timeout and invalid screenshot preserve DOM and append a warning instead of failing `/analyze`.

- [ ] **Step 2: Validate screenshot input**

Accept only `data:image/png;base64,` or `data:image/jpeg;base64,`, valid Base64 and configured byte limits. Do not persist decoded bytes.

- [ ] **Step 3: Add assessment-specific CloudVision call**

Reuse Qwen `qwen3.6-flash` routing and retry policy, but send a dedicated JSON-only Prompt that may fill only requested missing fields. Do not reuse the document Markdown prompt.

- [ ] **Step 4: Merge conservatively**

Visual values may fill missing fields but cannot overwrite high-confidence DOM evidence. Final confidence is the lower of deterministic completeness and reviewed confidence.

- [ ] **Step 5: Add PC disclosure**

Before or during visual review, show that the current viewport is being sent to the configured visual service; retain the manual-confirmation fallback.

---

### Task 7: Problem-type routing without false execution claims

**Files:**
- Modify: `src/career_assistant/online_assessment/contracts.py`
- Create: `src/career_assistant/online_assessment/answer_router.py`
- Modify: `src/career_assistant/online_assessment/web.py`
- Modify: `web-ui/src/components/OnlineAssessmentAssistantPage.vue`
- Test: `tests/test_online_assessment_services.py`
- Test: `tests/test_online_assessment_web.py`
- Test: `web-ui/src/online-assessment-view.test.js`

**Interfaces:**
- Produces: `ProblemType` enum and type-specific answer contracts
- Preserves: algorithm `AssessmentSolution` and Piston flow

- [ ] **Step 1: Add routing tests**

Algorithm routes to solution/test; SQL returns query, dialect assumptions and `executed=false`; multiple choice returns selected option and evidence; short answer returns structured Markdown; unknown requires confirmation.

- [ ] **Step 2: Implement answer router**

Each route uses a strict JSON Schema and the same one-correction boundary. Only algorithm solutions can call Piston.

- [ ] **Step 3: Render one task-specific workspace**

Use mutually exclusive PC branches. Hide code execution controls for SQL, multiple choice and short answer; show “未本地执行” for SQL.

- [ ] **Step 4: Preserve archive semantics**

Store only the confirmed type-specific final answer. Do not synthesize algorithm complexity or test summaries for non-algorithm questions.

---

### Task 8: Fixtures, packaging, documentation and end-to-end verification

**Files:**
- Create: `browser-extension/job-library/tests/fixtures/leetcode-search-insert.html`
- Create: `browser-extension/job-library/tests/fixtures/hackerrank-sample.html`
- Create: `browser-extension/job-library/tests/fixtures/nowcoder-sample.html`
- Modify: `browser-extension/job-library/manifest.json`
- Modify: `browser-extension/job-library/README.md`
- Modify: `docs/modules/online-assessment-assistant.md`
- Modify: `web-ui/src/boss-extension-onboarding.js`
- Modify: `web-ui/public/boss-extension-guide.html`

**Interfaces:**
- Produces: a versioned ZIP whose manifest, WebUI download URL and actual files match

- [ ] **Step 1: Add sanitized fixtures**

Fixtures contain only public/synthetic题面结构 and no account data. Validate Adapter extraction, generic fallback and selector priority.

- [ ] **Step 2: Run full automated validation**

```powershell
cd browser-extension/job-library
npm test
node --check service-worker.js
node --check content-script.js

cd ../../web-ui
npm test
npm run build

cd ..
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: extension, WebUI and backend suites have zero failures; Vite emits only the existing chunk-size warning.

- [ ] **Step 3: Execute real smoke cases**

Use “搜索插入位置” once in Python and once in JavaScript. Each run must generate a valid answer, execute public cases and leave no Chrome extension error. Verify HackerRank/NowCoder public pages when accessible; otherwise do not label them deep adapters beyond Fixture status.

- [ ] **Step 4: Package and verify**

Increment the extension version, run `scripts/package_boss_extension.py`, then open the ZIP and assert manifest version, file list and local script hashes match.

- [ ] **Step 5: Update module documentation**

Record design goal, Adapter/visual/model call chain, privacy boundary, exact validation counts, tested platforms, unsupported types and remaining Fixture requirements.

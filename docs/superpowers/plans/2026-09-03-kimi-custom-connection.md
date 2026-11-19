# Kimi 自定义模型连接修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让“自定义兼容服务商”能够识别 Moonshot 官方端点和常见 Kimi 展示名，并为 Kimi K3 发送兼容的 Chat Completions 请求，同时提供不易误填的 Kimi 预设入口。

**Architecture:** 在模型客户端集中识别官方 Moonshot API 主机、规范化受支持的 Kimi Model ID，并按 Kimi K3 的参数约束构造请求；Web 路由复用同一规范化结果进行测试和保存，避免“测试通过但保存后不可用”。前端只负责提供正确预设和即时提示，后端仍是最终兼容边界。

**Tech Stack:** Python 3.12、FastAPI/Pydantic、httpx、Vue 3、Node.js test runner、pytest

**Spec:** `docs/career_model_connections.md`

**Status:** 已于 2026-09-03 完成本地实现与验证：后端 `633 passed`，前端 `237 passed`，PC 端 Vite 生产构建成功，`git diff --check` 无空白错误。未连接生产环境，也未使用截图中暴露的 API Key。

## Global Constraints

- 只修改本地代码、测试和文档，不连接生产环境、不部署。
- 保留当前工作区已有未提交改动，不重置、不覆盖无关文件。
- Kimi API Key 只用于用户自行测试；回归测试使用 MockTransport，不读取或调用截图中已暴露的 Key。
- 仅识别 `api.moonshot.cn`、`api.moonshot.ai` 官方 API 主机，不根据任意第三方域名猜测 Provider。
- 仅规范化官方当前模型的已知展示写法，不改写未知或第三方模型 ID。
- Kimi K3 固定参数由服务端兼容层处理，其他 OpenAI-compatible Provider 的现有请求体保持不变。
- 本次不新增数据库迁移，不持久化或展示模型内部推理内容。
- 共享脏工作区不创建 Git commit，完成后仅报告精确改动与验证结果。

---

### Task 1: Kimi 目标识别与 Model ID 规范化

**Files:**
- Modify: `src/career_assistant/model_clients.py`
- Test: `tests/test_career_model_client.py`

**Interfaces:**
- Consumes: `ModelConnectionTarget(provider_key, model_id, api_base_url)`
- Produces: `normalize_model_connection_target(target: ModelConnectionTarget) -> ModelConnectionTarget`

- [ ] **Step 1: Write the failing tests**

```python
def test_official_moonshot_custom_target_normalizes_kimi_k3_alias() -> None:
    target = normalize_model_connection_target(
        ModelConnectionTarget("custom", "kimi K3", "https://api.moonshot.cn/v1")
    )
    assert target.provider_key == "kimi"
    assert target.model_id == "kimi-k3"


def test_unrelated_custom_target_keeps_model_id_unchanged() -> None:
    target = normalize_model_connection_target(
        ModelConnectionTarget("custom", "Vendor Model", "https://example.test/v1")
    )
    assert target == ModelConnectionTarget("custom", "Vendor Model", "https://example.test/v1")
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `python -m pytest tests/test_career_model_client.py -q`

Expected: FAIL because `normalize_model_connection_target` does not exist.

- [ ] **Step 3: Implement official-host recognition and conservative alias mapping**

```python
KIMI_API_HOSTS = frozenset({"api.moonshot.cn", "api.moonshot.ai"})
KIMI_MODEL_IDS = frozenset({
    "kimi-k3",
    "kimi-k2.7-code",
    "kimi-k2.7-code-highspeed",
    "kimi-k2.6",
})


def normalize_model_connection_target(target: ModelConnectionTarget) -> ModelConnectionTarget:
    configured_url = (target.api_base_url or "").strip().rstrip("/")
    host = urlparse(configured_url).hostname or ""
    is_kimi = target.provider_key.strip().casefold() == "kimi" or host.casefold() in KIMI_API_HOSTS
    if not is_kimi:
        return target
    alias = re.sub(r"[\s_]+", "-", target.model_id.strip().casefold())
    return ModelConnectionTarget(
        provider_key="kimi",
        model_id=alias if alias in KIMI_MODEL_IDS else target.model_id.strip(),
        api_base_url=configured_url or None,
    )
```

- [ ] **Step 4: Run focused tests and verify pass**

Run: `python -m pytest tests/test_career_model_client.py -q`

Expected: PASS.

### Task 2: Kimi K3 请求参数兼容

**Files:**
- Modify: `src/career_assistant/model_clients.py`
- Test: `tests/test_career_model_client.py`

**Interfaces:**
- Consumes: normalized `ModelConnectionTarget`
- Produces: `_build_request_payload(...)` with Kimi K3-compatible fields

- [ ] **Step 1: Write the failing payload test**

```python
def test_kimi_k3_uses_completion_tokens_and_fixed_sampling_contract() -> None:
    payload = OpenAICompatibleChatClient._build_request_payload(
        ModelConnectionTarget("custom", "kimi K3", "https://api.moonshot.cn/v1"),
        [ChatMessage("user", "你好")],
        max_tokens=512,
    )
    assert payload["model"] == "kimi-k3"
    assert payload["max_completion_tokens"] == 512
    assert payload["reasoning_effort"] == "low"
    assert "max_tokens" not in payload
    assert "temperature" not in payload
```

- [ ] **Step 2: Run the focused test and verify failure**

Run: `python -m pytest tests/test_career_model_client.py::test_kimi_k3_uses_completion_tokens_and_fixed_sampling_contract -q`

Expected: FAIL because the generic payload contains `temperature` and `max_tokens`.

- [ ] **Step 3: Add the Kimi K3 branch without changing generic providers**

```python
target = normalize_model_connection_target(target)
payload = {"model": target.model_id, "messages": serialized_messages}
if target.provider_key == "kimi" and target.model_id == "kimi-k3":
    payload["max_completion_tokens"] = request_options.max_tokens or max_tokens
    payload["reasoning_effort"] = "max" if request_options.thinking else "low"
else:
    payload["temperature"] = float(request_options.temperature)
    payload["max_tokens"] = request_options.max_tokens or max_tokens
```

- [ ] **Step 4: Verify Kimi and generic payload tests**

Run: `python -m pytest tests/test_career_model_client.py tests/test_career_greeting_model_options.py -q`

Expected: PASS; existing OpenAI/DeepSeek assertions remain unchanged.

### Task 3: 测试与保存使用同一规范化配置

**Files:**
- Modify: `src/career_assistant/web/router.py`
- Test: `tests/test_career_model_connection_normalization.py`

**Interfaces:**
- Consumes: `ModelConnectionRequest`
- Produces: `_normalized_model_connection_target(request_body) -> ModelConnectionTarget`

- [ ] **Step 1: Write failing route-helper tests**

```python
def test_kimi_custom_request_is_normalized_before_verification() -> None:
    request = ModelConnectionRequest(
        display_name="Kimi K3",
        provider_key="custom",
        model_id="kimi K3",
        api_key="test-key",
        capabilities={ModelCapability.TEXT},
        cost_tier=ModelCostTier.PAID,
        api_base_url="https://api.moonshot.cn/v1",
    )
    target = _normalized_model_connection_target(request)
    assert target.provider_key == "kimi"
    assert target.model_id == "kimi-k3"
```

- [ ] **Step 2: Run the focused route test and verify failure**

Run: `python -m pytest tests/test_career_model_connection_normalization.py -q`

Expected: FAIL because the route helper does not exist.

- [ ] **Step 3: Reuse normalized target in test and save paths**

```python
target = _normalized_model_connection_target(request_body)
_verify_model_connection(services, target, request_body.api_key)
profile = services.model_profile_repository.upsert_profile(
    actor.organization_id,
    ModelProfileDraft(
        provider_key=target.provider_key,
        model_id=target.model_id,
        api_base_url=target.api_base_url,
        # other request fields remain unchanged
    ),
    api_key=request_body.api_key,
)
```

- [ ] **Step 4: Verify route normalization and model-client tests**

Run: `python -m pytest tests/test_career_model_connection_normalization.py tests/test_career_model_client.py -q`

Expected: PASS.

### Task 4: 前端 Kimi 预设与自定义配置纠错

**Files:**
- Modify: `web-ui/src/components/CareerAssistantPage.vue`
- Create: `web-ui/src/career-kimi-provider.test.js`

**Interfaces:**
- Consumes: existing `providerOptions` and `modelConnectionPayload()`
- Produces: Kimi provider option `{ key: 'kimi', defaultModelId: 'kimi-k3', apiBaseUrl: 'https://api.moonshot.cn/v1' }`

- [ ] **Step 1: Write failing source-contract tests**

```javascript
test('Kimi 预设使用官方中国区地址和精确模型 ID', () => {
  assert.match(source, /key: 'kimi'/)
  assert.match(source, /defaultModelId: 'kimi-k3'/)
  assert.match(source, /apiBaseUrl: 'https:\/\/api\.moonshot\.cn\/v1'/)
  assert.match(source, /websiteUrl: 'https:\/\/platform\.kimi\.com'/)
})

test('自定义 Moonshot 配置会提示 Kimi Model ID 格式', () => {
  assert.match(source, /Kimi 模型 ID 应为 kimi-k3/)
})
```

- [ ] **Step 2: Run the frontend test and verify failure**

Run: `node --test web-ui/src/career-kimi-provider.test.js`

Expected: FAIL because the preset and hint do not exist.

- [ ] **Step 3: Add the preset and local validation hint**

```javascript
{
  key: 'kimi',
  label: 'Kimi（Moonshot AI）',
  short: 'KM',
  detail: 'Kimi 多模态与推理模型',
  websiteUrl: 'https://platform.kimi.com',
  apiBaseUrl: 'https://api.moonshot.cn/v1',
  defaultModelId: 'kimi-k3',
  modelHint: '例如：kimi-k3',
  vision: true,
  tools: true
}
```

For a custom Moonshot URL and a loose alias such as `kimi K3`, return `Kimi 模型 ID 应为 kimi-k3；系统将在测试和保存时自动规范化。` before the network request or normalize it in the payload and show a non-blocking note.

- [ ] **Step 4: Verify the focused frontend test**

Run: `node --test web-ui/src/career-kimi-provider.test.js`

Expected: PASS.

### Task 5: 费用、容量和文档边界

**Files:**
- Modify: `src/career_assistant/model_clients.py`
- Modify: `src/career_assistant/persistence/model_profile_repository.py`
- Modify: `config/career_assistant.yaml`
- Modify: `tests/test_model_context_policy.py`
- Modify: `tests/test_model_profile_cost_tier.py`
- Modify: `tests/test_career_deepseek_settings.py`
- Modify: `docs/career_model_connections.md`

**Interfaces:**
- Consumes: normalized provider key `kimi`
- Produces: Kimi default URL, `MOONSHOT_API_KEY` fallback, paid-only classification, 1M K3 context policy

- [ ] **Step 1: Add failing policy and settings assertions**

```python
def test_kimi_k3_context_policy_uses_official_window() -> None:
    policy = infer_model_context_policy("kimi", "kimi-k3")
    assert policy.context_window_tokens == 1_000_000
    assert policy.context_window_source == "built_in"


def test_kimi_is_always_normalized_to_paid() -> None:
    assert normalize_model_cost_tier("kimi", ModelCostTier.FREE_QUOTA) is ModelCostTier.PAID
```

Also assert `settings.provider_credential_env["kimi"] == "MOONSHOT_API_KEY"`.

- [ ] **Step 2: Run focused policy tests and verify failure**

Run: `python -m pytest tests/test_model_context_policy.py tests/test_model_profile_cost_tier.py tests/test_career_deepseek_settings.py -q`

Expected: FAIL for missing Kimi built-in policy and credential mapping.

- [ ] **Step 3: Add policy/configuration and update module documentation**

```python
_MODEL_CONTEXT_LIMITS[("kimi", "kimi-k3")] = (1_000_000, 131_072)
PAID_ONLY_PROVIDER_KEYS = frozenset({"deepseek", "kimi"})
```

Add `_DEFAULT_BASE_URLS["kimi"] = "https://api.moonshot.cn/v1"`, map `kimi: MOONSHOT_API_KEY` in YAML, and document the exact Model ID, official hosts, paid classification, K3 parameter adaptation, custom endpoint normalization, verification commands, and the boundary that provider reasoning is not persisted or displayed.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/test_model_context_policy.py tests/test_model_profile_cost_tier.py tests/test_career_deepseek_settings.py -q`

Expected: PASS.

### Task 6: 全量验证

**Files:**
- Verify: all modified files

**Interfaces:**
- Consumes: Tasks 1-5
- Produces: verified local implementation and concise handoff

- [ ] **Step 1: Run backend tests**

Run: `python -m pytest -q`

Expected: all tests PASS; if the pre-existing `src/test.py` syntax problem affects compile-only checks, report it separately without modifying unrelated code.

- [ ] **Step 2: Run WebUI tests**

Run: `npm test -- --runInBand` from `web-ui` if supported; otherwise use the repository's existing `npm test` script.

Expected: all tests PASS.

- [ ] **Step 3: Build the PC WebUI**

Run: `npm run build` from `web-ui`.

Expected: Vite production build succeeds.

- [ ] **Step 4: Review the final diff**

Run: `git diff --check` and inspect only Kimi-related hunks in shared files.

Expected: no whitespace errors and no unrelated edits introduced by this task.

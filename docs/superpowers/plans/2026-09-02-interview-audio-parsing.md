# 面经库音频解析实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在面经库增加“填写公司/岗位 → 上传面试录音 → 分离说话人并提取面试官问题 → 自动写入现有 RAG 面经库”的本地可验证功能。

**Architecture:** 新增独立的音频文件、DashScope 离线转写、问题选择和导入编排模块，不把文件模拟成现有实时 PCM 流。处理过程使用 NDJSON 保持当前请求内的五阶段进度，DashScope 通过短时随机 HTTPS 地址读取单声道规范化音频，最终只把原始话轮中已验证的问题交给现有 `InterviewLibraryService.ingest`。

**Tech Stack:** Python 3.13、FastAPI、httpx、imageio-ffmpeg、Pydantic、PostgreSQL/Alembic、Vue 3、Vite、Node test runner。

**Spec:** `docs/superpowers/specs/2026-09-02-interview-audio-parsing-design.md`

## Global Constraints

- 首版只实现 PC 端功能；手机端优化需在 PC 端验收后单独进行，不做平板专项适配。
- 页面标题、分区标题、导航名称和操作标题使用中文，不添加纯装饰性英文眉题。
- 公司名称和面试岗位由用户填写且必填，系统不得从音频推断或覆盖。
- 首版只实现 DashScope `qwen-audio-3.0-asr-flash-filetrans` Provider，不实现 OpenAI、WhisperX 或 FunASR。
- 单文件最大 512MB，最长 7200 秒；转写前统一为 16kHz 单声道 MP3。
- 只有至少提取到一个可回溯原始话轮的问题时才允许入库。
- 完整转写、候选人回答、音频、临时路径、临时 URL、声纹和上游原始响应均不得持久化或返回前端。
- 只在本地修改、测试和构建；不连接生产服务器，不部署，不重建生产容器。
- 当前工作树已有大量用户改动；每次暂存和提交只能显式列出本任务文件，禁止提交或覆盖无关改动。

## File Structure

- `src/career_assistant/interview_library/audio_files.py`：上传校验、ffmpeg 规范化、受控清理和短时来源令牌。
- `src/career_assistant/interview_library/audio_transcription.py`：稳定转写领域模型、Provider 协议和 DashScope 文件转写实现。
- `src/career_assistant/interview_library/audio_questions.py`：话轮合并、面试官判定、问题话轮选择和确定性降级。
- `src/career_assistant/interview_library/audio_import.py`：五阶段编排、Markdown 生成和现有面经服务调用。
- `src/career_assistant/interview_library/audio_web.py`：音频来源读取和 NDJSON 导入接口。
- `web-ui/src/interview-audio-import.js`：前端音频约束、表单校验和 NDJSON 读取。
- `web-ui/src/components/InterviewLibraryPage.vue`：顶部入口、弹窗、状态与成功后的树刷新。
- `web-ui/src/theme.css`：蓝色主题下音频弹窗和声波标识覆盖。

---

### Task 1: 固化音频来源类型、迁移与运行配置

**Files:**
- Create: `migrations/versions/20260902_34_interview_audio_source.py`
- Create: `tests/test_interview_audio_settings.py`
- Create: `tests/test_interview_audio_migration.py`
- Modify: `src/career_assistant/interview_library/models.py`
- Modify: `src/career_assistant/settings.py`
- Modify: `config/career_assistant.yaml`

**Interfaces:**
- Consumes: 当前 `InterviewSourceType`、`IngestionTriggerType` 和 YAML 环境变量展开器。
- Produces: `InterviewAudioImportSettings`、`load_interview_audio_import_settings()`、`InterviewSourceType.AUDIO_UPLOAD`、`IngestionTriggerType.MANUAL_AUDIO`。

- [ ] **Step 1: 写入失败的配置与枚举测试**

```python
def test_audio_settings_require_https_public_url(monkeypatch):
    monkeypatch.setenv("CAREER_AUDIO_PUBLIC_BASE_URL", "http://example.test")
    with pytest.raises(ValueError, match="HTTPS"):
        load_interview_audio_import_settings(CONFIG_PATH)


def test_audio_settings_load_exact_limits(monkeypatch):
    monkeypatch.setenv("CAREER_AUDIO_PUBLIC_BASE_URL", "https://career.example.com")
    settings = load_interview_audio_import_settings(CONFIG_PATH)
    assert settings.model_id == "qwen-audio-3.0-asr-flash-filetrans"
    assert settings.max_size_bytes == 536_870_912
    assert settings.max_duration_seconds == 7_200
    assert settings.source_ttl_seconds == 1_800
    assert InterviewSourceType.AUDIO_UPLOAD.value == "audio_upload"
    assert IngestionTriggerType.MANUAL_AUDIO.value == "manual_audio"
```

- [ ] **Step 2: 运行新测试并确认配置接口尚不存在**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_interview_audio_settings.py -q`

Expected: FAIL，提示无法导入 `InterviewAudioImportSettings` 或 `load_interview_audio_import_settings`。

- [ ] **Step 3: 实现严格配置对象与加载器**

```python
@dataclass(frozen=True)
class InterviewAudioImportSettings:
    temporary_root: Path
    public_base_url: str | None
    api_base_url: str
    model_id: str
    max_size_bytes: int
    max_duration_seconds: int
    source_ttl_seconds: int
    poll_interval_seconds: float
    request_timeout_seconds: float

    @property
    def ready(self) -> bool:
        return bool(self.public_base_url and os.getenv("DASHSCOPE_API_KEY", "").strip())
```

在 `config/career_assistant.yaml` 的 `interview_library` 下增加 `audio_import`，精确默认值为：

```yaml
audio_import:
  temporary_root: ${CAREER_TEMPORARY_ATTACHMENT_ROOT:-data/career-temporary-attachments}/interview-audio
  public_base_url: ${CAREER_AUDIO_PUBLIC_BASE_URL:-}
  api_base_url: ${DASHSCOPE_FILE_ASR_BASE_URL:-https://dashscope.aliyuncs.com/api/v1}
  model_id: ${DASHSCOPE_FILE_ASR_MODEL:-qwen-audio-3.0-asr-flash-filetrans}
  max_size_bytes: ${CAREER_AUDIO_MAX_BYTES:-536870912}
  max_duration_seconds: ${CAREER_AUDIO_MAX_DURATION_SECONDS:-7200}
  source_ttl_seconds: ${CAREER_AUDIO_SOURCE_TTL_SECONDS:-1800}
  poll_interval_seconds: 2
  request_timeout_seconds: 30
```

加载器必须拒绝非 HTTPS 的 `public_base_url`、非 HTTPS 的 `api_base_url`、非 `qwen-audio-3.0-asr-flash-filetrans` 的模型，以及越界的大小、时长和 TTL。`temporary_root` 若为相对路径，必须相对项目根目录解析，避免工作目录变化后写到不可控位置。

- [ ] **Step 4: 写入失败的迁移约束测试**

```python
def test_audio_source_migration_updates_both_checks():
    source = Path("migrations/versions/20260902_34_interview_audio_source.py").read_text("utf-8")
    assert 'down_revision = "20260901_33"' in source
    assert "'audio_upload'" in source
    assert "'manual_audio'" in source
    assert "UPDATE career_assistant.interview_experiences" in source
    assert "UPDATE career_assistant.interview_ingestion_jobs" in source
```

- [ ] **Step 5: 新增可逆迁移并运行测试**

迁移升级时重建两个 CHECK：

```sql
source_type IN ('manual_upload', 'manual_text', 'public_url',
                'authenticated_session', 'official_api', 'online_assessment',
                'audio_upload')
```

```sql
trigger_type IN ('manual_upload', 'manual_url', 'scheduled_scan', 'api_sync',
                 'manual_audio')
```

降级前把 `audio_upload` 改为 `manual_upload`，把 `manual_audio` 改为 `manual_upload`，再恢复旧约束。

Run: `\.venv\Scripts\python.exe -m pytest tests/test_interview_audio_settings.py tests/test_interview_audio_migration.py -q`

Expected: PASS。

- [ ] **Step 6: 只提交本任务文件**

```powershell
git add migrations/versions/20260902_34_interview_audio_source.py tests/test_interview_audio_settings.py tests/test_interview_audio_migration.py src/career_assistant/interview_library/models.py src/career_assistant/settings.py config/career_assistant.yaml
git commit -m "feat: define interview audio import settings"
```

---

### Task 2: 实现受控音频暂存、规范化与短时来源令牌

**Files:**
- Create: `src/career_assistant/interview_library/audio_files.py`
- Create: `tests/test_interview_audio_files.py`

**Interfaces:**
- Consumes: `InterviewAudioImportSettings`、FastAPI `UploadFile`、`imageio_ffmpeg.get_ffmpeg_exe()`。
- Produces: `StoredAudioFile`、`NormalizedAudioFile`、`RegisteredAudioSource`、`TemporaryAudioStore`、`TemporaryAudioSourceRegistry`。

- [ ] **Step 1: 写上传白名单、大小上限和清理测试**

```python
@pytest.mark.asyncio
async def test_audio_store_rejects_mime_mismatch_and_cleans_directory(tmp_path):
    store = TemporaryAudioStore(_settings(tmp_path, max_size_bytes=8))
    upload = UploadFile(filename="call.mp3", file=BytesIO(b"1234"), headers=Headers({"content-type": "text/plain"}))
    with pytest.raises(ValueError, match="音频类型不受支持"):
        await store.save(upload)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_audio_store_stops_after_streamed_size_limit(tmp_path):
    store = TemporaryAudioStore(_settings(tmp_path, max_size_bytes=3))
    upload = UploadFile(filename="call.mp3", file=BytesIO(b"1234"), headers=Headers({"content-type": "audio/mpeg"}))
    with pytest.raises(ValueError, match="音频超过允许的最大大小"):
        await store.save(upload)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("filename,media_type", [
    ("a.mp3", "audio/mpeg"), ("a.wav", "audio/wav"),
    ("a.m4a", "audio/mp4"), ("a.aac", "audio/aac"),
    ("a.ogg", "audio/ogg"), ("a.webm", "audio/webm"),
    ("a.mp4", "video/mp4"),
])
@pytest.mark.asyncio
async def test_audio_store_accepts_only_supported_extension_mime_pairs(tmp_path, filename, media_type):
    saved = await TemporaryAudioStore(_settings(tmp_path)).save(
        UploadFile(filename=filename, file=BytesIO(b"1234"), headers=Headers({"content-type": media_type}))
    )
    assert saved.original_name == filename
```

- [ ] **Step 2: 写 ffmpeg 参数、时长边界和注册器测试**

```python
def test_normalize_uses_mono_16khz_mp3(tmp_path):
    runner = Mock(return_value=CompletedProcess([], 0, "", ""))
    probe = Mock(return_value=90.5)
    normalized = TemporaryAudioStore(_settings(tmp_path), runner=runner, duration_probe=probe).normalize(_stored(tmp_path))
    command = runner.call_args.args[0]
    assert command[command.index("-ac") + 1] == "1"
    assert command[command.index("-ar") + 1] == "16000"
    assert command[command.index("-b:a") + 1] == "64k"
    assert normalized.duration_seconds == 90.5


def test_source_registry_revokes_and_expires(tmp_path):
    clock = Mock(side_effect=[100.0, 100.0, 2_000.0])
    registry = TemporaryAudioSourceRegistry(_settings(tmp_path), clock=clock, token_factory=lambda: "a" * 64)
    lease = registry.register(_normalized(tmp_path))
    assert registry.resolve(lease.token).path == lease.path
    assert registry.resolve(lease.token) is None


def test_source_registry_revoked_token_cannot_be_resolved(tmp_path):
    registry = TemporaryAudioSourceRegistry(_settings(tmp_path), token_factory=lambda: "b" * 64)
    lease = registry.register(_normalized(tmp_path))
    registry.revoke(lease.token)
    assert registry.resolve(lease.token) is None


def test_source_registry_rejects_file_outside_audio_root(tmp_path):
    registry = TemporaryAudioSourceRegistry(_settings(tmp_path))
    with pytest.raises(ValueError, match="临时音频路径无效"):
        registry.register(_normalized(tmp_path.parent))
```

- [ ] **Step 3: 运行测试确认模块尚不存在**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_interview_audio_files.py -q`

Expected: FAIL，提示无法导入 `audio_files`。

- [ ] **Step 4: 实现不可变文件与令牌契约**

```python
@dataclass(frozen=True)
class StoredAudioFile:
    original_name: str
    media_type: str
    size_bytes: int
    path: Path


@dataclass(frozen=True)
class NormalizedAudioFile:
    path: Path
    media_type: str
    duration_seconds: float


@dataclass(frozen=True)
class RegisteredAudioSource:
    token: str
    path: Path
    media_type: str
    expires_at_monotonic: float
```

`TemporaryAudioStore.save()` 每次建立 `interview-audio-*` 直接子目录并按 1MB 分块写入；`normalize()` 使用参数 `-vn -ac 1 -ar 16000 -codec:a libmp3lame -b:a 64k`；`cleanup()` 只允许删除音频根目录的直接子目录。注册器使用 `threading.Lock`，令牌来自 `secrets.token_urlsafe(32)`，`resolve()` 同时清理过期记录。

- [ ] **Step 5: 运行音频文件测试**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_interview_audio_files.py -q`

Expected: PASS。

- [ ] **Step 6: 提交音频文件边界**

```powershell
git add src/career_assistant/interview_library/audio_files.py tests/test_interview_audio_files.py
git commit -m "feat: add temporary interview audio storage"
```

---

### Task 3: 实现 DashScope 离线文件转写 Provider

**Files:**
- Create: `src/career_assistant/interview_library/audio_transcription.py`
- Create: `tests/test_interview_audio_transcription.py`

**Interfaces:**
- Consumes: `InterviewAudioImportSettings`、短时 HTTPS 音频 URL、`DASHSCOPE_API_KEY`、`DASHSCOPE_WORKSPACE_ID`。
- Produces: `TranscriptSegment`、`AudioTranscript`、`AudioTranscriptionProvider`、`DashScopeFileTranscriptionProvider.transcribe(source_url: str) -> AudioTranscript`。

- [ ] **Step 1: 写成功请求和响应映射测试**

```python
def test_dashscope_provider_requests_diarization_and_maps_segments():
    transport = SequencedTransport([
        (200, {"output": {"task_id": "task-1"}}),
        (200, {"output": {"task_status": "SUCCEEDED", "results": [{"subtask_status": "SUCCEEDED", "transcription_url": "https://dashscope.oss-cn-beijing.aliyuncs.com/result.json"}]}}),
        (200, {"transcripts": [{"sentences": [{"begin_time": 100, "end_time": 900, "text": "请介绍项目", "speaker_id": 0}]}]}),
    ])
    provider = _provider(transport)
    result = provider.transcribe("https://career.example.com/api/career/interview-library/audio-sources/token")
    submitted = json.loads(transport.requests[0].content)
    assert submitted["model"] == "qwen-audio-3.0-asr-flash-filetrans"
    assert submitted["input"]["file_urls"] == ["https://career.example.com/api/career/interview-library/audio-sources/token"]
    assert submitted["parameters"]["diarization_enabled"] is True
    assert result.segments[0].speaker_id == 0
    assert result.segments[0].text == "请介绍项目"
```

- [ ] **Step 2: 写失败、限流和不可信结果 URL 测试**

```python
@pytest.mark.parametrize("status", ["FAILED", "UNKNOWN"])
def test_dashscope_provider_rejects_terminal_failure(status):
    with pytest.raises(AudioTranscriptionError, match="语音转写暂时未完成"):
        _provider(_failed_transport(status)).transcribe(SOURCE_URL)


def test_dashscope_provider_rejects_untrusted_result_host():
    with pytest.raises(AudioTranscriptionError, match="结果地址无效"):
        _provider(_result_url_transport("https://attacker.example/result.json")).transcribe(SOURCE_URL)
```

- [ ] **Step 3: 运行测试确认 Provider 尚不存在**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_interview_audio_transcription.py -q`

Expected: FAIL，提示无法导入 `DashScopeFileTranscriptionProvider`。

- [ ] **Step 4: 实现稳定领域对象和阻塞 Provider**

```python
@dataclass(frozen=True)
class TranscriptSegment:
    index: int
    speaker_id: int
    begin_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class AudioTranscript:
    provider: str
    duration_seconds: float
    segments: tuple[TranscriptSegment, ...]


class AudioTranscriptionProvider(Protocol):
    def transcribe(self, source_url: str) -> AudioTranscript: ...
```

Provider 使用注入的 `httpx.Client`、`sleep` 和最大轮询截止时间。提交请求包含 `Authorization: Bearer`、`X-DashScope-Async: enable`，工作空间非空时增加 `X-DashScope-WorkSpace`。结果 URL 只接受 `https`，且主机名满足 `host == "aliyuncs.com" or host.endswith(".aliyuncs.com")`；不能只做包含匹配。上游没有单独返回总时长时，使用合法句子的最大 `end_ms / 1000` 推导。错误只保留固定分类消息。

提交固定调用 `POST {api_base_url}/services/audio/asr/transcription`，轮询固定调用 `GET {api_base_url}/tasks/{task_id}`；两者都不得接受浏览器传入的路径或模型。对 429、轮询超时、`FAILED` 和畸形 JSON 分别编写测试，但统一向业务层抛出不含上游正文的 `AudioTranscriptionError`。

- [ ] **Step 5: 运行 Provider 测试**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_interview_audio_transcription.py -q`

Expected: PASS。

- [ ] **Step 6: 提交离线转写 Provider**

```powershell
git add src/career_assistant/interview_library/audio_transcription.py tests/test_interview_audio_transcription.py
git commit -m "feat: add dashscope interview file transcription"
```

---

### Task 4: 实现基于原始话轮 ID 的面试官问题提取

**Files:**
- Create: `src/career_assistant/interview_library/audio_questions.py`
- Create: `tests/test_interview_audio_questions.py`

**Interfaces:**
- Consumes: `tuple[TranscriptSegment, ...]`、现有 `ModelGateway`、`OpenAICompatibleChatClient`、当前组织 ID。
- Produces: `InterviewTurn`、`AudioQuestionExtraction`、`AudioInterviewQuestionExtractor.extract(organization_id, segments) -> AudioQuestionExtraction`。

- [ ] **Step 1: 写话轮合并、模型 ID 校验和候选人反问测试**

```python
def test_extractor_selects_only_original_interviewer_turns():
    client = SequencedJsonClient([
        '{"interviewer_speaker_id": 0, "confidence": 0.93}',
        '{"question_turn_ids": [1, 3, 999]}',
    ])
    result = _extractor(client).extract(ORG_ID, _segments([
        (0, "请介绍一下你负责的项目"),
        (1, "我负责订单服务"),
        (0, "为什么使用消息队列"),
        (1, "我也想问一下团队规模是多少"),
    ]))
    assert result.interviewer_speaker_id == 0
    assert result.questions == ("请介绍一下你负责的项目", "为什么使用消息队列")
    assert 999 not in result.selected_turn_ids
```

- [ ] **Step 2: 写无模型确定性降级和无问题拒绝测试**

```python
def test_extractor_falls_back_to_question_markers_without_model():
    result = _extractor_without_model().extract(ORG_ID, _segments([
        (0, "好的，我们继续"),
        (0, "请说一下 Redis 为什么快"),
        (1, "主要因为内存访问"),
    ]))
    assert result.method == "deterministic"
    assert result.questions == ("请说一下 Redis 为什么快",)


def test_extractor_rejects_transcript_without_questions():
    with pytest.raises(ValueError, match="未识别到面试官问题"):
        _extractor_without_model().extract(ORG_ID, _segments([(0, "好的"), (1, "嗯")]))
```

- [ ] **Step 3: 运行测试确认提取器尚不存在**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_interview_audio_questions.py -q`

Expected: FAIL，提示无法导入 `AudioInterviewQuestionExtractor`。

- [ ] **Step 4: 实现话轮和提取结果契约**

```python
@dataclass(frozen=True)
class InterviewTurn:
    id: int
    speaker_id: int
    begin_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class AudioQuestionExtraction:
    interviewer_speaker_id: int
    confidence: float
    selected_turn_ids: tuple[int, ...]
    questions: tuple[str, ...]
    method: str
```

连续同说话人、间隔不超过 1200ms 且合并后不超过 600 字的话语合并。提取器通过 `ModelGateway.resolve(organization_id, ModelSelectionRequest(mode=ModelSelectionMode.FREE_QUOTA_FIRST, required_capabilities=frozenset({ModelCapability.TEXT})))` 获取模型，与现有面经分析的模型档案能力约定保持一致；只有 `ModelReadiness.READY` 且解析结果带凭据时才调用 `OpenAICompatibleChatClient.complete_json()`，其余状态进入确定性降级。文本模型第一轮只返回 `interviewer_speaker_id` 与 `confidence`，第二轮只返回 `question_turn_ids`；使用 `complete_json(..., operation="interview_audio_questions")`。所有返回 ID 必须在本地映射中存在，问题正文始终从 `InterviewTurn.text` 复制，并调用实时归档已有的 `merge_questions()` 去重。

确定性降级先按说话人分别统计“中文疑问词、请求式问法和问号”命中的话轮数与占比；只有一个说话人的命中数至少为 1，且命中占比严格高于另一说话人时才认定其为面试官。随后只从该说话人的原始话轮中选题；平分、单说话人或无法区分时抛出“未识别到面试官问题”。

- [ ] **Step 5: 运行问题提取测试**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_interview_audio_questions.py tests/test_live_interview_archive.py -q`

Expected: PASS，实时面试问题去重行为不变。

- [ ] **Step 6: 提交问题提取器**

```powershell
git add src/career_assistant/interview_library/audio_questions.py tests/test_interview_audio_questions.py
git commit -m "feat: extract interviewer questions from audio turns"
```

---

### Task 5: 编排音频导入并接入 FastAPI

**Files:**
- Create: `src/career_assistant/interview_library/audio_import.py`
- Create: `src/career_assistant/interview_library/audio_web.py`
- Create: `tests/test_interview_audio_import_service.py`
- Create: `tests/test_interview_audio_web.py`
- Modify: `src/career_assistant/web/router.py`
- Modify: `src/web/api.py`
- Modify: `tests/test_platform_actor_middleware.py`

**Interfaces:**
- Consumes: Tasks 1–4 的设置、文件、Provider、问题提取器，以及 `InterviewLibraryService.ingest()`。
- Produces: `AudioImportProgress`、`AudioImportCompleted`、`InterviewAudioImportService.stream_import(...)`、`POST /api/career/interview-library/audio-import-stream`、`GET /api/career/interview-library/audio-sources/{token}`。

- [ ] **Step 1: 写成功编排和清理测试**

```python
@pytest.mark.asyncio
async def test_audio_import_streams_five_phases_and_ingests_questions():
    service, doubles = _service()
    events = [event async for event in service.stream_import(
        organization_id=ORG_ID,
        actor_id=ACTOR_ID,
        can_manage_all=False,
        company_name="字节跳动",
        role_name="后端开发",
        interview_date=date(2026, 9, 2),
        upload=_upload(),
    )]
    assert [event.phase for event in events if isinstance(event, AudioImportProgress)] == [
        "upload", "normalize", "transcribe", "extract", "index"
    ]
    draft = doubles.library_service.ingest.call_args.args[1]
    assert draft.source_type is InterviewSourceType.AUDIO_UPLOAD
    assert "## 面试问题" in draft.markdown_content
    assert "1. 请介绍一下项目" in draft.markdown_content
    doubles.registry.revoke.assert_called_once()
    doubles.store.cleanup.assert_called_once()
```

- [ ] **Step 2: 写异常与取消路径测试**

```python
@pytest.mark.asyncio
async def test_audio_import_never_ingests_when_no_questions():
    service, doubles = _service(extractor_error=ValueError("未识别到面试官问题"))
    with pytest.raises(ValueError, match="未识别到面试官问题"):
        async for _ in service.stream_import(**_request_args()):
            pass
    doubles.library_service.ingest.assert_not_called()
    doubles.registry.revoke.assert_called_once()
    doubles.store.cleanup.assert_called_once()


@pytest.mark.asyncio
async def test_audio_import_cancellation_still_revokes_and_cleans():
    service, doubles = _service(provider_error=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        async for _ in service.stream_import(**_request_args()):
            pass
    doubles.library_service.ingest.assert_not_called()
    doubles.registry.revoke.assert_called_once()
    doubles.store.cleanup.assert_called_once()
```

- [ ] **Step 3: 实现编排事件和 Markdown 构建器**

```python
@dataclass(frozen=True)
class AudioImportProgress:
    phase: str
    percent: int
    detail: str


@dataclass(frozen=True)
class AudioImportCompleted:
    experience: InterviewExperienceRecord
    question_count: int


class InterviewAudioImportService:
    async def stream_import(
        self,
        *,
        organization_id: UUID,
        actor_id: UUID,
        can_manage_all: bool,
        company_name: str,
        role_name: str,
        interview_date: date | None,
        upload: UploadFile,
    ) -> AsyncIterator[AudioImportProgress | AudioImportCompleted]: ...
```

百分比固定为 10、25、45、75、90；`finally` 必须先撤销令牌再清理文件。`build_audio_interview_markdown()` 只写公司、岗位、日期、来源和编号问题，摘要为“从面试录音识别到 N 个面试官问题”，标签为 `("录音解析",)`，触发类型为 `MANUAL_AUDIO`。

- [ ] **Step 4: 写 API 成功、配置缺失和公开来源测试**

```python
def test_audio_import_endpoint_returns_ndjson_result(client):
    response = client.post(
        "/api/career/interview-library/audio-import-stream",
        data={"company_name": "字节跳动", "role_name": "后端开发"},
        files={"audio_file": ("interview.mp3", b"audio", "audio/mpeg")},
    )
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [item["event"] for item in events][-1] == "result"
    assert events[-1]["payload"]["source_type"] == "audio_upload"


def test_audio_source_path_is_public_but_other_career_paths_stay_protected(monkeypatch):
    monkeypatch.setenv("PLATFORM_AUTH_REQUIRED", "true")
    client = _middleware_client(session=None)
    assert client.get("/api/career/interview-library/audio-sources/unknown").status_code != 401
    assert client.get("/api/career/interview-library/tree").status_code == 401


def test_audio_import_requires_fields_and_ready_configuration(client, not_ready_service):
    missing = client.post("/api/career/interview-library/audio-import-stream", data={})
    assert missing.status_code == 422
    unavailable = client.post(
        "/api/career/interview-library/audio-import-stream",
        data={"company_name": "字节跳动", "role_name": "后端开发"},
        files={"audio_file": ("interview.mp3", b"audio", "audio/mpeg")},
    )
    assert unavailable.status_code == 503
```

- [ ] **Step 5: 实现独立 Router、服务装配和精确认证豁免**

`audio_web.py` 使用独立 `APIRouter(prefix="/api/career/interview-library")`。来源接口从 `request.app.state.interview_audio_source_registry` 解析令牌并返回 `FileResponse(..., media_type="audio/mpeg")`；未知或过期令牌返回 404。导入接口在开始流响应前调用 `service.ensure_ready()`，缺少 Key 或 HTTPS 公网地址时返回 503；流开始后的领域错误编码为：

```json
{"event":"error","message":"未识别到面试官问题，请确认录音包含双方对话。"}
```

在 `CareerAssistantServices` 增加 `interview_audio_import_service`，在 `install_career_assistant_api()` 注册 Router 和进程级 `TemporaryAudioSourceRegistry`，关闭服务时关闭 DashScope `httpx.Client`。把 `_interview_experience_payload` 改名为可复用的 `interview_experience_payload`，由音频 Router 输出与现有页面一致的结果结构。

在 `src/web/api.py` 只把 `/api/career/interview-library/audio-sources/` 加入公开前缀；导入 POST 继续要求现有平台会话。

- [ ] **Step 6: 运行服务、API 和认证回归**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_interview_audio_import_service.py tests/test_interview_audio_web.py tests/test_platform_actor_middleware.py tests/test_interview_library_permissions.py -q`

Expected: PASS。

- [ ] **Step 7: 提交后端完整链路**

```powershell
git add src/career_assistant/interview_library/audio_import.py src/career_assistant/interview_library/audio_web.py src/career_assistant/web/router.py src/web/api.py tests/test_interview_audio_import_service.py tests/test_interview_audio_web.py tests/test_platform_actor_middleware.py
git commit -m "feat: import interview questions from audio"
```

---

### Task 6: 完成 PC 端音频解析入口与进度交互

**Files:**
- Create: `web-ui/src/interview-audio-import.js`
- Create: `web-ui/src/interview-library-audio-import.test.js`
- Modify: `web-ui/src/components/InterviewLibraryPage.vue`
- Modify: `web-ui/src/theme.css`

**Interfaces:**
- Consumes: `POST /api/career/interview-library/audio-import-stream` 的 `progress/result/error` NDJSON。
- Produces: 顶部“音频解析”按钮、必填归属表单、单文件选择、五阶段进度和成功后的现有 `finishImport()` 行为。

- [ ] **Step 1: 写纯函数和页面契约测试**

```javascript
test('音频导入必须先填写公司和岗位且只接受单文件', () => {
  assert.equal(validateAudioImportDraft({ companyName: '', roleName: '后端', file: null }), '请先填写公司名称。')
  assert.equal(validateAudioImportDraft({ companyName: '字节', roleName: '', file: null }), '请先填写面试岗位。')
  assert.equal(validateAudioImportDraft({ companyName: '字节', roleName: '后端', file: null }), '请选择面试录音。')
})


test('面经库顶部在小红书入口前展示音频解析按钮', () => {
  const audioIndex = component.indexOf('>音频解析</button>')
  const xhsIndex = component.indexOf('>小红书URL读取</button>')
  assert.ok(audioIndex >= 0 && audioIndex < xhsIndex)
  assert.match(component, /@click="openAudioImport"/)
  assert.match(component, /解析并落库/)
})
```

- [ ] **Step 2: 运行前端测试确认模块与入口不存在**

Run: `npm --prefix web-ui test -- --test-name-pattern="音频"`

Expected: FAIL，提示无法导入 `interview-audio-import.js` 或找不到按钮。

- [ ] **Step 3: 实现前端纯函数与 NDJSON 读取**

```javascript
export const AUDIO_ACCEPT = '.mp3,.wav,.m4a,.aac,.ogg,.webm,.mp4'

export function createAudioImportDraft() {
  return { companyName: '', roleName: '', interviewDate: '', file: null }
}

export function validateAudioImportDraft(draft) {
  if (!draft.companyName.trim()) return '请先填写公司名称。'
  if (!draft.roleName.trim()) return '请先填写面试岗位。'
  if (!draft.file) return '请选择面试录音。'
  return ''
}

async function readApiError(response) {
  try {
    const payload = await response.json()
    return payload.detail || payload.message || '音频解析请求失败'
  } catch {
    return '音频解析请求失败'
  }
}

export async function readAudioImportStream(response, onProgress) {
  if (!response.ok) throw new Error(await readApiError(response))
  const reader = response.body?.getReader()
  if (!reader) throw new Error('音频解析服务未返回数据流')
  const decoder = new TextDecoder()
  let buffer = ''
  let result

  const acceptLine = (line) => {
    const trimmed = line.trim()
    if (!trimmed) return
    let event
    try {
      event = JSON.parse(trimmed)
    } catch {
      throw new Error('音频解析服务返回了无法识别的数据')
    }
    if (event.event === 'progress') onProgress(event)
    if (event.event === 'error') throw new Error(event.message || '音频解析失败')
    if (event.event === 'result') result = event.payload
  }

  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
    const lines = buffer.split(/\r?\n/)
    buffer = lines.pop() || ''
    lines.forEach(acceptLine)
    if (done) break
  }
  acceptLine(buffer)
  if (!result) throw new Error('音频解析服务未返回入库结果')
  return result
}
```

`readAudioImportStream` 必须处理 JSON 被拆到多个网络 chunk、尾部无换行、空行和非 JSON 行；流结束没有 `result` 时抛出“音频解析服务未返回入库结果”。

为上述读取器增加三个确定性测试：用两个 `ReadableStream` chunk 拆开同一 JSON 并确认仍返回 `result`；输入 `error` 事件并确认抛出服务端稳定消息；输入只有 `progress` 的结束流并确认抛出“音频解析服务未返回入库结果”。另用替身 `fetch` 检查 `FormData` 只包含 `company_name`、`role_name`、可选 `interview_date` 和单个 `audio_file`。

- [ ] **Step 4: 在页面增加状态、弹窗和提交行为**

新增 `showAudioImportModal`、`audioImporting`、`audioImportProgress`、`audioImportError`、`audioDraft` 和文件 input ref。`openAudioImport()` 只重置错误和进度，不清除用户上一次尚未成功的归属字段；`submitAudioImport()` 构造字段名完全一致的 `FormData`：

```javascript
const data = new FormData()
data.set('company_name', audioDraft.value.companyName.trim())
data.set('role_name', audioDraft.value.roleName.trim())
if (audioDraft.value.interviewDate) data.set('interview_date', audioDraft.value.interviewDate)
data.set('audio_file', audioDraft.value.file)
```

成功时调用 `await finishImport(payload)` 并重置草稿；失败时保留公司、岗位和文件。选择文件按钮使用 `:disabled="!audioDraft.companyName.trim() || !audioDraft.roleName.trim() || audioImporting"`。

- [ ] **Step 5: 增加克制的 PC 样式和蓝色主题覆盖**

页面样式增加 `.audio-import-dialog`、`.audio-wave-mark`、`.audio-file-picker`、`.audio-stage-list`。签名元素只使用一个由 CSS 绘制的三柱声波标记；弹窗宽度 `min(720px, 100%)`，归属字段两列，进度五阶段纵向排列。`theme.css` 使用现有 `--ui-accent-*`、`--ui-line` 和 `--ui-surface-*`，不增加新的全局色值或字体。

- [ ] **Step 6: 运行前端测试和生产构建**

Run: `npm --prefix web-ui test`

Expected: 全部 Node 测试 PASS。

Run: `npm --prefix web-ui run build`

Expected: Vite 构建成功，无 Vue 编译错误。

- [ ] **Step 7: 提交 PC 端交互**

```powershell
git add web-ui/src/interview-audio-import.js web-ui/src/interview-library-audio-import.test.js web-ui/src/components/InterviewLibraryPage.vue web-ui/src/theme.css
git commit -m "feat: add interview audio import dialog"
```

---

### Task 7: 对齐代理、临时盘、示例配置与模块文档

**Files:**
- Create: `tests/test_interview_audio_deployment.py`
- Modify: `.env.career-assistant.example`
- Modify: `.env.production.example`
- Modify: `docker-compose.production.yml`
- Modify: `docker/caddy/Caddyfile`
- Modify: `docker/nginx/default.conf`
- Modify: `docs/interview_library_rag_module.md`

**Interfaces:**
- Consumes: Task 1 配置名、Task 5 两个 API 路径和 512MB 应用层限制。
- Produces: 可复制的本地/生产配置说明、640MB API 临时盘、令牌路径日志跳过和本地 Nginx 精确大文件路由。

- [ ] **Step 1: 写部署静态契约测试**

```python
def test_audio_import_deployment_contract():
    compose = Path("docker-compose.production.yml").read_text("utf-8")
    caddy = Path("docker/caddy/Caddyfile").read_text("utf-8")
    nginx = Path("docker/nginx/default.conf").read_text("utf-8")
    prod_env = Path(".env.production.example").read_text("utf-8")
    assert "/var/lib/career-temporary-attachments:size=640m" in compose
    assert "log_skip @audio_source" in caddy
    assert "audio-sources/*" in caddy
    assert "location = /api/career/interview-library/audio-import-stream" in nginx
    assert "client_max_body_size 520m" in nginx
    assert "CAREER_AUDIO_PUBLIC_BASE_URL=" in prod_env
    assert "DASHSCOPE_FILE_ASR_MODEL=qwen-audio-3.0-asr-flash-filetrans" in prod_env
```

- [ ] **Step 2: 运行部署测试并确认配置尚未对齐**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_interview_audio_deployment.py -q`

Expected: FAIL，至少提示 640m 临时盘或音频环境变量缺失。

- [ ] **Step 3: 更新示例环境和 Compose**

两个环境示例增加：

```dotenv
CAREER_AUDIO_PUBLIC_BASE_URL=https://your-domain.example
DASHSCOPE_FILE_ASR_BASE_URL=https://dashscope.aliyuncs.com/api/v1
DASHSCOPE_FILE_ASR_MODEL=qwen-audio-3.0-asr-flash-filetrans
CAREER_AUDIO_MAX_BYTES=536870912
CAREER_AUDIO_MAX_DURATION_SECONDS=7200
CAREER_AUDIO_SOURCE_TTL_SECONDS=1800
```

只把 `career-api` 的附件 tmpfs 改为 `size=640m`；Scheduler 和 Worker 不处理音频上传，保持原有 128m。

- [ ] **Step 4: 更新 Caddy 与本地 Nginx 精确路由**

Caddy 站点块增加：

```caddyfile
@audio_source path /api/career/interview-library/audio-sources/*
log_skip @audio_source
```

Nginx 在通用 `/api/career/` 前增加精确导入 location，复用相同代理头并设置：

```nginx
location = /api/career/interview-library/audio-import-stream {
    client_max_body_size 520m;
    proxy_pass http://career-api:8012;
    proxy_http_version 1.1;
    proxy_request_buffering off;
    proxy_buffering off;
    proxy_read_timeout 60m;
    proxy_send_timeout 60m;
}
```

再为 `/api/career/interview-library/audio-sources/` 增加 `access_log off` 的前缀 location，并保持 `proxy_pass` 不移除原始 URI。

- [ ] **Step 5: 同步面经库模块文档**

在 `docs/interview_library_rag_module.md` 增加“音频解析”章节，明确记录：设计目标、DashScope 离线模型选择、短时回源调用链、只选择原始话轮 ID 的取舍、依赖、五阶段事件、只持久化问题、配置项、验证结果和“不含逐字稿/播放器/后台恢复”的后续边界。

- [ ] **Step 6: 运行部署契约和全量相关回归**

Run: `\.venv\Scripts\python.exe -m pytest tests/test_interview_audio_settings.py tests/test_interview_audio_migration.py tests/test_interview_audio_files.py tests/test_interview_audio_transcription.py tests/test_interview_audio_questions.py tests/test_interview_audio_import_service.py tests/test_interview_audio_web.py tests/test_interview_audio_deployment.py tests/test_platform_actor_middleware.py tests/test_interview_library_permissions.py tests/test_live_interview_archive.py -q`

Expected: PASS。

Run: `npm --prefix web-ui test`

Expected: PASS。

Run: `npm --prefix web-ui run build`

Expected: PASS。

- [ ] **Step 7: 执行本地 PC 视觉验收**

启动本地后端与 Vite 页面，打开 `/interviews`，确认按钮位于小红书入口左侧；手工检查空表单、已填归属和已选文件三个静态状态。五阶段进度、错误态和成功态由 `tests/test_interview_audio_web.py` 的注入式假 Provider 以及前端 NDJSON 单元测试覆盖，不为视觉验收增加只能用于测试的运行时开关，也不连接真实 DashScope 付费接口。

- [ ] **Step 8: 提交部署与模块文档**

```powershell
git add tests/test_interview_audio_deployment.py .env.career-assistant.example .env.production.example docker-compose.production.yml docker/caddy/Caddyfile docker/nginx/default.conf docs/interview_library_rag_module.md
git commit -m "docs: record interview audio import operations"
```

---

## Final Verification

- [ ] `git diff --check` 没有空白错误。
- [ ] `\.venv\Scripts\python.exe -m alembic heads` 只输出 `20260902_34 (head)`。
- [ ] 相关后端测试、全部前端 Node 测试和 Vite 构建均通过。
- [ ] `git status --short` 中用户原有改动仍然存在且未被本任务提交。
- [ ] 最终交付说明列出新增入口、支持格式、512MB/2小时边界、所需环境变量、验证结果和未部署生产的事实。

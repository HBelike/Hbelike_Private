"""验证漏页、部分 OCR 与视觉输出截断不会被当成完整简历。"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import httpx
import pytest
from PIL import Image

from src.career_assistant.attachments import AttachmentParser, AttachmentSettings
from src.career_assistant.cloud_vision import CloudVisionError, CloudVisionResult, OpenAICompatibleCloudVisionClient
from src.career_assistant.contracts import AttachmentDescriptor, AttachmentKind
from src.career_assistant.document_parsing import DocumentUnderstandingResult
from src.career_assistant.document_probe import DocumentProbeIssue, PdfDocumentProbe
from src.career_assistant.settings import CloudVisionConnectionSettings


def test_one_missing_page_in_five_still_requires_ocr():
    result = PdfDocumentProbe().probe(('完整的简历文字' * 10,) * 4 + ('',))
    assert result.requires_document_understanding
    assert DocumentProbeIssue.PARTIAL_TEXT_LAYER in result.issues


def test_partial_image_ocr_uses_vision_even_when_text_is_long(tmp_path):
    path = tmp_path / 'resume.png'
    Image.effect_noise((120, 120), 100).convert('RGB').save(path)
    attachment = AttachmentDescriptor(
        attachment_id=uuid4(), kind=AttachmentKind.RESUME_IMAGE,
        original_filename=path.name, media_type='image/png',
        size_bytes=path.stat().st_size, temporary_path=path,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    docling = Mock()
    docling.parse_document.return_value = DocumentUnderstandingResult(
        parser_name='docling-serve', analysis_text='只读取一栏经历，第二栏教育项目尚未识别。' * 4,
        status='partial_success', processing_time_seconds=None,
    )
    vision = Mock()
    vision.analyze_image.return_value = CloudVisionResult(
        analysis_text='完整经历与末尾成果', provider_key='test', model_id='vision',
    )
    parser = AttachmentParser(
        AttachmentSettings(temporary_root=tmp_path, max_size_bytes=100_000, ttl_seconds=300),
        document_understanding_parser=docling, cloud_vision_parser=vision,
    )
    result = parser.parse(attachment)
    docling.parse_document.assert_called_once()
    vision.analyze_image.assert_called_once()
    assert result.extracted_text == '完整经历与末尾成果'
    assert result.parsing_notices == ()


def test_native_pdf_truncation_is_reported(tmp_path):
    path = tmp_path / 'resume.pdf'
    path.write_bytes(b'%PDF-test')
    attachment = AttachmentDescriptor(
        attachment_id=uuid4(), kind=AttachmentKind.RESUME_PDF,
        original_filename=path.name, media_type='application/pdf',
        size_bytes=path.stat().st_size, temporary_path=path,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    parser = AttachmentParser(AttachmentSettings(
        temporary_root=tmp_path, max_size_bytes=100_000, ttl_seconds=300,
        max_extracted_characters=50,
    ))
    reader = SimpleNamespace(is_encrypted=False, pages=[SimpleNamespace(extract_text=lambda: '完整经历' * 30)])
    with patch('src.career_assistant.attachments.PdfReader', return_value=reader):
        result = parser.parse(attachment)
    assert result.text_truncated
    assert any('截断' in notice for notice in result.parsing_notices)


def test_vision_token_limit_is_an_explicit_error(monkeypatch):
    monkeypatch.setenv('TEST_VISION_KEY', 'test-only')
    monkeypatch.setenv('TEST_VISION_URL', 'https://vision.example/v1')
    settings = CloudVisionConnectionSettings(
        connection_key='test', enabled=True, provider_key='test', model_id='vision',
        api_key_env='TEST_VISION_KEY', api_base_url_env='TEST_VISION_URL',
        request_timeout_seconds=5, max_completion_tokens=800,
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
        'choices': [{'message': {'content': '被截断的简历开头'}, 'finish_reason': 'length'}],
    }))) as client:
        parser = OpenAICompatibleCloudVisionClient(settings, client=client)
        with pytest.raises(CloudVisionError, match='截断'):
            parser.analyze_image('image/png', b'image-bytes')

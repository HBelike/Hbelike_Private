"""整页图片 PDF 复用平台视觉能力，必须保留所有页面与识别失败页码。"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from PIL import Image
from pypdf import PdfReader

from src.career_assistant.attachments import AttachmentParser, AttachmentSettings
from src.career_assistant.cloud_vision import CloudVisionError, CloudVisionResult
from src.career_assistant.contracts import AttachmentDescriptor, AttachmentKind
from src.career_assistant.document_parsing import DocumentUnderstandingError, DocumentUnderstandingResult


@pytest.fixture
def scan(tmp_path):
    path = tmp_path / 'anonymous-scan.pdf'
    images = [Image.effect_noise((600, 850), 60).convert('RGB') for _ in range(2)]
    images[0].save(path, 'PDF', save_all=True, append_images=images[1:])
    for image in images:
        image.close()
    return AttachmentDescriptor(
        attachment_id=uuid4(), kind=AttachmentKind.RESUME_PDF,
        original_filename=path.name, media_type='application/pdf',
        size_bytes=path.stat().st_size, temporary_path=path,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


def vision_result(text):
    return CloudVisionResult(analysis_text=text, provider_key='test', model_id='vision')


def parser_for(scan, vision, docling=None):
    return AttachmentParser(
        AttachmentSettings(temporary_root=scan.temporary_path.parent, max_size_bytes=10*1024*1024, ttl_seconds=300),
        document_understanding_parser=docling, cloud_vision_parser=vision,
    )


@pytest.mark.parametrize('docling_mode', ['disabled', 'failed', 'partial'])
def test_two_scan_pages_are_transcribed_in_order(scan, docling_mode):
    vision = Mock()
    vision.analyze_image_with_prompt.side_effect = [
        vision_result('教育背景\n匿名学校\n工作经历\n第一页完整原文'),
        vision_result('个人作品\n第十一个模块\n专业技能\n末尾技能原文'),
    ]
    docling = None if docling_mode == 'disabled' else Mock()
    if docling_mode == 'failed':
        docling.parse_document.side_effect = DocumentUnderstandingError('文档解析服务暂时不可用')
    elif docling_mode == 'partial':
        docling.parse_document.return_value = DocumentUnderstandingResult(
            parser_name='test', analysis_text='不完整片段', status='partial_success', processing_time_seconds=None,
        )
    result = parser_for(scan, vision, docling).parse(scan)
    assert result.page_count == 2
    assert vision.analyze_image_with_prompt.call_count == 2
    assert result.extracted_text.index('第一页完整原文') < result.extracted_text.index('末尾技能原文')
    assert '第十一个模块' in result.extracted_text
    assert result.parsing_notices == ()
    for call in vision.analyze_image_with_prompt.call_args_list:
        media_type, data, prompt = call.args
        assert media_type == 'image/jpeg'
        assert data.startswith(b'\xff\xd8')
        assert '逐字' in prompt and '不要总结' in prompt


def test_failed_second_scan_page_is_reported_without_losing_first(scan):
    vision = Mock()
    vision.analyze_image_with_prompt.side_effect = [
        vision_result('第一页已识别的真实原文'), CloudVisionError('视觉输出已截断'),
    ]
    result = parser_for(scan, vision).parse(scan)
    assert '第一页已识别的真实原文' in result.extracted_text
    assert any('第 2 页' in notice and '截断' in notice for notice in result.parsing_notices)
    assert result.document_understanding_error is not None


def test_successful_docling_does_not_repeat_paid_recognition(scan):
    vision = Mock()
    docling = Mock()
    docling.parse_document.return_value = DocumentUnderstandingResult(
        parser_name='test', analysis_text='完整的两页文字内容。' * 5, status='success', processing_time_seconds=None,
    )
    result = parser_for(scan, vision, docling).parse(scan)
    vision.analyze_image_with_prompt.assert_not_called()
    assert result.extracted_text == '完整的两页文字内容。' * 5


def test_scanned_pdf_output_limit_is_still_reported(scan):
    vision = Mock()
    vision.analyze_image_with_prompt.side_effect = [vision_result('原文' * 50), vision_result('末尾' * 50)]
    parser = AttachmentParser(
        AttachmentSettings(temporary_root=scan.temporary_path.parent, max_size_bytes=10*1024*1024, ttl_seconds=300, max_extracted_characters=80),
        cloud_vision_parser=vision,
    )
    result = parser.parse(scan)
    assert len(result.extracted_text) <= 80
    assert result.text_truncated
    assert any('截断' in notice for notice in result.parsing_notices)


def test_mixed_pdf_keeps_full_native_page_and_only_recognizes_scan(scan):
    native_text = '原生文本页的完整工作经历。' * 20 + '原生页末尾证据'
    reader = SimpleNamespace(is_encrypted=False, pages=[
        SimpleNamespace(extract_text=lambda: native_text), PdfReader(scan.temporary_path).pages[1],
    ])
    vision = Mock()
    vision.analyze_image_with_prompt.return_value = vision_result('扫描页的末尾技能')
    with patch('src.career_assistant.attachments.PdfReader', return_value=reader):
        result = parser_for(scan, vision).parse(scan)
    vision.analyze_image_with_prompt.assert_called_once()
    assert native_text in result.extracted_text
    assert '扫描页的末尾技能' in result.extracted_text
    assert result.parsing_notices == ()


def test_native_text_pdf_never_calls_vision(scan):
    text = '完整的原生简历文字与真实项目证据。' * 10
    reader = SimpleNamespace(is_encrypted=False, pages=[SimpleNamespace(extract_text=lambda: text)])
    vision = Mock()
    with patch('src.career_assistant.attachments.PdfReader', return_value=reader):
        result = parser_for(scan, vision).parse(scan)
    vision.analyze_image_with_prompt.assert_not_called()
    assert result.extracted_text == text

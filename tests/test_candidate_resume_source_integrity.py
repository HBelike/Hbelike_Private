"""基准简历上传不得静默丢失末尾论文和成果。"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException, UploadFile
from src.career_assistant.attachments import ParsedAttachment
from src.career_assistant.contracts import AttachmentKind
from src.career_assistant.document_parsing import DocumentUnderstandingResult
from src.career_assistant.document_probe import PdfDocumentProbe
from src.career_assistant.web.router import parse_candidate_profile


class CandidateSourceTests(unittest.IsolatedAsyncioTestCase):
    def services(self, text, **parsed_fields):
        attachment = SimpleNamespace(original_filename='resume.pdf')
        parsed = ParsedAttachment(
            kind=AttachmentKind.RESUME_PDF, media_type='application/pdf',
            page_count=1, image_width=None, image_height=None,
            extracted_text=text, requires_vision_model=False, image_bytes=None,
            **parsed_fields,
        )
        return SimpleNamespace(
            temporary_attachment_store=SimpleNamespace(save_upload=AsyncMock(return_value=attachment), cleanup=Mock()),
            attachment_parser=SimpleNamespace(parse=Mock(return_value=parsed)),
            redactor=SimpleNamespace(redact=lambda text: text),
        )

    async def test_long_section_and_last_publication_are_preserved(self):
        text = '项目经历\n' + '真实经历原文。' * 500 + '\n论文成果\n末尾论文：第一作者，已发表。'
        services = self.services(text)
        with patch('src.career_assistant.web.router.get_career_services', return_value=services), patch('src.career_assistant.web.router._upload_kind', return_value='resume_pdf'):
            result = await parse_candidate_profile(SimpleNamespace(), UploadFile(filename='resume.pdf', file=None))
        self.assertEqual(result['resume_outline'], text)
        self.assertEqual(result['parsing_status'], 'ready')
        self.assertEqual(result['notices'], [])
        services.temporary_attachment_store.cleanup.assert_called_once()

    async def parse_with(self, services):
        with patch('src.career_assistant.web.router.get_career_services', return_value=services), patch('src.career_assistant.web.router._upload_kind', return_value='resume_pdf'):
            return await parse_candidate_profile(SimpleNamespace(), UploadFile(filename='resume.pdf', file=None))

    async def test_partial_success_requires_review_and_keeps_available_text(self):
        text = '只识别到的项目经历'
        services = self.services(text, document_understanding_result=DocumentUnderstandingResult(
            parser_name='docling-serve', analysis_text=text,
            status='partial_success', processing_time_seconds=None,
        ))
        result = await self.parse_with(services)
        self.assertEqual(result['resume_outline'], text)
        self.assertEqual(result['parsing_status'], 'needs_review')
        self.assertTrue(any('部分' in notice for notice in result['notices']))

    async def test_missing_pdf_pages_are_reported_when_ocr_is_unavailable(self):
        text = '已提取的真实经历' * 10
        services = self.services(text, document_probe=PdfDocumentProbe().probe((text, '')))
        result = await self.parse_with(services)
        self.assertEqual(result['parsing_status'], 'needs_review')
        self.assertTrue(any('PDF' in notice and '完整' in notice for notice in result['notices']))

    async def test_empty_result_exposes_actual_ocr_failure(self):
        services = self.services('', document_understanding_error='文档解析服务处理超时，请稍后重试')
        with self.assertRaises(HTTPException) as caught:
            await self.parse_with(services)
        self.assertIn('超时', caught.exception.detail)
        services.temporary_attachment_store.cleanup.assert_called_once()

    async def test_oversized_input_is_explicitly_rejected_instead_of_truncated(self):
        services = self.services('经历' * 16000)
        with patch('src.career_assistant.web.router.get_career_services', return_value=services), patch('src.career_assistant.web.router._upload_kind', return_value='resume_pdf'):
            with self.assertRaises(HTTPException) as caught:
                await parse_candidate_profile(SimpleNamespace(), UploadFile(filename='resume.pdf', file=None))
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn('30,000', caught.exception.detail)
        services.temporary_attachment_store.cleanup.assert_called_once()

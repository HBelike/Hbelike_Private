"""全网公开面经 Firecrawl 适配器与确定性清洗测试。"""

from __future__ import annotations

import json
import socket

import httpx
import pytest

from src.career_assistant.interview_library.public_web import (
    FirecrawlClient,
    FirecrawlRequestError,
    FirecrawlSettings,
    PublicWebUrlError,
    canonicalize_public_url,
    hash_public_markdown,
    infer_public_platform,
    load_firecrawl_settings,
    normalize_public_markdown,
    validate_public_https_target,
)


def test_canonicalize_public_url_removes_tracking_and_preserves_identity_query() -> None:
    assert canonicalize_public_url(
        "HTTPS://Example.COM:443/a//b/?utm_source=x&b=2&a=1&source=feed#card",
    ) == "https://example.com/a/b?a=1&b=2"


def test_canonicalize_public_url_normalizes_idna_and_root_path() -> None:
    assert canonicalize_public_url("https://例子.测试/") == "https://xn--fsqu00a.xn--0zwm56d"


@pytest.mark.parametrize(
    "value",
    (
        "http://example.com/a",
        "https://127.0.0.1/a",
        "https://[::1]/a",
        "https://example.com:8443/a",
        "https://user:secret@example.com/a",
        "https://localhost/a",
    ),
)
def test_canonicalize_public_url_rejects_unsafe_targets(value: str) -> None:
    with pytest.raises(PublicWebUrlError):
        canonicalize_public_url(value)


def test_validate_public_https_target_rejects_private_dns_result() -> None:
    def resolver(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443))]

    with pytest.raises(PublicWebUrlError, match="内网"):
        validate_public_https_target(
            "https://internal.example/interview",
            resolver=resolver,
        )


def test_validate_public_https_target_accepts_public_dns_result() -> None:
    def resolver(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    validate_public_https_target("https://example.com/interview", resolver=resolver)


def test_normalized_markdown_removes_collection_wrappers_and_has_stable_hash() -> None:
    left = "# 面经\r\n\r\n-  Redis   缓存\n来源：https://a.example\n![配图](https://img/a.png)"
    right = "# 面经\n\n* Redis 缓存\n抓取时间：2026-08-27"

    assert normalize_public_markdown(left) == "# 面经\n\n- Redis 缓存"
    assert hash_public_markdown(left) == hash_public_markdown(right)


def test_load_firecrawl_settings_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    assert load_firecrawl_settings() is None

    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setenv("FIRECRAWL_API_URL", "https://firecrawl.example/")
    settings = load_firecrawl_settings()
    assert settings == FirecrawlSettings(
        api_key="fc-test",
        api_url="https://firecrawl.example",
        timeout_seconds=60.0,
    )


def test_firecrawl_search_discovers_urls_without_scraping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/search"
        assert request.headers["authorization"] == "Bearer fc-test"
        assert json.loads(request.content) == {
            "query": "agent开发面经 (面经 OR 面试经历 OR 面试题)",
            "limit": 6,
            "sources": ["web"],
            "country": "CN",
            "ignoreInvalidURLs": True,
        }
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "web": [
                        {
                            "title": "Agent 面经",
                            "description": "一面问题",
                            "url": "https://example.com/a",
                            "category": "web",
                        },
                    ],
                },
            },
        )

    client = FirecrawlClient(
        FirecrawlSettings("fc-test"),
        transport=httpx.MockTransport(handler),
    )
    try:
        results = client.search("agent开发面经", limit=6)
    finally:
        client.close()

    assert len(results) == 1
    assert results[0].url == "https://example.com/a"
    assert results[0].snippet == "一面问题"
    assert results[0].metadata == {"category": "web"}


def test_firecrawl_scrape_returns_markdown_images_and_cache_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/scrape"
        assert json.loads(request.content) == {
            "url": "https://example.com/a",
            "formats": ["markdown", "images"],
            "onlyMainContent": True,
            "removeBase64Images": True,
            "storeInCache": True,
            "timeout": 60000,
        }
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "markdown": "# 一面\n\n1. 解释 Agent memory",
                    "images": [
                        "https://cdn.example.com/1.png",
                        "data:image/png;base64,ignored",
                    ],
                    "metadata": {
                        "url": "https://example.com/final",
                        "title": "面经",
                        "statusCode": 200,
                        "cacheState": "hit",
                        "cachedAt": "2026-08-27T00:00:00Z",
                    },
                },
            },
        )

    client = FirecrawlClient(
        FirecrawlSettings("fc-test"),
        transport=httpx.MockTransport(handler),
    )
    try:
        document = client.scrape("https://example.com/a")
    finally:
        client.close()

    assert document.final_url == "https://example.com/final"
    assert document.image_urls == ("https://cdn.example.com/1.png",)
    assert document.metadata["cacheState"] == "hit"


@pytest.mark.parametrize(
    ("status_code", "code", "retryable"),
    (
        (401, "firecrawl_auth_failed", False),
        (429, "firecrawl_rate_limited", True),
        (503, "firecrawl_unavailable", True),
    ),
)
def test_firecrawl_error_classification(
    status_code: int,
    code: str,
    retryable: bool,
) -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(status_code, json={"success": False, "error": "blocked"}),
    )
    client = FirecrawlClient(FirecrawlSettings("fc-test"), transport=transport)
    try:
        with pytest.raises(FirecrawlRequestError) as raised:
            client.search("agent开发面经", limit=1)
    finally:
        client.close()

    assert raised.value.code == code
    assert raised.value.retryable is retryable
    assert "fc-test" not in raised.value.message


def test_infer_public_platform_uses_known_label_or_hostname() -> None:
    assert infer_public_platform("https://www.nowcoder.com/discuss/1") == "牛客"
    assert infer_public_platform("https://interview.example.com/a") == "interview.example.com"

"""面经库公共资料采集模块的离线自检。

不访问互联网、不连接数据库，只验证采集边界、HTML 正文归一化和平台策略。
"""

from __future__ import annotations

from pathlib import Path
import sys

# 允许从项目根目录直接执行该离线校验脚本。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.interview_library.collection import (
    CollectionOperationError,
    InterviewCollectionService,
    PublicUrlArticleExtractor,
    _ArticleTextParser,
)
from src.career_assistant.interview_library.models import CollectionConnectorKind


def verify_html_parser() -> None:
    """脚本、样式和模板内容不能混入可入库的正文。"""

    parser = _ArticleTextParser()
    parser.feed(
        "<html><head><title>Java 后端一面复盘</title><style>body{display:none}</style>"
        "<script>window.secret = 'nope'</script></head><body><article>"
        "<h1>Java 后端一面</h1><p>面试围绕 JVM、并发和数据库索引展开，"
        "面试官重点追问了线程池参数选择、MySQL 索引失效场景以及线上排障过程。</p>"
        "<template>不应保留</template></article></body></html>"
    )
    parser.close()

    assert parser.title == "Java 后端一面复盘"
    assert "window.secret" not in parser.text
    assert "display:none" not in parser.text
    assert "Java 后端一面" in parser.text
    assert "线上排障过程" in parser.text


def verify_url_guards() -> None:
    """危险或不符合约定的 URL 要在发起网络请求前被拒绝。"""

    extractor = PublicUrlArticleExtractor()
    invalid_urls = (
        "http://example.com/article",
        "https://localhost/article",
        "https://user:password@example.com/article",
        "https://example.com:8443/article",
    )
    for value in invalid_urls:
        try:
            extractor._validate_public_https_url(value)
        except CollectionOperationError:
            continue
        raise AssertionError(f"危险 URL 未被拒绝：{value}")


def verify_platform_policy() -> None:
    """受限平台默认不执行自动抓取，公开链接走独立的受控路径。"""

    policies = InterviewCollectionService._POLICIES
    for platform in ("xiaohongshu", "nowcoder", "maimai"):
        policy = policies[platform]
        assert policy.can_run_keyword_search is False
        assert policy.connector_kind == CollectionConnectorKind.USER_AUTHORIZED_BROWSER

    public_url = policies["public_url"]
    assert public_url.connector_kind == CollectionConnectorKind.URL_IMPORT
    assert "公开 HTTPS" in public_url.policy_decision


def main() -> None:
    verify_html_parser()
    verify_url_guards()
    verify_platform_policy()
    print("面经库公共资料采集离线自检通过")


if __name__ == "__main__":
    main()

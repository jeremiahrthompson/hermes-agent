import asyncio
import json
import httpx
from unittest.mock import AsyncMock, MagicMock, patch


def test_direct_search_normalizes_duckduckgo_results():
    html = '''
    <html><body>
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Farticle">Example <b>Article</b></a>
      <div class="result__snippet">A <b>useful</b> summary.</div>
    </body></html>
    '''
    mock_response = MagicMock()
    mock_response.text = html
    mock_response.raise_for_status = MagicMock()

    with patch("tools.web_tools.httpx.get", return_value=mock_response):
        from tools.web_tools import _direct_search

        result = _direct_search("example query", limit=5)

    assert result["success"] is True
    assert result["data"]["web"] == [{
        "title": "Example Article",
        "url": "https://example.com/article",
        "description": "A useful summary.",
        "position": 1,
    }]


def test_extract_reader_markdown_splits_header_and_body():
    from tools.web_tools import _extract_reader_markdown

    title, body = _extract_reader_markdown(
        "Title: Example Page\n\nURL Source: http://example.com\n\nMarkdown Content:\n# Heading\nBody"
    )

    assert title == "Example Page"
    assert body == "# Heading\nBody"


def test_select_direct_crawl_urls_filters_to_same_host_and_caps_depth():
    from tools.web_tools import _select_direct_crawl_urls

    search_results = {
        "data": {
            "web": [
                {"url": "https://docs.example.com/a"},
                {"url": "https://other.example.com/b"},
                {"url": "https://docs.example.com/c"},
                {"url": "https://docs.example.com/d"},
                {"url": "https://docs.example.com/e"},
            ]
        }
    }

    selected = _select_direct_crawl_urls("https://docs.example.com/start", search_results, "basic")

    assert selected == [
        "https://docs.example.com/start",
        "https://docs.example.com/a",
        "https://docs.example.com/c",
        "https://docs.example.com/d",
    ]


def test_direct_crawl_search_query_uses_site_restriction_and_instructions():
    from tools.web_tools import _direct_crawl_search_query

    query = _direct_crawl_search_query("https://docs.example.com/start", "installation guide")

    assert query == "site:docs.example.com installation guide"


def test_web_search_tool_direct_dispatch():
    with patch("tools.web_tools._get_backend", return_value="direct"), \
         patch("tools.web_tools._direct_search", return_value={
             "success": True,
             "data": {"web": [{"title": "T", "url": "https://e.com", "description": "D", "position": 1}]},
         }), \
         patch("tools.interrupt.is_interrupted", return_value=False):
        from tools.web_tools import web_search_tool

        result = json.loads(web_search_tool("test query", limit=3))

    assert result["success"] is True
    assert result["data"]["web"][0]["url"] == "https://e.com"


def test_web_search_tool_falls_back_to_direct_when_tavily_http_fails():
    request = httpx.Request("POST", "https://api.tavily.com/search")
    response = httpx.Response(432, request=request, text="quota or account error")
    tavily_error = httpx.HTTPStatusError("Tavily failed", request=request, response=response)

    with patch("tools.web_tools._get_backend", return_value="tavily"), \
         patch("tools.web_tools._tavily_request", side_effect=tavily_error), \
         patch("tools.web_tools._direct_search", return_value={
             "success": True,
             "data": {"web": [{"title": "Fallback", "url": "https://e.com", "description": "D", "position": 1}]},
         }) as direct_search, \
         patch("tools.interrupt.is_interrupted", return_value=False):
        from tools.web_tools import web_search_tool

        result = json.loads(web_search_tool("test query", limit=3))

    assert result["success"] is True
    assert result["data"]["web"][0]["title"] == "Fallback"
    direct_search.assert_called_once_with("test query", 3)


def test_web_extract_tool_direct_dispatch():
    with patch("tools.web_tools._get_backend", return_value="direct"), \
         patch("tools.web_tools._direct_extract", new=AsyncMock(return_value=[{
             "url": "https://example.com",
             "title": "Example",
             "content": "Body text",
             "raw_content": "Body text",
         }])), \
         patch("tools.web_tools.process_content_with_llm", return_value=None):
        from tools.web_tools import web_extract_tool

        result = json.loads(asyncio.get_event_loop().run_until_complete(
            web_extract_tool(["https://example.com"], use_llm_processing=False)
        ))

    assert result["results"] == [{
        "url": "https://example.com",
        "title": "Example",
        "content": "Body text",
        "error": None,
    }]


def test_web_extract_tool_falls_back_to_direct_when_tavily_http_fails():
    request = httpx.Request("POST", "https://api.tavily.com/extract")
    response = httpx.Response(432, request=request, text="quota or account error")
    tavily_error = httpx.HTTPStatusError("Tavily failed", request=request, response=response)

    with patch("tools.web_tools._get_backend", return_value="tavily"), \
         patch("tools.web_tools._tavily_request", side_effect=tavily_error), \
         patch("tools.web_tools._direct_extract", new=AsyncMock(return_value=[{
             "url": "https://example.com",
             "title": "Fallback",
             "content": "Fallback body",
             "raw_content": "Fallback body",
         }])) as direct_extract, \
         patch("tools.web_tools.process_content_with_llm", return_value=None):
        from tools.web_tools import web_extract_tool

        result = json.loads(asyncio.get_event_loop().run_until_complete(
            web_extract_tool(["https://example.com"], use_llm_processing=False)
        ))

    assert result["results"][0]["title"] == "Fallback"
    direct_extract.assert_awaited_once()


def test_direct_extract_uses_reader_specific_headers():
    captured_headers = None

    class FakeResponse:
        text = "Title: Example\n\nMarkdown Content:\nBody"

        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        def __init__(self, headers=None, timeout=None, follow_redirects=None):
            nonlocal captured_headers
            captured_headers = headers

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url):
            return FakeResponse()

    with patch("tools.web_tools.httpx.AsyncClient", FakeAsyncClient):
        from tools.web_tools import _direct_extract

        result = asyncio.get_event_loop().run_until_complete(_direct_extract(["https://example.com"]))

    assert result[0]["content"] == "Body"
    assert captured_headers is not None
    assert "Chrome/" not in captured_headers.get("User-Agent", "")


def test_web_crawl_tool_direct_dispatch():
    with patch("tools.web_tools._get_backend", return_value="direct"), \
         patch("tools.web_tools._direct_crawl", new=AsyncMock(return_value=[{
             "url": "https://docs.example.com/start",
             "title": "Start",
             "content": "Crawled body",
             "raw_content": "Crawled body",
         }])), \
         patch("tools.web_tools.check_website_access", return_value=None), \
         patch("tools.web_tools.is_safe_url", return_value=True), \
         patch("tools.interrupt.is_interrupted", return_value=False):
        from tools.web_tools import web_crawl_tool

        result = json.loads(asyncio.get_event_loop().run_until_complete(
            web_crawl_tool("https://docs.example.com/start", instructions="install", use_llm_processing=False)
        ))

    assert result["results"] == [{
        "url": "https://docs.example.com/start",
        "title": "Start",
        "content": "Crawled body",
        "error": None,
    }]

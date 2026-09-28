"""Crawler tests against a local HTTP server (no third-party sites)."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

import pytest
from bs4 import BeautifulSoup
from crawlee import service_locator
from crawlee.configuration import Configuration
from crawlee.crawlers._beautifulsoup._beautifulsoup_parser import BeautifulSoupParser

from src.crawler import CrawlSettings, _Crawler, _FastBlockCheckParser, extract_links, run_crawler

if TYPE_CHECKING:
    from collections.abc import Iterator

# crawlee's storages are cached globally and bound to the event loop they were created in
same_loop = pytest.mark.asyncio(loop_scope='module')

PAGE_COUNT = 60


class _SiteHandler(BaseHTTPRequestHandler):
    """Serves /docs and /docs/0 ... /docs/59, every page links to all of them (like a full sidebar)."""

    delay = 0.0

    def do_GET(self) -> None:  # noqa: N802
        if not self.path.startswith('/docs'):
            self.send_response(404)
            self.end_headers()
            return
        time.sleep(self.delay)
        links = ''.join(f'<li><a href="/docs/{i}">Page {i}</a></li>' for i in range(PAGE_COUNT))
        body = f'<html><head><title>{self.path}</title></head><body><nav><ul>{links}</ul></nav>'
        body += f'<main><h1>Page {self.path}</h1><p>Content.</p></main></body></html>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture(scope='module', autouse=True)
def _storage(tmp_path_factory: pytest.TempPathFactory) -> None:
    # file system storage in a temporary directory: with `MemoryStorageClient`, crawlee's autoscaled pool busy-loops
    # (its queue checks never yield to the event loop for real), which slows the crawl down to seconds per page
    service_locator.set_configuration(Configuration(storage_dir=str(tmp_path_factory.mktemp('storage'))))


@pytest.fixture
def site() -> Iterator[type[_SiteHandler]]:
    handler = type('Handler', (_SiteHandler,), {'delay': 0.0})
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    handler.base_url = f'http://127.0.0.1:{server.server_address[1]}'  # type: ignore[attr-defined]
    try:
        yield handler
    finally:
        server.shutdown()


@same_loop
async def test_crawl_shares_outlink_keys_and_drops_links_beyond_the_budget(site: type[_SiteHandler]) -> None:
    base_url = site.base_url  # type: ignore[attr-defined]
    result = await run_crawler(CrawlSettings(start_url=f'{base_url}/docs', max_crawl_pages=10))

    assert len(result.pages) == 10
    crawled = {page['key'] for page in result.pages}
    for page in result.pages:
        # only links to pages that were crawled are kept, the budget was spent on the start page
        assert set(page['outlinks']) <= crawled
    # the same key is one shared string object across all pages
    first = {key: key for key in result.pages[0]['outlinks']}
    for page in result.pages[1:]:
        assert all(first.get(key, key) is key for key in page['outlinks'])


@same_loop
async def test_crawl_stops_at_the_deadline(site: type[_SiteHandler], monkeypatch: pytest.MonkeyPatch) -> None:
    """Already enqueued pages don't keep the crawl running past the deadline."""
    monkeypatch.setattr('src.crawler.STOP_GRACE', timedelta(seconds=1))
    site.delay = 1.0
    base_url = site.base_url  # type: ignore[attr-defined]
    started = time.monotonic()
    result = await run_crawler(
        CrawlSettings(
            start_url=f'{base_url}/docs',
            max_crawl_pages=PAGE_COUNT,
            deadline=datetime.now(UTC) + timedelta(seconds=3),
        )
    )
    elapsed = time.monotonic() - started

    assert result.deadline_reached
    assert 1 <= len(result.pages) < PAGE_COUNT
    # deadline + grace + a little slack for the crawler to shut down
    assert elapsed < 3 + 1 + 3


def test_extract_links() -> None:
    html = """<html><head><base href="/docs/"></head><body>
    <a href="intro">Intro</a> <a href=" https://other.example/x ">Other</a> <a href="#top">Top</a>
    <a href="">Empty</a> <a>No href</a> <a href="mailto:a@b.c">Mail</a> <a href="http://[::1">Broken</a>
    </body></html>"""
    links = extract_links(BeautifulSoup(html, 'lxml'), 'https://e.com/site/page')
    assert links == ['https://e.com/docs/intro', 'https://other.example/x', 'mailto:a@b.c']


def test_fast_block_check_matches_crawlee() -> None:
    blocked_html = (
        '<html><body><div id="turnstile-wrapper"><iframe src="https://challenges.cloudflare.com/x"></iframe>'
        '</div></body></html>'
    )
    iframe_html = '<html><body><iframe src="https://www.youtube.com/embed/x"></iframe></body></html>'
    plain_html = '<html><body><main><p>Docs</p></main></body></html>'
    incapsula_html = '<html><body><iframe src="/_Incapsula_Resource?x=1"></iframe></body></html>'
    google_html = (
        '<html><body><div id="infoDiv0"><a href="https://www.google.com/policies/terms/">x</a></div></body></html>'
    )
    for html in [blocked_html, iframe_html, plain_html, incapsula_html, google_html]:
        soup = BeautifulSoup(html, 'lxml')
        assert _FastBlockCheckParser().is_blocked(soup) == BeautifulSoupParser().is_blocked(soup)
    for html in [blocked_html, incapsula_html, google_html]:
        assert _FastBlockCheckParser().is_blocked(BeautifulSoup(html, 'lxml')).reason
    assert isinstance(_Crawler()._parser, _FastBlockCheckParser)

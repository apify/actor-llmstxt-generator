from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

from bs4.element import Tag
from crawlee import ConcurrencySettings
from crawlee._utils.blocked import RETRY_CSS_SELECTORS
from crawlee.crawlers import BeautifulSoupCrawler, BeautifulSoupCrawlingContext
from crawlee.crawlers._beautifulsoup._beautifulsoup_parser import BeautifulSoupParser
from crawlee.crawlers._types import BlockedInfo

from src.helpers import (
    compute_scope,
    get_description_from_soup,
    get_html_title,
    get_meta_content,
    get_page_title,
    get_suitable_description,
    is_in_scope,
    looks_like_non_html,
    matches_any_glob,
    url_key,
)
from src.markdown import html_to_markdown, is_markdown_response, pack_markdown
from src.mytypes import CrawlResult

if TYPE_CHECKING:
    import bs4
    from apify import ProxyConfiguration

logger = logging.getLogger('apify')

# After `CrawlSettings.deadline`, requests already being processed get this long to finish before the crawl is
# cut off, so a slow page can never push the run past its timeout.
STOP_GRACE = timedelta(seconds=15)
# Upper bound of the URL caches of `_CrawlState`, a safety net for sites with endless unique URLs.
_KEY_CACHE_MAX_SIZE = 200_000


# crawlee's block detection runs these CSS selectors over every page, which took ~20% of the crawl's CPU time. Each
# of them needs an iframe of a bot-protection service or an `#infoDiv0` element (see `_FastBlockCheckParser`).
_BLOCKING_IFRAME_SRC_RE = re.compile(r'challenges\.cloudflare\.com|_Incapsula_Resource')
_BLOCK_SELECTORS_HAVE_PREFILTER = all(
    _BLOCKING_IFRAME_SRC_RE.search(selector) or '#infoDiv0' in selector for selector in RETRY_CSS_SELECTORS
)


class _FastBlockCheckParser(BeautifulSoupParser):
    """crawlee's parser with the same block detection, skipped on pages that can't match any blocking selector.

    Two quick `find` calls rule out every `RETRY_CSS_SELECTORS` match on almost every page, so the slow CSS
    selectors only run when a page may really be a bot-protection challenge. If crawlee ever adds a selector the
    prefilter doesn't cover, the full check always runs.
    """

    def is_blocked(self, parsed_content: bs4.BeautifulSoup) -> BlockedInfo:
        if (
            _BLOCK_SELECTORS_HAVE_PREFILTER
            and parsed_content.find('iframe', src=_BLOCKING_IFRAME_SRC_RE) is None
            and parsed_content.find(id='infoDiv0') is None
        ):
            return BlockedInfo(reason='')
        blocked: BlockedInfo = super().is_blocked(parsed_content)
        return blocked


class _Crawler(BeautifulSoupCrawler):
    """`BeautifulSoupCrawler` with `_FastBlockCheckParser` (crawlee builds its parser internally)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(parser='lxml', **kwargs)
        self._parser = _FastBlockCheckParser('lxml')


def extract_links(soup: bs4.BeautifulSoup, page_url: str) -> list[str]:
    """Returns absolute URLs of all `<a href>` links on the page, respecting `<base href>`.

    Replaces crawlee's `context.extract_links`, which took ~25% of the crawl's CPU time (a CSS selector over the
    whole page and a validated `Request` object per link, although most links are dropped by the crawl filters).
    Links disallowed by robots.txt are still enqueued, but crawlee skips them before downloading.
    """
    base_url = page_url
    if isinstance(base := soup.find('base', href=True), Tag) and isinstance(href := base.get('href'), str):
        with contextlib.suppress(ValueError):
            base_url = urljoin(page_url, href.strip())
    urls: list[str] = []
    for anchor in soup.find_all('a', href=True):
        if not isinstance(anchor, Tag) or not isinstance(href := anchor.get('href'), str):
            continue
        href = href.strip()
        if not href or href.startswith('#'):
            continue
        try:
            urls.append(urljoin(base_url, href))
        except ValueError:  # e.g. an invalid IPv6 host
            continue
    return urls


@dataclass
class CrawlSettings:
    """Settings of a single crawl."""

    start_url: str
    max_crawl_depth: int = 1
    max_crawl_pages: int = 50
    exclude_url_globs: list[str] = field(default_factory=list)
    # collect Markdown content of every page for `llms-full.txt`
    collect_markdown: bool = False
    proxy: ProxyConfiguration | None = None
    # stop the crawl at this time (requests in progress get `STOP_GRACE` more), so there is time left for the AI
    # step and for saving the results
    deadline: datetime | None = None


@dataclass
class _CrawlState:
    hosts: frozenset[str]
    path_prefix: str
    # keys of all URLs that were enqueued, including the start URL
    enqueued: set[str]
    deadline_reached: bool = False
    # set when `run_crawler` returns: request handlers that crawlee left running after a stop must not add pages
    # to the result anymore
    closed: bool = False
    # URL -> its `url_key`. Site navigation repeats the same links on every page, so this saves recomputing the
    # key for each of them, and makes every page's `outlinks` share one string object per key instead of holding
    # its own copy (measured: 2,000 pages x 2,000 nav links held ~430 MB of duplicate key strings).
    _keys: dict[str, str] = field(default_factory=dict)

    def key_of(self, url: str) -> str:
        """Returns the (shared) `url_key` of the URL."""
        if (key := self._keys.get(url)) is not None:
            return key
        key = url_key(url)
        if len(self._keys) < _KEY_CACHE_MAX_SIZE:
            # store the key object under its own value too, so equal keys from different raw URLs are shared
            key = self._keys.setdefault(key, key)
            self._keys[url] = key
        return key

    # URL -> its key when it passes the crawl filters, `None` when it doesn't, see `link_key`
    _links: dict[str, str | None] = field(default_factory=dict)

    def set_scope(self, hosts: frozenset[str], path_prefix: str) -> None:
        """Changes the scope of the crawl, which invalidates the cached link filter results."""
        self.hosts, self.path_prefix = hosts, path_prefix
        self._links.clear()

    def link_key(self, url: str, exclude_url_globs: list[str]) -> str | None:
        """Returns the key of a link that should be followed, `None` for links out of scope, to files or excluded.

        Cached, because parsing every link of the site navigation again on every page was the biggest part of the
        per-page CPU time on sites with a large sidebar.
        """
        if url in self._links:
            return self._links[url]
        key: str | None = None
        if (
            is_in_scope(url, self.hosts, self.path_prefix)
            and not looks_like_non_html(url)
            and not matches_any_glob(url, exclude_url_globs)
        ):
            key = self.key_of(url)
        if len(self._links) < _KEY_CACHE_MAX_SIZE:
            self._links[url] = key
        return key


async def _find_existing_llms_txt(context: BeautifulSoupCrawlingContext, candidates: list[str]) -> str | None:
    """Returns the first candidate URL that serves an `llms.txt` file."""
    for candidate in candidates:
        try:
            response = await context.send_request(candidate)
            if response.status_code != 200:  # noqa: PLR2004
                continue
            body = (await response.read()).decode('utf-8', 'replace').lstrip('﻿ \n')
            if body.startswith('# ') and not body.lower().startswith('<!doctype'):
                return candidate
        except Exception as exc:  # the probe is best effort only
            context.log.debug(f'Failed to probe {candidate}: {exc}')
    return None


async def _get_markdown_twin(context: BeautifulSoupCrawlingContext, markdown_url: str) -> str | None:
    """Downloads the Markdown version of the page, returns `None` if it is not available."""
    try:
        response = await context.send_request(markdown_url)
        if response.status_code != 200:  # noqa: PLR2004
            return None
        body = (await response.read()).decode('utf-8', 'replace')
        content_type = response.headers.get('content-type') or ''
        return body if is_markdown_response(content_type, body) else None
    except Exception as exc:
        context.log.warning(f'Failed to download Markdown version {markdown_url}: {exc}')
        return None


def _get_markdown_url(context: BeautifulSoupCrawlingContext) -> str | None:
    """Returns the URL of the Markdown version advertised via <link rel="alternate" type="text/markdown">."""
    for link in context.soup.find_all('link', attrs={'type': 'text/markdown'}):
        if not isinstance(link, Tag):
            continue
        rel = link.get('rel') or []
        href = link.get('href')
        if 'alternate' in rel and isinstance(href, str) and href.strip():
            return urljoin(context.request.loaded_url or context.request.url, href.strip())
    return None


async def run_crawler(settings: CrawlSettings) -> CrawlResult:
    """Crawls the site from the start URL and returns metadata of the crawled pages."""
    result = CrawlResult(start_url=settings.start_url)
    start_key = url_key(settings.start_url)
    hosts, path_prefix = compute_scope(settings.start_url, None)
    state = _CrawlState(hosts=hosts, path_prefix=path_prefix, enqueued={start_key})

    async def request_handler(context: BeautifulSoupCrawlingContext) -> None:
        request = context.request
        is_start_page = url_key(request.url) == start_key

        content_type = context.http_response.headers.get('content-type') or ''
        if content_type and 'html' not in content_type.lower():
            context.log.info(f'Skipping non-HTML page {request.url} ({content_type})')
            return

        if is_start_page:
            result.loaded_url = request.loaded_url or request.url
            state.set_scope(*compute_scope(settings.start_url, result.loaded_url))
            # links back to the redirected start page must not be crawled again
            state.enqueued.add(url_key(result.loaded_url))
            if url_key(result.loaded_url) != start_key:
                context.log.info(
                    f'Start URL redirected to {result.loaded_url}, crawling '
                    f'{", ".join(sorted(state.hosts))} under "{state.path_prefix or "/"}"'
                )

        links = extract_links(context.soup, request.loaded_url or request.url)
        if is_start_page:
            result.start_page_link_count = len(links)

        outlinks: list[str] = []
        to_enqueue: list[str] = []
        can_enqueue = request.crawl_depth < settings.max_crawl_depth
        if state.deadline_reached:
            can_enqueue = False

        request_key = state.key_of(request.url)
        seen_on_page: set[str] = set()
        for link in links:
            url = link.split('#', 1)[0]
            key = state.link_key(url, settings.exclude_url_globs)
            if key is None or key == request_key or key in seen_on_page:
                continue
            seen_on_page.add(key)
            if key not in state.enqueued:
                if len(state.enqueued) >= settings.max_crawl_pages:
                    # the page budget is spent, so this page can never be crawled and the link is useless for the
                    # navigation order -- dropping it keeps memory flat on sites that link every page from every page
                    continue
                if can_enqueue:
                    state.enqueued.add(key)
                    to_enqueue.append(url)
            outlinks.append(key)
        if to_enqueue:
            await context.add_requests(to_enqueue)

        title = get_page_title(context.soup)
        if not title:
            context.log.warning(f'No title found for {request.url}, skipping it.')
            return

        description = get_suitable_description(get_description_from_soup(context.soup)) or get_suitable_description(
            get_meta_content(context.soup, prop='og:description')
        )
        markdown_url = _get_markdown_url(context)
        markdown: str | None = None
        if settings.collect_markdown:
            if markdown_url:
                markdown = await _get_markdown_twin(context, markdown_url)
            if markdown is None:
                # modifies the soup, so it must run after everything else that reads it
                markdown = html_to_markdown(context.soup, base_url=request.loaded_url or request.url)

        if is_start_page:
            loaded = urlparse(request.loaded_url or request.url)
            origin = f'{loaded.scheme}://{loaded.netloc}'
            candidates = [f'{origin}{state.path_prefix}/llms.txt'] if state.path_prefix else []
            candidates.append(f'{origin}/llms.txt')
            result.existing_llms_txt_url = await _find_existing_llms_txt(context, candidates)

        if state.closed:
            return
        result.pages.append(
            {
                'url': request.url,
                'key': request_key,
                'title': title,
                'html_title': get_html_title(context.soup),
                'description': description,
                'markdown_url': markdown_url,
                'markdown': pack_markdown(markdown),
                'outlinks': outlinks,
                'depth': request.crawl_depth,
            }
        )

    crawler = _Crawler(
        request_handler=request_handler,
        max_crawl_depth=settings.max_crawl_depth,
        # safety net only, the page budget is enforced when enqueuing so the crawler never overshoots it
        max_requests_per_crawl=settings.max_crawl_pages,
        max_request_retries=2,
        respect_robots_txt_file=True,
        concurrency_settings=ConcurrencySettings(min_concurrency=5, desired_concurrency=15, max_concurrency=50),
        **({'proxy_configuration': settings.proxy} if settings.proxy else {}),
    )
    if (deadline := settings.deadline) is None:
        try:
            await crawler.run([settings.start_url])
        finally:
            state.closed = True
        return result

    async def stop_at_deadline() -> None:
        await asyncio.sleep(max(0.0, (deadline - datetime.now(UTC)).total_seconds()))
        state.deadline_reached = True
        logger.warning(
            f'Approaching the run timeout, stopping the crawl with {len(result.pages)} pages crawled so far. '
            'Increase the run timeout (or the memory, which also speeds up the crawl) to crawl more pages.'
        )
        crawler.stop('Approaching the run timeout')

    watchdog = asyncio.create_task(stop_at_deadline())
    hard_stop = (deadline + STOP_GRACE - datetime.now(UTC)).total_seconds()
    try:
        async with asyncio.timeout(max(hard_stop, 0.0)):
            await crawler.run([settings.start_url])
    except TimeoutError:
        logger.warning(f'Requests still running {STOP_GRACE.total_seconds():.0f} s after the stop were abandoned.')
    finally:
        state.closed = True
        watchdog.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watchdog
    result.deadline_reached = state.deadline_reached
    return result

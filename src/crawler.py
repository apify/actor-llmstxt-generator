from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

from bs4.element import Tag
from crawlee import ConcurrencySettings
from crawlee.crawlers import BeautifulSoupCrawler, BeautifulSoupCrawlingContext

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
from src.markdown import html_to_markdown, is_markdown_response
from src.mytypes import CrawlResult

if TYPE_CHECKING:
    from apify import ProxyConfiguration

logger = logging.getLogger('apify')


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
    # stop enqueuing new pages after this time, so there is time left to save the results
    deadline: datetime | None = None


@dataclass
class _CrawlState:
    hosts: frozenset[str]
    path_prefix: str
    # keys of all URLs that were enqueued, including the start URL
    enqueued: set[str]
    deadline_reached: bool = False


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
            state.hosts, state.path_prefix = compute_scope(settings.start_url, result.loaded_url)
            # links back to the redirected start page must not be crawled again
            state.enqueued.add(url_key(result.loaded_url))
            if url_key(result.loaded_url) != start_key:
                context.log.info(
                    f'Start URL redirected to {result.loaded_url}, crawling '
                    f'{", ".join(sorted(state.hosts))} under "{state.path_prefix or "/"}"'
                )

        links = await context.extract_links(strategy='all')
        if is_start_page:
            result.start_page_link_count = len(links)

        outlinks: list[str] = []
        to_enqueue: list[str] = []
        can_enqueue = request.crawl_depth < settings.max_crawl_depth
        if can_enqueue and settings.deadline and datetime.now(UTC) >= settings.deadline:
            if not state.deadline_reached:
                context.log.warning('Approaching the run timeout, not enqueuing any more pages.')
            state.deadline_reached = True
            can_enqueue = False

        for link in links:
            url = link.url.split('#', 1)[0]
            if not is_in_scope(url, state.hosts, state.path_prefix) or looks_like_non_html(url):
                continue
            if matches_any_glob(url, settings.exclude_url_globs):
                continue
            key = url_key(url)
            if key == url_key(request.url) or key in outlinks:
                continue
            outlinks.append(key)
            if can_enqueue and key not in state.enqueued and len(state.enqueued) < settings.max_crawl_pages:
                state.enqueued.add(key)
                to_enqueue.append(url)
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
                markdown = html_to_markdown(context.soup, base_url=request.loaded_url or request.url)

        if is_start_page:
            loaded = urlparse(request.loaded_url or request.url)
            origin = f'{loaded.scheme}://{loaded.netloc}'
            candidates = [f'{origin}{state.path_prefix}/llms.txt'] if state.path_prefix else []
            candidates.append(f'{origin}/llms.txt')
            result.existing_llms_txt_url = await _find_existing_llms_txt(context, candidates)

        result.pages.append(
            {
                'url': request.url,
                'key': url_key(request.url),
                'title': title,
                'html_title': get_html_title(context.soup),
                'description': description,
                'markdown_url': markdown_url,
                'markdown': markdown,
                'outlinks': outlinks,
                'depth': request.crawl_depth,
            }
        )

    crawler = BeautifulSoupCrawler(
        request_handler=request_handler,
        max_crawl_depth=settings.max_crawl_depth,
        # safety net only, the page budget is enforced when enqueuing so the crawler never overshoots it
        max_requests_per_crawl=settings.max_crawl_pages,
        max_request_retries=2,
        respect_robots_txt_file=True,
        concurrency_settings=ConcurrencySettings(min_concurrency=5, desired_concurrency=15, max_concurrency=50),
        **({'proxy_configuration': settings.proxy} if settings.proxy else {}),  # type: ignore[arg-type]
    )
    await crawler.run([settings.start_url])
    return result

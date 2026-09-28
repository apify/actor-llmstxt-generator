"""Entry point of the /llms.txt generator Actor."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from apify import Actor

from src.builder import build_llms_data, entries_in_llms_order
from src.crawler import CrawlSettings, run_crawler
from src.curation import DEFAULT_MODEL, curate_with_ai, estimate_ai_seconds
from src.markdown import unpack_markdown
from src.renderer import render_llms_full_txt, render_llms_txt

if TYPE_CHECKING:
    from apify import ProxyConfiguration

    from src.mytypes import CrawlResult

logger = logging.getLogger('apify')

LLMS_TXT_KEY = 'llms.txt'
LLMS_FULL_TXT_KEY = 'llms-full.txt'
TEXT_CONTENT_TYPE = 'text/plain; charset=utf-8'
# Limits of the input, keep in sync with `.actor/input_schema.json` (API callers can bypass the schema defaults).
DEFAULT_MAX_CRAWL_PAGES = 100
MAX_CRAWL_PAGES = 5000
MAX_CRAWL_DEPTH = 10
# time kept at the end of the run for building and saving the output
FINISH_RESERVE = timedelta(seconds=40)
# share of the run the crawl always gets, even when the AI step would like to reserve more
MIN_CRAWL_SHARE = 0.5
# `llms-full.txt` may use at most this share of the run's memory (the rest is the crawler, the page contents and the
# upload), pages beyond the limit are left out with a note
FULL_TXT_MEMORY_SHARE = 0.15
# a dataset item must be under 9 MB, the full `llms.txt` is always in the key-value store
MAX_DATASET_LLMS_TXT_CHARS = 4_000_000


def normalize_start_url(url: Any) -> str:
    """Validates the start URL and adds the https:// scheme if it is missing."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError('Missing "startUrl" in the input!')
    url = url.strip()
    if '://' not in url:
        url = f'https://{url}'
    if not url.startswith(('http://', 'https://')):
        raise ValueError(f'"startUrl" must be an http(s) URL, got: {url}')
    return url


def plan_deadlines(
    now: datetime, timeout_at: datetime | None, *, ai_seconds: float
) -> tuple[datetime | None, datetime | None]:
    """Splits the run time into the crawl and the AI step, returns their deadlines (`None` without a run timeout).

    The crawl stops at its deadline (see `CrawlSettings.deadline`), the AI step at its own (see `curate_with_ai`),
    and `FINISH_RESERVE` is left for saving the output, so the run always ends with a result instead of timing out.
    """
    if timeout_at is None:
        return None, None
    finish_at = timeout_at - FINISH_RESERVE
    available = max((finish_at - now).total_seconds(), 0.0)
    ai_reserve = min(ai_seconds, available * (1 - MIN_CRAWL_SHARE))
    return finish_at - timedelta(seconds=ai_reserve), finish_at


def int_input(value: Any, default: int, minimum: int, maximum: int) -> int:
    """Reads an integer input, falls back to the default when it is missing or invalid and clamps it to the limits."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return min(max(number, minimum), maximum)


def explain_empty_result(result: CrawlResult) -> str:
    """Explains why no pages besides the start page were found."""
    if result.deadline_reached and len(result.pages) <= 1:
        return (
            'The run timeout was reached before any page besides the start page was crawled. '
            'Increase the timeout in the run options and try again.'
        )
    if not result.pages:
        return (
            f'The start page {result.start_url} could not be loaded. Check that the URL is correct and publicly '
            'accessible. If the site blocks bots, try enabling Apify Proxy in the input.'
        )
    if result.start_page_link_count == 0:
        return (
            f'The start page {result.start_url} contains no links in its HTML. The site is most likely rendered '
            'with JavaScript in the browser (single-page app), which this Actor does not execute. If the site has '
            'a pre-rendered documentation URL or a sitemap-based docs host, start from there.'
        )
    return (
        f'No links on {result.loaded_url or result.start_url} point to pages under the start URL. '
        'Try starting from a higher-level URL (e.g. the site root), increasing "maxCrawlDepth", '
        'or loosening "excludeUrlGlobs".'
    )


async def get_proxy(proxy_input: dict | None) -> ProxyConfiguration | None:
    """Creates the proxy configuration, runs without proxy when Apify Proxy is not available locally."""
    try:
        return await Actor.create_proxy_configuration(actor_proxy_input=proxy_input)
    except Exception as exc:
        if Actor.is_at_home():
            raise
        logger.warning(f'Apify Proxy is not available locally ({exc}), crawling without proxy.')
        return None


async def main() -> None:
    """Main entry point of the /llms.txt generator Actor."""
    async with Actor:
        actor_input = await Actor.get_input() or {}
        started = time.monotonic()
        start_url = normalize_start_url(actor_input.get('startUrl'))
        max_crawl_depth = int_input(actor_input.get('maxCrawlDepth'), 1, 1, MAX_CRAWL_DEPTH)
        max_crawl_pages = int_input(actor_input.get('maxCrawlPages'), DEFAULT_MAX_CRAWL_PAGES, 2, MAX_CRAWL_PAGES)
        exclude_url_globs = [glob for glob in actor_input.get('excludeUrlGlobs') or [] if isinstance(glob, str)]
        generate_full_txt = bool(actor_input.get('generateLlmsFullTxt', True))
        link_to_markdown = bool(actor_input.get('linkToMarkdown', True))
        use_ai = bool(actor_input.get('aiCuration', False))
        ai_model = actor_input.get('aiModel') or DEFAULT_MODEL

        crawl_deadline, ai_deadline = plan_deadlines(
            datetime.now(UTC),
            Actor.configuration.timeout_at if Actor.is_at_home() else None,
            ai_seconds=estimate_ai_seconds(max_crawl_pages) if use_ai else 0,
        )

        await Actor.set_status_message(f'Crawling {start_url}...')
        result = await run_crawler(
            CrawlSettings(
                start_url=start_url,
                max_crawl_depth=max_crawl_depth,
                max_crawl_pages=max_crawl_pages,
                exclude_url_globs=exclude_url_globs,
                collect_markdown=generate_full_txt,
                proxy=await get_proxy(actor_input.get('proxyConfiguration')),
                deadline=crawl_deadline,
            )
        )
        crawl_secs = time.monotonic() - started

        data, start_page, entries = build_llms_data(result, link_to_markdown=link_to_markdown)
        if not entries:
            message = explain_empty_result(result)
            await Actor.fail(status_message=message)
            return

        if use_ai:
            if not (token := Actor.configuration.token) or not Actor.is_at_home():
                logger.warning('AI curation is available only when running on the Apify platform, skipping it.')
            else:
                await Actor.set_status_message(f'Crawled {len(result.pages)} pages, curating with AI...')
                try:
                    data = await curate_with_ai(
                        data,
                        entries,
                        start_url=start_url,
                        token=token,
                        model=ai_model,
                        link_to_markdown=link_to_markdown,
                        deadline=ai_deadline,
                    )
                except TimeoutError as exc:
                    logger.warning(
                        f'AI curation did not finish before the run timeout, using the structure based on URL '
                        f'paths. Increase the run timeout to give it more time. ({exc})'
                    )
                except Exception as exc:
                    logger.warning(f'AI curation failed, using the structure based on URL paths: {exc}')

        store = await Actor.open_key_value_store()
        llms_txt = render_llms_txt(data)
        await store.set_value(LLMS_TXT_KEY, llms_txt, content_type=TEXT_CONTENT_TYPE)
        if len(llms_txt) > MAX_DATASET_LLMS_TXT_CHARS:
            dataset_llms_txt = f'{llms_txt[:MAX_DATASET_LLMS_TXT_CHARS]}\n\n[Truncated, download the full file.]\n'
        else:
            dataset_llms_txt = llms_txt
        output: dict[str, Any] = {
            'url': start_url,
            LLMS_TXT_KEY: dataset_llms_txt,
            'llmsTxtUrl': await store.get_public_url(LLMS_TXT_KEY),
            'llmsFullTxtUrl': None,
            'pagesCrawled': len(result.pages),
            'linksInLlmsTxt': sum(len(section['links']) for section in data['sections']),
            'existingLlmsTxtUrl': result.existing_llms_txt_url,
            'crawlStoppedEarly': result.deadline_reached,
            'pagesLeftOutOfLlmsFullTxt': 0,
        }

        full_txt_omitted = 0
        if generate_full_txt:
            ordered_entries = entries_in_llms_order(data, entries, link_to_markdown=link_to_markdown)
            max_bytes = None
            if memory_mbytes := Actor.configuration.memory_mbytes:
                max_bytes = int(memory_mbytes * 1024 * 1024 * FULL_TXT_MEMORY_SHARE)
            full_txt, full_txt_omitted = render_llms_full_txt(
                data,
                unpack_markdown(start_page['markdown']) if start_page else None,
                ordered_entries,
                max_bytes=max_bytes,
            )
            if full_txt_omitted:
                logger.warning(
                    f'"{LLMS_FULL_TXT_KEY}" reached the size limit of this run, {full_txt_omitted} pages were left '
                    'out of it. Run with more memory to include them.'
                )
            await store.set_value(LLMS_FULL_TXT_KEY, full_txt, content_type=TEXT_CONTENT_TYPE)
            output['llmsFullTxtUrl'] = await store.get_public_url(LLMS_FULL_TXT_KEY)
            output['pagesLeftOutOfLlmsFullTxt'] = full_txt_omitted
            logger.info(f'Saved "{LLMS_FULL_TXT_KEY}" ({len(full_txt):,} bytes).')
            del full_txt

        await Actor.push_data(output)

        message = (
            f'Generated llms.txt with {output["linksInLlmsTxt"]} links from {len(result.pages)} crawled pages '
            f'in {crawl_secs:.0f} s.'
        )
        if result.deadline_reached:
            message += ' The crawl was stopped early to finish before the run timeout, increase it to crawl more pages.'
        if full_txt_omitted:
            message += f' {full_txt_omitted} pages did not fit into {LLMS_FULL_TXT_KEY}, run with more memory.'
        if result.existing_llms_txt_url:
            message += f' Note: the site already publishes {result.existing_llms_txt_url}.'
        logger.info(message)
        await Actor.set_status_message(message)

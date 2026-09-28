"""Entry point of the /llms.txt generator Actor."""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from apify import Actor

from src.builder import build_llms_data, entries_in_llms_order
from src.crawler import CrawlSettings, run_crawler
from src.curation import DEFAULT_MODEL, curate_with_ai
from src.renderer import render_llms_full_txt, render_llms_txt

if TYPE_CHECKING:
    from apify import ProxyConfiguration

    from src.mytypes import CrawlResult

logger = logging.getLogger('apify')

LLMS_TXT_KEY = 'llms.txt'
LLMS_FULL_TXT_KEY = 'llms-full.txt'
TEXT_CONTENT_TYPE = 'text/plain; charset=utf-8'
# time kept at the end of the run for building and saving the output
FINISH_RESERVE = timedelta(seconds=30)
AI_RESERVE = timedelta(seconds=120)


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


def explain_empty_result(result: CrawlResult) -> str:
    """Explains why no pages besides the start page were found."""
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
        max_crawl_depth = max(0, int(actor_input.get('maxCrawlDepth', 1)))
        max_crawl_pages = max(1, int(actor_input.get('maxCrawlPages', 50)))
        exclude_url_globs = [glob for glob in actor_input.get('excludeUrlGlobs') or [] if isinstance(glob, str)]
        generate_full_txt = bool(actor_input.get('generateLlmsFullTxt', True))
        link_to_markdown = bool(actor_input.get('linkToMarkdown', True))
        use_ai = bool(actor_input.get('aiCuration', False))
        ai_model = actor_input.get('aiModel') or DEFAULT_MODEL

        deadline = None
        if Actor.is_at_home() and Actor.configuration.timeout_at:
            deadline = Actor.configuration.timeout_at - FINISH_RESERVE - (AI_RESERVE if use_ai else timedelta(0))

        await Actor.set_status_message(f'Crawling {start_url}...')
        result = await run_crawler(
            CrawlSettings(
                start_url=start_url,
                max_crawl_depth=max_crawl_depth,
                max_crawl_pages=max_crawl_pages,
                exclude_url_globs=exclude_url_globs,
                collect_markdown=generate_full_txt,
                proxy=await get_proxy(actor_input.get('proxyConfiguration')),
                deadline=deadline,
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
                    )
                except Exception as exc:
                    logger.warning(f'AI curation failed, using the structure based on URL paths: {exc}')

        store = await Actor.open_key_value_store()
        llms_txt = render_llms_txt(data)
        await store.set_value(LLMS_TXT_KEY, llms_txt, content_type=TEXT_CONTENT_TYPE)
        output: dict[str, Any] = {
            'url': start_url,
            LLMS_TXT_KEY: llms_txt,
            'llmsTxtUrl': await store.get_public_url(LLMS_TXT_KEY),
            'llmsFullTxtUrl': None,
            'pagesCrawled': len(result.pages),
            'linksInLlmsTxt': sum(len(section['links']) for section in data['sections']),
            'existingLlmsTxtUrl': result.existing_llms_txt_url,
        }

        if generate_full_txt:
            ordered_entries = entries_in_llms_order(data, entries, link_to_markdown=link_to_markdown)
            full_txt = render_llms_full_txt(data, start_page['markdown'] if start_page else None, ordered_entries)
            await store.set_value(LLMS_FULL_TXT_KEY, full_txt, content_type=TEXT_CONTENT_TYPE)
            output['llmsFullTxtUrl'] = await store.get_public_url(LLMS_FULL_TXT_KEY)
            logger.info(f'Saved "{LLMS_FULL_TXT_KEY}" ({len(full_txt):,} characters).')

        await Actor.push_data(output)

        message = (
            f'Generated llms.txt with {output["linksInLlmsTxt"]} links from {len(result.pages)} crawled pages '
            f'in {crawl_secs:.0f} s.'
        )
        if result.existing_llms_txt_url:
            message += f' Note: the site already publishes {result.existing_llms_txt_url}.'
        logger.info(message)
        await Actor.set_status_message(message)

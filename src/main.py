"""This module defines the main entry point for the llsm.txt generator actor."""

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from apify import Actor

from .helpers import (
    clean_llms_data,
    get_crawler_actor_config,
    get_description_from_kvstore,
    get_url_path_dir,
    is_description_suitable,
    normalize_url,
)
from .renderer import render_llms_txt

if TYPE_CHECKING:
    from src.mytypes import LLMSData

logger = logging.getLogger('apify')

# minimum for the llms.txt generator to process the results
MIN_GENERATOR_RUN_SECS = 60
LOG_POLL_INTERVAL_SECS = 5
SECTION_MIN_LINKS = 2


async def main() -> None:
    """Main entry point for the llms.txt generator actor."""
    async with Actor:
        actor_input = await Actor.get_input()
        url = actor_input.get('startUrl')
        if url is None:
            msg = 'Missing "startUrl" attribute in input!'
            raise ValueError(msg)
        url_normalized = normalize_url(url)

        max_crawl_depth = int(actor_input.get('maxCrawlDepth', 1))
        max_crawl_pages = int(actor_input.get('maxCrawlPages', 50))
        crawler_type = actor_input.get('crawlerType', 'playwright:adaptive')

        if run_id := Actor.config.actor_run_id:
            if not (run := await Actor.apify_client.run(run_id).get()):
                msg = 'Failed to get the actor run details!'
                raise RuntimeError(msg)

            if not (timeout_secs := run.get('options', {}).get('timeoutSecs')):
                msg = 'Missing "timeoutSecs" attribute in actor run details!'
                raise ValueError(msg)

            # crawler timeout is set to timeout - MIN_GENERATOR_RUN_SECS or timeout if tha time is too low
            timeout_crawler = timedelta(
                seconds=(
                    timeout_secs - MIN_GENERATOR_RUN_SECS
                    if timeout_secs >= MIN_GENERATOR_RUN_SECS * 2
                    else timeout_secs
                )
            )
        # if run is local, do not set the timeout
        else:
            logger.warning('Running the actor locally, not setting the crawler timeout!')
            timeout_crawler = None

        # call apify/website-content-crawler actor to get the html content
        logger.info(f'Starting the "apify/website-content-crawler" actor for URL: {url}')
        await Actor.set_status_message('Starting the crawler...')
        actor_run_details = await Actor.call(
            'apify/website-content-crawler',
            get_crawler_actor_config(
                url, max_crawl_depth=max_crawl_depth, max_crawl_pages=max_crawl_pages, crawler_type=crawler_type
            ),
            # memory limit for the crawler actor so free tier can use this actor
            memory_mbytes=4096,
            wait=timedelta(seconds=LOG_POLL_INTERVAL_SECS),
            timeout=timeout_crawler,
        )
        if actor_run_details is None:
            msg = 'Failed to start the "apify/website-content-crawler" actor!'
            raise RuntimeError(msg)

        run_client = Actor.apify_client.run(actor_run_details.id)
        last_status_msg = None
        while (run := await run_client.get()) and run.get('status') == 'RUNNING':
            status_msg = run.get('statusMessage')
            if status_msg != last_status_msg:
                logger.info(f'Crawler status: {status_msg}')
                if status_msg is not None:
                    await Actor.set_status_message(status_msg)
                last_status_msg = status_msg
            await asyncio.sleep(LOG_POLL_INTERVAL_SECS)

        if not (run := await run_client.wait_for_finish()):
            msg = 'Failed to get the "apify/website-content-crawler" actor run details!'
            raise RuntimeError(msg)
        status_msg = run.get('statusMessage')
        logger.info(f'Crawler status: {status_msg}')
        await Actor.set_status_message('Crawler finished! Processing the results...')

        run_store = run_client.key_value_store()
        run_dataset = run_client.dataset()

        hostname = urlparse(url).hostname
        root_title = hostname

        data: LLMSData = {'title': root_title, 'description': None, 'details': None, 'sections': {}}
        sections = data['sections']

        is_dataset_empty = True
        async for item in run_dataset.iterate_items():
            is_dataset_empty = False
            item_url = item.get('url')
            logger.info(f'Processing page: {item_url}')
            if item_url is None:
                logger.warning('Missing "url" attribute in dataset item!')
                continue
            html_url = item.get('htmlUrl')
            if html_url is None:
                logger.warning('Missing "htmlUrl" attribute in dataset item!')
                continue

            # handle input root url separately
            is_root = normalize_url(item_url) == url_normalized
            if is_root:
                description = await get_description_from_kvstore(run_store, html_url)
                data['description'] = description if is_description_suitable(description) else None
                continue

            metadata = item.get('metadata', {})
            description = metadata.get('description')
            title = metadata.get('title')

            # extract description from HTML, crawler might not have extracted it
            if description is None:
                description = await get_description_from_kvstore(run_store, html_url)

            if not is_description_suitable(description):
                description = None

            section_dir = get_url_path_dir(item_url)
            section_title = section_dir
            if section_title not in sections:
                sections[section_dir] = {'title': section_title, 'links': []}

            sections[section_dir]['links'].append({'url': item_url, 'title': title, 'description': description})

        if is_dataset_empty:
            msg = (
                'No pages were crawled successfully!'
                ' Please check the "apify/website-content-crawler" actor run for more details.'
            )
            raise RuntimeError(msg)

        # move sections with less than SECTION_MIN_LINKS to the root
        clean_llms_data(data)
        output = render_llms_txt(data)

        # save into kv-store as a file to be able to download it
        store = await Actor.open_key_value_store()
        await store.set_value('llms.txt', output)
        logger.info('Saved the "llms.txt" file into the key-value store!')

        await Actor.push_data({'llms.txt': output})
        logger.info('Pushed the "llms.txt" file to the dataset!')

        await Actor.set_status_message('Finished! Saved the "llms.txt" file into the key-value store and dataset...')

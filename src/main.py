"""This module defines the main entry point for the Apify Actor.

To build Apify Actors, utilize the Apify SDK toolkit, read more at the official documentation:
https://docs.apify.com/sdk/python
"""

# Apify SDK - A toolkit for building Apify Actors. Read more at:
# https://docs.apify.com/sdk/python
import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from apify import Actor

from .helpers import get_crawler_actor_config, get_description_from_kvstore, is_description_suitable, render_llms_txt

if TYPE_CHECKING:
    from src.types import SectionDict


async def main() -> None:
    """Main entry point for the Apify Actor.

    This coroutine is executed using `asyncio.run()`, so it must remain an asynchronous function for proper execution.
    Asynchronous execution is required for communication with Apify platform, and it also enhances performance in
    the field of web scraping significantly.
    """
    async with Actor:
        actor_input = await Actor.get_input() or {'url': 'https://docs.apify.com/'}
        url = actor_input.get('url')
        if url is None:
            raise ValueError('Missing "url" attribute in input!')

        max_crawl_depth = int(actor_input.get('maxCrawlDepth', 1))

        # call apify/website-content-crawler actor to get the html content
        actor_run_details = await Actor.call(
            'apify/website-content-crawler',
            get_crawler_actor_config(url, max_crawl_depth=max_crawl_depth),
            memory_mbytes=4096,
        )
        if actor_run_details is None:
            raise ValueError('Failed to start the "apify/website-content-crawler" actor!')

        run_client = Actor.apify_client.run(actor_run_details.id)
        run_store = run_client.key_value_store()
        run_dataset = run_client.dataset()

        hostname = urlparse(url).hostname
        root_title = hostname

        data = {'title': root_title, 'description': None, 'sections': []}
        # add all pages to index section for now
        # TODO: use path or LLM suggestions to group pages into sections # noqa: TD003
        section: SectionDict = {'title': 'Index', 'links': []}

        async for item in run_dataset.iterate_items():
            item_url = item.get('url')
            if item_url is None:
                logging.warning('Missing "url" attribute in dataset item!')
                continue
            html_url = item.get('htmlUrl')
            if html_url is None:
                logging.warning('Missing "htmlUrl" attribute in dataset item!')
                continue

            is_root = item_url == url
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

            section['links'].append({'url': item_url, 'title': title, 'description': description})

        if section['links']:
            data['sections'].append(section)

        output = render_llms_txt(data)

        # save into kv-store as a file to be able to download it
        store = await Actor.open_key_value_store()
        await store.set_value('llms.txt', output)

        await Actor.push_data({'llms.txt': output})

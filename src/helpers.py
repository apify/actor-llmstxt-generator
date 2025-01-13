from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import bs4
from bs4.element import NavigableString

from src.crawler_config import CRAWLER_CONFIG
from src.renderer import render

if TYPE_CHECKING:
    from apify_client.clients import KeyValueStoreClientAsync

logger = logging.getLogger('apify')


def get_hostname_path_string_from_url(url: str) -> str:
    """Extracts the hostname and path from the URL."""
    parsed_url = urlparse(url)
    if parsed_url.hostname is None or parsed_url.path is None:
        return url
    return f'{parsed_url.hostname}{parsed_url.path}'


def is_description_suitable(description: str | None) -> bool:
    """Checks if the description is suitable for the `llms.txt` file.

    Currently only cheks if the description does not contain newlines.
    This was created because of the https://docs.apify.com/api/v2.
    The page that contains whole MD document in the meta tag description.
    """
    if description is None:
        return False
    return '\n' not in description


async def get_description_from_kvstore(kvstore: KeyValueStoreClientAsync, html_url: str) -> str | None:
    """Extracts the description from the HTML content stored in the KV store."""
    store_id = html_url.split('records/')[-1]
    if not (record := await kvstore.get_record(store_id)):
        logger.warning(f'Failed to get record with id "{store_id}"!')
        return None
    if not (html := record.get('value')) or not isinstance(html, str):
        logger.warning(f'Invalid HTML content for record with id "{store_id}"!')
        return None

    return get_description_from_html(html)


def render_llms_txt(data: dict) -> str:
    """Renders the `llms.txt` file using the provided data."""
    return render(data)


def get_crawler_actor_config(url: str, max_crawl_depth: int = 1) -> dict:
    """Creates actor input configuration for the `apify/website-content-crawler` actor."""
    config = CRAWLER_CONFIG
    config['startUrls'] = [{'url': url, 'method': 'GET'}]
    config['maxCrawlDepth'] = max_crawl_depth

    return config


def get_description_from_html(html: str) -> None | str:
    """Extracts the description from the HTML content.

    Uses meta 'description' or 'Description' from the html.
    """
    soup = bs4.BeautifulSoup(html, 'html.parser')
    description = soup.find('meta', {'name': 'description'})
    if description is None:
        description = soup.find('meta', {'name': 'Description'})

    if description is None:
        return None

    if isinstance(description, NavigableString):
        return description.getText()

    content = description.get('content')
    if isinstance(content, list):
        return ''.join(content)

    return content

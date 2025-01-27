from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import bs4
from bs4.element import NavigableString

from src.crawler_config import CRAWLER_CONFIG

if TYPE_CHECKING:
    from apify_client.clients import KeyValueStoreClientAsync

    from src.mytypes import LLMSData

# not using Actor.log because pytest then throws a warning
# about non existent event loop
logger = logging.getLogger('apify')


def clean_llms_data(data: LLMSData, section_min_links: int = 2) -> None:
    """Cleans the LLMS data by removing sections with low link count and moving the links to the index section.

    :param data: LLMS data to clean
    :param section_min_links: Minimum number of links in a section to keep it
    and not move the links to the index section
    """
    to_remove_sections: set[str] = set()

    if 'sections' not in data:
        raise ValueError('Missing "sections" attribute in the LLMS data!')

    sections = data['sections']

    for section_dir, section in sections.items():
        # skip the index section
        if section_dir == '/':
            continue
        if len(section['links']) < section_min_links:
            to_remove_sections.add(section_dir)

    if to_remove_sections:
        if '/' not in sections:
            sections['/'] = {'title': 'Index', 'links': []}
        for section_dir in to_remove_sections:
            sections['/']['links'].extend(sections[section_dir]['links'])
            del sections[section_dir]


def get_url_path_dir(url: str) -> str:
    """Get the directory path from the URL."""
    url_normalized = normalize_url(url)
    parsed_url = urlparse(url_normalized)
    return parsed_url.path.rsplit('/', 1)[0] or '/'


def normalize_url(url: str) -> str:
    """Normalizes the URL by removing trailing slash."""
    parsed_url = urlparse(url)
    normalized = parsed_url._replace(path=parsed_url.path.rstrip('/'))
    return normalized.geturl()


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


def get_crawler_actor_config(
    url: str, max_crawl_depth: int = 1, max_crawl_pages: int = 50, crawler_type: str = 'playwright:adaptive'
) -> dict:
    """Creates actor input configuration for the `apify/website-content-crawler` actor."""
    config = CRAWLER_CONFIG
    config['startUrls'] = [{'url': url, 'method': 'GET'}]
    config['maxCrawlDepth'] = max_crawl_depth
    config['maxCrawlPages'] = max_crawl_pages
    config['crawlerType'] = crawler_type

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

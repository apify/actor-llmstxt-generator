from __future__ import annotations

import re
import zlib
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from bs4.element import Tag
from markdownify import MarkdownConverter

if TYPE_CHECKING:
    import bs4

# elements that are never part of the main content
_NOISE_TAGS = ['script', 'style', 'noscript', 'template', 'svg', 'nav', 'header', 'footer', 'aside', 'form', 'iframe']
# selectors of the main content, in order of preference
_MAIN_SELECTORS = ['main article', 'article', 'main', '[role=main]', '#content', '.content', 'body']
_MANY_NEWLINES_RE = re.compile(r'\n{3,}')
# converts the already parsed tree: `markdownify(str(tag))` would serialize it and parse it again with the slow
# pure-Python `html.parser`
_CONVERTER = MarkdownConverter(heading_style='ATX', bullets='-')


def is_markdown_response(content_type: str, body: str) -> bool:
    """Checks that a response to a Markdown URL is Markdown and not an HTML error or fallback page."""
    content_type = content_type.lower()
    if content_type and 'markdown' not in content_type and 'text/plain' not in content_type:
        return False
    stripped = body.lstrip('﻿ \t\r\n')
    return bool(stripped) and not stripped.startswith('<')


def pack_markdown(markdown: str | None) -> bytes | None:
    """Compresses page Markdown kept in memory until `llms-full.txt` is written.

    Every crawled page's content is held until the end of the crawl, because `llms-full.txt` follows the order of
    `llms.txt`, which is only known then. Docs Markdown compresses ~4-5x, which keeps a few thousand pages well
    within the memory of a small run.
    """
    return zlib.compress(markdown.encode('utf-8'), 6) if markdown else None


def unpack_markdown(packed: bytes | None) -> str | None:
    """Reverses `pack_markdown`."""
    return zlib.decompress(packed).decode('utf-8') if packed else None


def html_to_markdown(soup: bs4.BeautifulSoup, base_url: str) -> str | None:
    """Converts the main content of the page to Markdown.

    Modifies the soup (removes noise elements and makes link URLs absolute) instead of working on a copy: copying
    the tree took about a third of the conversion time, and CPU is the bottleneck of small Apify runs.
    """
    content = next((tag for selector in _MAIN_SELECTORS if isinstance(tag := soup.select_one(selector), Tag)), None)
    if content is None:
        return None
    for tag in content.find_all([*_NOISE_TAGS, 'img']):
        if isinstance(tag, Tag):
            tag.decompose()
    for anchor in content.find_all('a', href=True):
        href = anchor.get('href')
        if isinstance(href, str):
            anchor['href'] = urljoin(base_url, href)
    markdown = _CONVERTER.convert_soup(content)
    markdown = _MANY_NEWLINES_RE.sub('\n\n', markdown).strip()
    return markdown or None

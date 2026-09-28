from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from bs4.element import Tag
from markdownify import markdownify

if TYPE_CHECKING:
    import bs4

# elements that are never part of the main content
_NOISE_TAGS = ['script', 'style', 'noscript', 'template', 'svg', 'nav', 'header', 'footer', 'aside', 'form', 'iframe']
# selectors of the main content, in order of preference
_MAIN_SELECTORS = ['main article', 'article', 'main', '[role=main]', '#content', '.content', 'body']
_MANY_NEWLINES_RE = re.compile(r'\n{3,}')


def is_markdown_response(content_type: str, body: str) -> bool:
    """Checks that a response to a Markdown URL is Markdown and not an HTML error or fallback page."""
    content_type = content_type.lower()
    if content_type and 'markdown' not in content_type and 'text/plain' not in content_type:
        return False
    stripped = body.lstrip('﻿ \t\r\n')
    return bool(stripped) and not stripped.startswith('<')


def html_to_markdown(soup: bs4.BeautifulSoup, base_url: str) -> str | None:
    """Converts the main content of the page to Markdown. Does not modify the soup."""
    main = next((tag for selector in _MAIN_SELECTORS if isinstance(tag := soup.select_one(selector), Tag)), None)
    if main is None:
        return None
    content = copy.copy(main)
    for tag in content.find_all([*_NOISE_TAGS, 'img']):
        if isinstance(tag, Tag):
            tag.decompose()
    for anchor in content.find_all('a', href=True):
        href = anchor.get('href')
        if isinstance(href, str):
            anchor['href'] = urljoin(base_url, href)
    markdown = markdownify(str(content), heading_style='ATX', bullets='-')
    markdown = _MANY_NEWLINES_RE.sub('\n\n', markdown).strip()
    return markdown or None

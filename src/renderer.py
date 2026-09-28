from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.builder import PageEntry
    from src.mytypes import LLMSData

_WHITESPACE_RE = re.compile(r'\s+')
_LEADING_H1_RE = re.compile(r'\A\s*#\s+[^\n]*\n+')


def _one_line(text: str) -> str:
    return _WHITESPACE_RE.sub(' ', text).strip()


def escape_link_text(text: str) -> str:
    """Makes the text safe to use inside `[...]` of a Markdown link."""
    return _one_line(text).replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]')


def escape_link_url(url: str) -> str:
    """Makes the URL safe to use inside `(...)` of a Markdown link."""
    return url.strip().replace(' ', '%20').replace('(', '%28').replace(')', '%29')


def render_llms_txt(data: LLMSData) -> str:
    """Generates the `llms.txt` file from the provided data.

    Example output:
    # Example

    > Example description

    Example details

    ## Section 1

    - [Example](https://example.com): Example description

    ## Optional

    - [Changelog](https://example.com/changelog)
    """
    result = [f'# {_one_line(data["title"])}\n\n']

    if description := data.get('description'):
        result.append(f'> {_one_line(description)}\n\n')

    if details := data.get('details'):
        result.append(f'{details.strip()}\n\n')

    for section in data.get('sections', []):
        if not section['links']:
            continue
        result.append(f'## {_one_line(section["title"])}\n\n')
        for link in section['links']:
            link_str = f'- [{escape_link_text(link["title"])}]({escape_link_url(link["url"])})'
            if link_description := link.get('description'):
                link_str += f': {_one_line(link_description)}'
            result.append(f'{link_str}\n')
        result.append('\n')

    return ''.join(result).rstrip('\n') + '\n'


def render_llms_full_txt(data: LLMSData, start_markdown: str | None, entries: list[PageEntry]) -> str:
    """Generates the `llms-full.txt` file: the content of the start page and all `entries` (in the given order)."""
    result = [f'# {_one_line(data["title"])}\n\n']
    if description := data.get('description'):
        result.append(f'> {_one_line(description)}\n\n')
    if start_markdown:
        result.append(f'{_LEADING_H1_RE.sub("", start_markdown.strip(), count=1).strip()}\n\n')

    for entry in entries:
        markdown = entry.page['markdown']
        if not markdown:
            continue
        body = _LEADING_H1_RE.sub('', markdown.strip(), count=1).strip()
        result.append(f'---\n\n# {_one_line(entry.title)}\n\nSource: {entry.page["url"]}\n\n{body}\n\n')

    return ''.join(result).rstrip('\n') + '\n'

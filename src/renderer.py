from __future__ import annotations

import re
from typing import TYPE_CHECKING

from src.markdown import unpack_markdown

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


def render_llms_full_txt(
    data: LLMSData, start_markdown: str | None, entries: list[PageEntry], *, max_bytes: int | None = None
) -> tuple[bytes, int]:
    """Generates the UTF-8 encoded `llms-full.txt` file: the content of the start page and all `entries` in order.

    Pages are decompressed and appended one at a time into a single buffer, so peak memory is about twice the size
    of the output (the buffer and the final `bytes` copy that storage clients require) instead of several copies of
    every page. When `max_bytes` is set, pages that would make the file
    larger are left out (they are the last ones in `llms.txt` order, i.e. Optional pages first) and a note says so.

    Returns the file content and the number of pages left out.
    """
    output = bytearray(f'# {_one_line(data["title"])}\n\n'.encode())
    if description := data.get('description'):
        output += f'> {_one_line(description)}\n\n'.encode()
    if start_markdown:
        output += f'{_LEADING_H1_RE.sub("", start_markdown.strip(), count=1).strip()}\n\n'.encode()

    omitted = 0
    for entry in entries:
        markdown = unpack_markdown(entry.page['markdown'])
        if not markdown:
            continue
        if omitted:
            omitted += 1
            continue
        body = _LEADING_H1_RE.sub('', markdown.strip(), count=1).strip()
        part = f'---\n\n# {_one_line(entry.title)}\n\nSource: {entry.page["url"]}\n\n{body}\n\n'.encode()
        if max_bytes is not None and len(output) + len(part) > max_bytes:
            omitted += 1
            continue
        output += part

    while output.endswith(b'\n'):
        output.pop()
    output += b'\n'
    if omitted:
        output += (
            f'\n---\n\n{omitted} more pages were left out because this file reached the size limit of '
            f'{(max_bytes or 0) / 1_000_000:.0f} MB for this run.\n'
        ).encode()
    return bytes(output), omitted

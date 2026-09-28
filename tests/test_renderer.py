from __future__ import annotations

from typing import TYPE_CHECKING

from src.builder import PageEntry
from src.renderer import escape_link_text, escape_link_url, render_llms_full_txt, render_llms_txt

if TYPE_CHECKING:
    from src.mytypes import CrawledPage, LLMSData

ACADEMY_LINK = {
    'url': 'https://docs.apify.com/academy',
    'title': 'Web Scraping Academy',
    'description': 'Learn everything about web scraping.',
}


def test_render_llms_txt() -> None:
    data: LLMSData = {
        'title': 'Apify Documentation',
        'details': None,
        'description': None,
        'sections': [{'title': 'Docs', 'links': [ACADEMY_LINK]}],  # type: ignore[list-item]
    }

    expected_output = """# Apify Documentation

## Docs

- [Web Scraping Academy](https://docs.apify.com/academy): Learn everything about web scraping.
"""

    assert render_llms_txt(data) == expected_output


def test_render_llms_txt_with_description_and_details() -> None:
    data: LLMSData = {
        'title': 'Apify Documentation',
        'description': 'Apify documentation',
        'details': 'This is the documentation for Apify',
        'sections': [{'title': 'Docs', 'links': [ACADEMY_LINK]}],  # type: ignore[list-item]
    }

    expected_output = """# Apify Documentation

> Apify documentation

This is the documentation for Apify

## Docs

- [Web Scraping Academy](https://docs.apify.com/academy): Learn everything about web scraping.
"""

    assert render_llms_txt(data) == expected_output


def test_render_llms_txt_with_no_sections() -> None:
    data: LLMSData = {
        'title': 'Apify Documentation',
        'description': 'Apify documentation',
        'details': None,
        'sections': [],
    }

    expected_output = """# Apify Documentation

> Apify documentation
"""

    assert render_llms_txt(data) == expected_output


def test_render_llms_txt_keeps_section_order_and_skips_empty_sections() -> None:
    data: LLMSData = {
        'title': 'Example',
        'description': None,
        'details': None,
        'sections': [
            {'title': 'Guides', 'links': [{'url': 'https://e.com/g', 'title': 'Guide', 'description': None}]},
            {'title': 'Empty', 'links': []},
            {'title': 'Optional', 'links': [{'url': 'https://e.com/c', 'title': 'Changelog', 'description': None}]},
        ],
    }

    expected_output = """# Example

## Guides

- [Guide](https://e.com/g)

## Optional

- [Changelog](https://e.com/c)
"""

    assert render_llms_txt(data) == expected_output


def test_render_llms_txt_escapes_markdown() -> None:
    data: LLMSData = {
        'title': 'Example\nsite',
        'description': 'Multi\nline',
        'details': None,
        'sections': [
            {
                'title': 'A\nB',
                'links': [{'url': 'https://e.com/a b(1)', 'title': 'Arrays [deprecated]\nfoo', 'description': 'd\ne'}],
            }
        ],
    }

    expected_output = """# Example site

> Multi line

## A B

- [Arrays \\[deprecated\\] foo](https://e.com/a%20b%281%29): d e
"""

    assert render_llms_txt(data) == expected_output


def test_escape_helpers() -> None:
    assert escape_link_text('  a [b]  c ') == 'a \\[b\\] c'
    assert escape_link_url(' https://e.com/x y ') == 'https://e.com/x%20y'


def _page(url: str, title: str, markdown: str | None) -> CrawledPage:
    return {
        'url': url,
        'key': url,
        'title': title,
        'html_title': None,
        'description': None,
        'markdown_url': None,
        'markdown': markdown,
        'outlinks': [],
        'depth': 1,
    }


def test_render_llms_full_txt() -> None:
    data: LLMSData = {'title': 'Example', 'description': 'An example site.', 'details': None, 'sections': []}
    entries = [
        PageEntry(_page('https://e.com/a', 'Page A', '# Page A\n\nContent A'), 'Page A', None, 1, '/', optional=False),
        PageEntry(_page('https://e.com/b', 'Page B', None), 'Page B', None, 2, '/', optional=False),
        PageEntry(_page('https://e.com/c', 'Page C', 'Content C'), 'Page C', None, 3, '/', optional=True),
    ]

    expected_output = """# Example

> An example site.

Start content

---

# Page A

Source: https://e.com/a

Content A

---

# Page C

Source: https://e.com/c

Content C
"""

    assert render_llms_full_txt(data, '# Example\n\nStart content', entries) == expected_output

import random

from src.builder import build_llms_data, entries_in_llms_order, get_navigation_order
from src.helpers import url_key
from src.mytypes import CrawledPage, CrawlResult
from src.renderer import render_llms_txt

BASE = 'https://docs.example.com/cli/docs'
SITE_DESCRIPTION = 'An introduction to Example CLI.'


def page(
    path: str,
    title: str,
    description: str | None = None,
    outlinks: list[str] | None = None,
    *,
    html_title: str | None = None,
    markdown_url: str | None = None,
) -> CrawledPage:
    url = f'{BASE}{path}'
    return {
        'url': url,
        'key': url_key(url),
        'title': title,
        'html_title': html_title or f'{title} | CLI | Example Docs',
        'description': description,
        'markdown_url': markdown_url,
        'markdown': None,
        'outlinks': [url_key(f'{BASE}{link}') for link in outlinks or []],
        'depth': 0 if not path else 1,
    }


def make_result(*, shuffle_seed: int | None = None) -> CrawlResult:
    nav = ['/quick-start', '/installation', '/1.9', '/next', '/guides/deploy', '/guides/secrets', '/changelog', '/vars']
    pages = [
        page('', 'Example CLI overview', SITE_DESCRIPTION, nav),
        page('/quick-start', 'Quick start', 'Create and run your first Actor.'),
        page(
            '/installation',
            'Installation',
            'Install the CLI with Homebrew or npm.',
            markdown_url=f'{BASE}/installation.md',
        ),
        # old docs versions: exact duplicates of the start page
        page('/1.9', 'Example CLI overview', SITE_DESCRIPTION),
        page('/next', 'Example CLI overview', SITE_DESCRIPTION),
        page('/guides/deploy', 'Deploy', SITE_DESCRIPTION),
        page('/guides/secrets', 'Secrets', 'Keep secrets out of your source code.'),
        page('/changelog', 'Changelog', 'All notable changes.'),
        page('/vars', 'Environment variables', 'Define environment variables.'),
    ]
    if shuffle_seed is not None:
        random.Random(shuffle_seed).shuffle(pages)
    return CrawlResult(start_url=BASE, loaded_url=BASE, pages=pages, start_page_link_count=len(nav))


def test_build_llms_data() -> None:
    data, start_page, entries = build_llms_data(make_result(), link_to_markdown=True)
    assert start_page is not None
    assert start_page['url'] == BASE
    assert len(entries) == 6  # duplicates of the start page are dropped

    expected_output = """# Example CLI overview | CLI | Example Docs

> An introduction to Example CLI.

## Docs

- [Quick start](https://docs.example.com/cli/docs/quick-start): Create and run your first Actor.
- [Installation](https://docs.example.com/cli/docs/installation.md): Install the CLI with Homebrew or npm.
- [Environment variables](https://docs.example.com/cli/docs/vars): Define environment variables.

## Guides

- [Deploy](https://docs.example.com/cli/docs/guides/deploy)
- [Secrets](https://docs.example.com/cli/docs/guides/secrets): Keep secrets out of your source code.

## Optional

- [Changelog](https://docs.example.com/cli/docs/changelog): All notable changes.
"""
    assert render_llms_txt(data) == expected_output


def test_build_llms_data_is_deterministic() -> None:
    outputs = {render_llms_txt(build_llms_data(make_result(shuffle_seed=seed))[0]) for seed in range(5)}
    assert len(outputs) == 1


def test_link_to_original_pages() -> None:
    data, _, _ = build_llms_data(make_result(), link_to_markdown=False)
    urls = [link['url'] for section in data['sections'] for link in section['links']]
    assert f'{BASE}/installation' in urls
    assert not any(url.endswith('.md') for url in urls)


def test_small_sections_are_merged_into_main_section() -> None:
    result = CrawlResult(
        start_url=BASE,
        pages=[
            page('', 'Home', None, ['/a', '/deep/b']),
            page('/a', 'A'),
            page('/deep/b', 'B'),
        ],
    )
    data, _, _ = build_llms_data(result)
    assert [section['title'] for section in data['sections']] == ['Docs']
    assert [link['title'] for link in data['sections'][0]['links']] == ['A', 'B']


def test_entries_in_llms_order() -> None:
    data, _, entries = build_llms_data(make_result(), link_to_markdown=True)
    ordered = entries_in_llms_order(data, entries, link_to_markdown=True)
    assert [entry.title for entry in ordered] == [
        'Quick start',
        'Installation',
        'Environment variables',
        'Deploy',
        'Secrets',
        'Changelog',
    ]


def test_get_navigation_order() -> None:
    pages = [page('', 'Home', None, ['/b', '/a']), page('/a', 'A', None, ['/c']), page('/b', 'B'), page('/c', 'C')]
    order = get_navigation_order(pages, url_key(BASE))
    assert sorted(order, key=order.__getitem__) == [url_key(f'{BASE}{p}') for p in ['', '/b', '/a', '/c']]

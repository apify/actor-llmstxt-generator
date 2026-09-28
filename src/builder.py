"""Turns crawled pages into the `llms.txt` structure: ordering, deduplication, sections and the Optional section."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from src.helpers import (
    compute_scope,
    find_common_title_suffixes,
    get_url_path,
    get_url_path_dir,
    humanize_path_segment,
    is_optional_url,
    strip_title_suffix,
    url_key,
)

if TYPE_CHECKING:
    from src.mytypes import CrawledPage, CrawlResult, LinkDict, LLMSData, SectionDict

# sections with fewer links are merged into the main section
SECTION_MIN_LINKS = 2
# a description shared by at least this many pages is site-wide boilerplate, not a page summary
BOILERPLATE_DESCRIPTION_MIN_PAGES = 3
OPTIONAL_SECTION_TITLE = 'Optional'


@dataclass
class PageEntry:
    """A page selected for the `llms.txt` file."""

    page: CrawledPage
    title: str
    description: str | None
    # position in the site navigation order (breadth-first from the start page)
    order: int
    section_dir: str
    optional: bool

    def to_link(self, *, link_to_markdown: bool) -> LinkDict:
        """Returns the link rendered in `llms.txt`."""
        url = self.page['markdown_url'] if link_to_markdown and self.page['markdown_url'] else self.page['url']
        return {'url': url, 'title': self.title, 'description': self.description}


def get_navigation_order(pages: list[CrawledPage], start_key: str) -> dict[str, int]:
    """Orders pages breadth-first by the order of links on the pages, starting at the start page.

    This follows the site navigation (menus, sidebars) and does not depend on the order in which
    the concurrent crawler finished the requests, so the output is deterministic.
    """
    by_key = {page['key']: page for page in pages}
    order: dict[str, int] = {}
    queue = deque([start_key])
    while queue:
        key = queue.popleft()
        if key in order or key not in by_key:
            continue
        order[key] = len(order)
        queue.extend(by_key[key]['outlinks'])
    for page in pages:
        order.setdefault(page['key'], len(order))
    return order


def get_site_title(start_page: CrawledPage | None, start_url: str) -> str:
    """Returns the H1 title of the `llms.txt` file: the <title> of the start page, its heading or the hostname."""
    if start_page:
        title = start_page['html_title'] or start_page['title']
        if title:
            return title
    return urlparse(start_url).hostname or start_url


def select_pages(result: CrawlResult) -> tuple[CrawledPage | None, list[PageEntry]]:
    """Selects pages for the `llms.txt` file in navigation order.

    Drops duplicates (same title and description, e.g. old docs versions of the same page), strips the common
    site-name suffix from titles and removes site-wide boilerplate descriptions.
    """
    start_keys = {url_key(result.start_url)}
    if result.loaded_url:
        start_keys.add(url_key(result.loaded_url))
    start_page = next((page for page in result.pages if page['key'] in start_keys), None)
    order = get_navigation_order(result.pages, start_page['key'] if start_page else url_key(result.start_url))
    pages = sorted(result.pages, key=lambda page: order[page['key']])

    suffixes = find_common_title_suffixes(page['html_title'] or page['title'] for page in pages)
    description_counts = Counter(page['description'] for page in pages if page['description'])
    start_description = start_page['description'] if start_page else None
    _, path_prefix = compute_scope(result.start_url, result.loaded_url)

    seen: set[tuple[str, str | None]] = set()
    if start_page:
        seen.add((strip_title_suffix(start_page['title'], suffixes).lower(), start_description))

    entries: list[PageEntry] = []
    for page in pages:
        if page is start_page:
            continue
        title = strip_title_suffix(page['title'], suffixes)
        description = page['description']
        signature = (title.lower(), description)
        if signature in seen:
            continue
        seen.add(signature)
        if description and (
            description == start_description or description_counts[description] >= BOILERPLATE_DESCRIPTION_MIN_PAGES
        ):
            description = None
        entries.append(
            PageEntry(
                page=page,
                title=title,
                description=description,
                order=order[page['key']],
                section_dir=get_url_path_dir(page['url']),
                optional=is_optional_url(page['url'], path_prefix),
            )
        )
    return start_page, entries


def _main_section_title(start_url: str) -> str:
    return 'Docs' if 'doc' in start_url.lower() else 'Pages'


def build_sections(
    entries: list[PageEntry], start_url: str, loaded_url: str | None, *, link_to_markdown: bool
) -> list[SectionDict]:
    """Groups the pages into sections by URL directory, in navigation order, with the Optional section last."""
    _, path_prefix = compute_scope(start_url, loaded_url)
    main_dirs = {path_prefix or '/', (path_prefix.rsplit('/', 1)[0] or '/') if path_prefix else '/'}
    title_by_path = {get_url_path(entry.page['url']): entry.title for entry in entries}

    groups: dict[str, list[PageEntry]] = {}
    optional: list[PageEntry] = []
    for entry in entries:
        if entry.optional:
            optional.append(entry)
            continue
        section_dir = '' if entry.section_dir in main_dirs else entry.section_dir
        groups.setdefault(section_dir, []).append(entry)

    # merge small sections into the main one
    for section_dir in [d for d, group in groups.items() if d and len(group) < SECTION_MIN_LINKS]:
        groups.setdefault('', []).extend(groups.pop(section_dir))

    sections: dict[str, tuple[int, list[PageEntry]]] = {}
    for section_dir, group in groups.items():
        if section_dir:
            title = title_by_path.get(section_dir) or humanize_path_segment(section_dir.rsplit('/', 1)[-1])
        else:
            title = _main_section_title(start_url)
        # sections with the same title are merged
        first_order, existing = sections.get(title, (len(entries), []))
        merged = existing + group
        sections[title] = (min(first_order, *(entry.order for entry in group)), merged)

    result: list[SectionDict] = []
    main_title = _main_section_title(start_url)
    ordered = sorted(sections.items(), key=lambda item: (item[0] != main_title, item[1][0]))
    for title, (_, group) in ordered:
        group.sort(key=lambda entry: entry.order)
        result.append({'title': title, 'links': [entry.to_link(link_to_markdown=link_to_markdown) for entry in group]})
    if optional:
        optional.sort(key=lambda entry: entry.order)
        result.append(
            {
                'title': OPTIONAL_SECTION_TITLE,
                'links': [entry.to_link(link_to_markdown=link_to_markdown) for entry in optional],
            }
        )
    return result


def entries_in_llms_order(data: LLMSData, entries: list[PageEntry], *, link_to_markdown: bool) -> list[PageEntry]:
    """Returns the entries in the order of the links in the `llms.txt` data (pages not linked are left out)."""
    by_url = {entry.to_link(link_to_markdown=link_to_markdown)['url']: entry for entry in entries}
    ordered: list[PageEntry] = []
    for section in data['sections']:
        for link in section['links']:
            entry = by_url.pop(link['url'], None)
            if entry is not None:
                ordered.append(entry)
    return ordered


def build_llms_data(
    result: CrawlResult, *, link_to_markdown: bool = True
) -> tuple[LLMSData, CrawledPage | None, list[PageEntry]]:
    """Builds the `llms.txt` data from the crawl result."""
    start_page, entries = select_pages(result)
    data: LLMSData = {
        'title': get_site_title(start_page, result.start_url),
        'description': start_page['description'] if start_page else None,
        'details': None,
        'sections': build_sections(entries, result.start_url, result.loaded_url, link_to_markdown=link_to_markdown),
    }
    return data, start_page, entries

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict


class LinkDict(TypedDict):
    """Dictionary representing a single link in the `llms.txt` file."""

    url: str
    title: str
    description: str | None


class SectionDict(TypedDict):
    """Dictionary representing a single section in the `llms.txt` file."""

    title: str
    links: list[LinkDict]


class LLMSData(TypedDict):
    """Dictionary representing the data structure of the `llms.txt` file.

    Sections are rendered in the given order.
    """

    title: str
    description: str | None
    details: str | None
    sections: list[SectionDict]


class CrawledPage(TypedDict):
    """Dictionary representing a single crawled page."""

    # URL as requested (before redirects)
    url: str
    # normalized key of `url`, used for deduplication and link graph
    key: str
    title: str
    # content of the <title> tag, used for site name and title suffix detection
    html_title: str | None
    description: str | None
    # URL of the Markdown version of the page advertised via <link rel="alternate" type="text/markdown">
    markdown_url: str | None
    # Markdown content of the page, only collected when generating `llms-full.txt`
    markdown: str | None
    # normalized keys of in-scope links found on the page, in document order
    outlinks: list[str]
    depth: int


@dataclass
class CrawlResult:
    """Result of the crawl."""

    start_url: str
    # URL of the start page after redirects
    loaded_url: str | None = None
    pages: list[CrawledPage] = field(default_factory=list)
    # number of <a href> links found on the start page (0 usually means a JavaScript-rendered page)
    start_page_link_count: int = 0
    # URL of an `llms.txt` the site already publishes, if any
    existing_llms_txt_url: str | None = None

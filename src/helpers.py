from __future__ import annotations

import html
import logging
import re
from collections import Counter
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING
from urllib.parse import urlparse, urlunparse

import bs4
from bs4.element import Comment, NavigableString, Tag

if TYPE_CHECKING:
    from collections.abc import Iterable

# not using Actor.log because pytest then throws a warning
# about non existent event loop
logger = logging.getLogger('apify')

# Real SEO meta descriptions are ~50-160 characters (search engines truncate around 160).
# Anything much longer is almost certainly a dumped document, not a page summary.
DESCRIPTION_MAX_LENGTH = 300

_HTML_TAG_RE = re.compile(r'</?[a-zA-Z][^<>]*>')
_MD_LINK_RE = re.compile(r'\[([^\[\]]*)\]\([^()\s]*\)')
_WHITESPACE_RE = re.compile(r'\s+')
_PARAGRAPH_BREAK_RE = re.compile(r'\n[ \t]*\n')
# Markdown block markers at the start of a line: blockquote, heading, list item, numbered item, code fence, table row
_MD_BLOCK_LINE_RE = re.compile(r'^[ \t]*(?:>|#{1,6}(?:[ \t]|$)|[-*+][ \t]|\d{1,2}[.)][ \t]|```|~~~|\|)', re.MULTILINE)

# separators used between the page name and the site name in <title>, e.g. "Install | CLI | Apify Documentation"
_TITLE_SEPARATOR_RE = re.compile(r'\s+(?:\||-|–|—|·|•|::)\s+')  # noqa: RUF001
# text of permalink anchors that docs generators put inside headings
_PERMALINK_TEXTS = {'', '#', '¶', '§', '🔗', chr(0x200B)}  # chr(0x200B) is a zero-width space

# links with these extensions are not HTML pages
_NON_HTML_EXTENSIONS = frozenset(
    [
        '7z',
        'avi',
        'avif',
        'bz2',
        'css',
        'csv',
        'dmg',
        'doc',
        'docx',
        'epub',
        'exe',
        'gif',
        'gz',
        'ico',
        'ipynb',
        'jpeg',
        'jpg',
        'js',
        'json',
        'md',
        'mjs',
        'mov',
        'mp3',
        'mp4',
        'msi',
        'ogg',
        'otf',
        'pdf',
        'png',
        'ppt',
        'pptx',
        'rar',
        'rss',
        'svg',
        'tar',
        'tgz',
        'ttf',
        'txt',
        'wasm',
        'wav',
        'webm',
        'webp',
        'whl',
        'woff',
        'woff2',
        'xls',
        'xlsx',
        'xml',
        'yaml',
        'yml',
        'zip',
    ]
)

# path segments of pages that are useful but secondary, they go to the "Optional" section
_OPTIONAL_SEGMENTS = frozenset(
    [
        'archive',
        'archived',
        'blog',
        'careers',
        'changelog',
        'changes',
        'community',
        'cookie-policy',
        'cookies',
        'deprecated',
        'jobs',
        'legal',
        'news',
        'press',
        'privacy',
        'privacy-policy',
        'release-notes',
        'releases',
        'terms',
        'terms-of-service',
        'terms-of-use',
        'tos',
    ]
)
# path segments of versioned docs snapshots, e.g. /docs/1.9/, /docs/v2.3/, /docs/next/
_VERSION_SEGMENT_RE = re.compile(r'^(?:v?\d+(?:\.\d+)+(?:\.x)?|next|canary|nightly|unreleased)$')
# a trailing filename extension, e.g. "index.html" -- alphabetic only, so version segments like "v1.2" don't match
_FILE_EXTENSION_RE = re.compile(r'\.[a-zA-Z]{1,10}$')


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------


def normalize_url(url: str) -> str:
    """Normalizes the URL by removing trailing slash."""
    parsed_url = urlparse(url)
    normalized = parsed_url._replace(path=parsed_url.path.rstrip('/'))
    return normalized.geturl()


def url_key(url: str) -> str:
    """Returns a normalized key of the URL used for deduplication.

    Lowercases the scheme and host, drops the fragment, default ports and the trailing slash. Keeps the query.
    """
    parsed = urlparse(url.strip())
    host = (parsed.hostname or '').lower()
    if parsed.port and not (
        (parsed.scheme == 'http' and parsed.port == 80) or (parsed.scheme == 'https' and parsed.port == 443)  # noqa: PLR2004
    ):
        host = f'{host}:{parsed.port}'
    path = parsed.path.rstrip('/')
    return urlunparse((parsed.scheme.lower(), host, path, '', parsed.query, ''))


def get_url_path(url: str) -> str:
    """Get the path from the URL."""
    url_normalized = normalize_url(url)
    parsed_url = urlparse(url_normalized)
    return parsed_url.path or '/'


def get_url_path_dir(url: str) -> str:
    """Get the directory path from the URL."""
    url_normalized = normalize_url(url)
    parsed_url = urlparse(url_normalized)
    return parsed_url.path.rsplit('/', 1)[0] or '/'


def get_scope_path(url: str) -> str:
    """Returns the path prefix a crawl started at `url` is limited to (without trailing slash, '' for the root).

    A start URL pointing to a file such as `/docs/index.html` is scoped to its directory. A last segment is only
    treated as a filename when its extension is alphabetic (`.html`, `.htm`, ...) so version-like segments such
    as `/docs/v1.2` are not mistaken for one.
    """
    path = urlparse(url).path
    last_segment = path.rstrip('/').rsplit('/', 1)[-1]
    if not path.endswith('/') and _FILE_EXTENSION_RE.search(last_segment):
        path = path.rsplit('/', 1)[0]
    return path.rstrip('/')


def compute_scope(start_url: str, loaded_url: str | None) -> tuple[frozenset[str], str]:
    """Computes hosts and path prefix of the crawl.

    Follows a redirect of the start URL: when the start page redirects to a different path (e.g.
    `docs.pydantic.dev/latest/` -> `pydantic.dev/docs/validation/latest/get-started/`), the crawl is scoped to
    the directory of the final page, so its siblings are included.
    """
    start = urlparse(start_url)
    hosts = {(start.hostname or '').lower()}
    prefix = get_scope_path(start_url)
    if loaded_url:
        loaded = urlparse(loaded_url)
        hosts.add((loaded.hostname or '').lower())
        loaded_prefix = get_scope_path(loaded_url)
        if loaded_prefix != prefix or loaded.hostname != start.hostname:
            prefix = loaded_prefix if loaded_prefix == prefix else loaded_prefix.rsplit('/', 1)[0]
    return frozenset(hosts), prefix


def is_in_scope(url: str, hosts: frozenset[str], path_prefix: str) -> bool:
    """Checks that the URL is an http(s) URL on one of the hosts and under the path prefix.

    Matches whole path segments: prefix `/docs` matches `/docs` and `/docs/x` but not `/docs-old`.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {'http', 'https'} or (parsed.hostname or '').lower() not in hosts:
        return False
    path = parsed.path.rstrip('/')
    return not path_prefix or path == path_prefix or path.startswith(path_prefix + '/')


def looks_like_non_html(url: str) -> bool:
    """Checks if the URL points to a file that is not an HTML page, based on its extension."""
    last_segment = urlparse(url).path.rsplit('/', 1)[-1]
    if '.' not in last_segment:
        return False
    return last_segment.rsplit('.', 1)[-1].lower() in _NON_HTML_EXTENSIONS


def matches_any_glob(url: str, patterns: Iterable[str]) -> bool:
    """Checks if the URL matches any of the glob patterns (`*` matches anything including `/`)."""
    return any(fnmatchcase(url, pattern) for pattern in patterns)


def is_optional_url(url: str, path_prefix: str) -> bool:
    """Checks if the page is secondary (changelog, blog, legal, old docs version, ...) based on its URL path.

    Only path segments below the crawl path prefix are considered, so a crawl started at `/blog` is not
    all optional.
    """
    path = urlparse(url).path.rstrip('/')
    if path_prefix and (path == path_prefix or path.startswith(path_prefix + '/')):
        path = path[len(path_prefix) :]
    segments = [segment.lower() for segment in path.split('/') if segment]
    return any(segment in _OPTIONAL_SEGMENTS or _VERSION_SEGMENT_RE.match(segment) for segment in segments)


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------


def clean_text(text: str | None) -> str | None:
    """Collapses whitespace and strips the text. Returns `None` for empty text."""
    if text is None:
        return None
    text = _WHITESPACE_RE.sub(' ', text).strip()
    return text or None


def get_heading_text(tag: Tag) -> str | None:
    """Returns clean text of a heading, without permalink anchors (`#`, `¶`) and with spaces between elements."""
    text = ''
    for element in tag.descendants:
        if not isinstance(element, NavigableString) or isinstance(element, Comment):
            continue
        parent_anchor = element.find_parent('a')
        if parent_anchor is not None and parent_anchor.get_text().strip() in _PERMALINK_TEXTS:
            continue
        part = str(element)
        # separate words from adjacent elements so "Agent<span>Beta</span>" becomes "Agent Beta"
        if text and part and text[-1].isalnum() and part[0].isalnum():
            text += ' '
        text += part
    return clean_text(text)


def get_h1_from_html(html_content: str) -> str | None:
    """Extracts the first h1 tag from the HTML content."""
    soup = bs4.BeautifulSoup(html_content, 'html.parser')
    return get_h1_from_soup(soup)


def get_h1_from_soup(soup: bs4.BeautifulSoup) -> str | None:
    """Extracts the text of the first h1 tag from the BeautifulSoup object."""
    h1 = soup.find('h1')
    return get_heading_text(h1) if isinstance(h1, Tag) else None


def get_meta_content(soup: bs4.BeautifulSoup, *, name: str | None = None, prop: str | None = None) -> str | None:
    """Returns the content of a <meta name=...> or <meta property=...> tag."""
    attrs: dict[str, str] = {'name': name} if name else {'property': prop or ''}
    meta = soup.find('meta', attrs=attrs)
    if not isinstance(meta, Tag):
        return None
    content = meta.get('content')
    if isinstance(content, list):
        content = ' '.join(content)
    return clean_text(content)


def get_html_title(soup: bs4.BeautifulSoup) -> str | None:
    """Returns clean text of the <title> tag."""
    return clean_text(soup.title.get_text()) if soup.title else None


def get_page_title(soup: bs4.BeautifulSoup) -> str | None:
    """Returns the page title: first <h1>, then og:title, then <title>."""
    return get_h1_from_soup(soup) or get_meta_content(soup, prop='og:title') or get_html_title(soup)


def find_common_title_suffixes(titles: Iterable[str | None], min_share: float = 0.3, min_count: int = 3) -> list[str]:
    """Finds site-name suffixes shared by many titles, e.g. " | Supabase Docs".

    Returns the suffixes (including the leading separator) ordered from the longest to the shortest.
    """
    counts: Counter[str] = Counter()
    total = 0
    for title in titles:
        if not title:
            continue
        total += 1
        counts.update({title[match.start() :] for match in _TITLE_SEPARATOR_RE.finditer(title)})
    threshold = max(min_count, min_share * total)
    return sorted((suffix for suffix, count in counts.items() if count >= threshold), key=len, reverse=True)


def strip_title_suffix(title: str, suffixes: Iterable[str]) -> str:
    """Strips the first (longest) matching site-name suffix from the title."""
    for suffix in suffixes:
        if title.endswith(suffix) and len(title) > len(suffix):
            return title[: -len(suffix)].strip()
    return title


def humanize_path_segment(segment: str) -> str:
    """Turns a URL path segment into a title, e.g. `getting-started` -> `Getting started`."""
    words = re.sub(r'[-_]+', ' ', segment).strip()
    return words[:1].upper() + words[1:] if words else segment


# ---------------------------------------------------------------------------
# Descriptions
# ---------------------------------------------------------------------------


def clean_description(description: str | None) -> str | None:
    r"""Normalizes a raw meta description into a single plain-text line.

    Unescapes HTML entities, strips HTML tags (some generators leak markup such as
    `<span id=...></span>` into the meta content), replaces complete Markdown links
    with their text and collapses all whitespace (including `\r\n`) into single spaces.
    Returns `None` if nothing is left.
    """
    if description is None:
        return None
    text = html.unescape(description)
    text = _HTML_TAG_RE.sub('', text)
    text = _MD_LINK_RE.sub(r'\1', text)
    text = _WHITESPACE_RE.sub(' ', text).strip()
    return text or None


def is_description_suitable(description: str | None) -> bool:
    """Checks if the raw meta description is a real page summary suitable for the `llms.txt` file.

    Some sites put a whole Markdown document (or a truncated piece of one) into
    `<meta name="description">`, e.g. https://docs.apify.com/api/v2. Such a description is rejected when:
    - it spans multiple paragraphs,
    - any of its lines starts with a Markdown block marker (blockquote, heading, list item, code fence, table row),
    - after cleaning it is longer than `DESCRIPTION_MAX_LENGTH`,
    - after cleaning it still contains Markdown link syntax or unbalanced brackets (e.g. a link cut in half).

    A single line break inside otherwise normal text is fine, it is collapsed by `clean_description`.
    """
    if description is None or not description.strip():
        return False

    normalized = description.replace('\r\n', '\n').replace('\r', '\n')
    if _PARAGRAPH_BREAK_RE.search(normalized):
        return False
    if _MD_BLOCK_LINE_RE.search(normalized):
        return False

    cleaned = clean_description(normalized)
    if cleaned is None or len(cleaned) > DESCRIPTION_MAX_LENGTH:
        return False
    return '](' not in cleaned and cleaned.count('[') == cleaned.count(']')


def get_suitable_description(description: str | None) -> str | None:
    """Returns the cleaned description if it is suitable for the `llms.txt` file, otherwise `None`."""
    return clean_description(description) if is_description_suitable(description) else None


def get_description_from_html(html_content: str) -> None | str:
    """Extracts the description from the HTML content.

    Uses meta 'description' or 'Description' from the html.
    """
    soup = bs4.BeautifulSoup(html_content, 'html.parser')
    return get_description_from_soup(soup)


def get_description_from_soup(soup: bs4.BeautifulSoup) -> None | str:
    """Extracts the raw description from the BeautifulSoup object.

    Uses meta 'description' or 'Description' from the html.
    """
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

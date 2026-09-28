"""Keeps `.actor/input_schema.json` and `actor.json` consistent with the limits the code enforces."""

import json
import re
from pathlib import Path

from src.curation import DEFAULT_MODEL
from src.main import DEFAULT_MAX_CRAWL_PAGES, MAX_CRAWL_DEPTH, MAX_CRAWL_PAGES, normalize_start_url

ACTOR_DIR = Path(__file__).parent.parent / '.actor'
PROPERTIES = json.loads((ACTOR_DIR / 'input_schema.json').read_text())['properties']


def test_limits_match_the_code() -> None:
    assert PROPERTIES['maxCrawlPages']['maximum'] == MAX_CRAWL_PAGES
    assert PROPERTIES['maxCrawlPages']['default'] == DEFAULT_MAX_CRAWL_PAGES
    assert PROPERTIES['maxCrawlDepth']['maximum'] == MAX_CRAWL_DEPTH
    assert PROPERTIES['aiModel']['default'] == DEFAULT_MODEL
    assert DEFAULT_MODEL in PROPERTIES['aiModel']['enum']
    assert len(PROPERTIES['aiModel']['enum']) == len(PROPERTIES['aiModel']['enumTitles'])


def test_only_the_start_url_is_required_and_everything_else_is_advanced() -> None:
    schema = json.loads((ACTOR_DIR / 'input_schema.json').read_text())
    assert schema['required'] == ['startUrl']
    names = list(PROPERTIES)
    assert names[0] == 'startUrl'
    # the Advanced section starts right after the start URL and is the only section
    assert 'sectionCaption' in PROPERTIES[names[1]]
    assert [name for name in names if 'sectionCaption' in PROPERTIES[name]] == [names[1]]
    for name in names[1:]:
        assert 'default' in PROPERTIES[name], name


def test_start_url_pattern_accepts_what_the_code_accepts() -> None:
    pattern = re.compile(PROPERTIES['startUrl']['pattern'])
    for url in [
        'https://docs.apify.com/cli/docs',
        'docs.apify.com',
        ' http://example.com:8080/a?b=1#c ',
        'www.x.co.uk/',
    ]:
        assert pattern.match(url), url
        assert normalize_start_url(url).startswith(('http://', 'https://'))
    for url in [
        'localhost',
        'https://',
        'not a url',
        'ftp://x.com',
        'https://docs.apify.com/a b',
        'javascript:alert(1)',
    ]:
        assert not pattern.match(url), url


def test_dynamic_memory_expression() -> None:
    """Verified with `@apify/actor-memory-expression`: 256 MB up to 300 pages, 1 GB up to 1,500, 4 GB above.

    The expression language has no `<=` / `>=` (evaluation fails and the platform silently falls back to the
    Actor's default memory), so only `<` / `>` may be used.
    """
    expression = json.loads((ACTOR_DIR / 'actor.json').read_text())['defaultMemoryMbytes']
    assert f"get(input, 'maxCrawlPages', {DEFAULT_MAX_CRAWL_PAGES})" in expression
    assert '<=' not in expression
    assert '>=' not in expression

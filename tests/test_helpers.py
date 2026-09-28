from typing import TYPE_CHECKING

from src.helpers import (
    DESCRIPTION_MAX_LENGTH,
    clean_description,
    clean_llms_data,
    get_h1_from_html,
    get_hostname_path_string_from_url,
    get_section_dir_title,
    get_suitable_description,
    get_url_path,
    get_url_path_dir,
    is_description_suitable,
    normalize_url,
)

if TYPE_CHECKING:
    from src.mytypes import LLMSData


def test_get_section_dir_title() -> None:
    path_titles = {
        '/dir': 'Directory',
        '/dir/subdir': 'Subdirectory',
        '/dir/subdir/page': 'Page',
    }

    # Test case 1: Exact match in path_titles
    section_dir = '/dir/subdir'
    assert get_section_dir_title(section_dir, path_titles) == 'Subdirectory'

    # Test case 2: No exact match, but parent directory match
    section_dir2 = '/dir/subdir/page/subpage'
    assert get_section_dir_title(section_dir2, path_titles) == 'Page'

    # Test case 3: No match at all, should return the original section_dir
    section_dir3 = '/unknown/path'
    assert get_section_dir_title(section_dir3, path_titles) == '/unknown/path'

    # Test case 4: Root directory match
    section_dir4 = '/dir'
    assert get_section_dir_title(section_dir4, path_titles) == 'Directory'

    # Test case 5: Empty section_dir
    section_dir5 = ''
    assert get_section_dir_title(section_dir5, path_titles) == ''


def test_get_url_path() -> None:
    url = 'https://example.com/path'
    assert get_url_path(url) == '/path'

    url2 = 'https://example.com/path/'
    assert get_url_path(url2) == '/path'

    url3 = 'https://example.com/'
    assert get_url_path(url3) == '/'

    url4 = 'https://example.com/dir/page'
    assert get_url_path(url4) == '/dir/page'

    url5 = 'https://example.com'
    assert get_url_path(url5) == '/'


def test_get_h1_from_html() -> None:
    # single h1 tag
    html = '<h1>Example</h1>'
    assert get_h1_from_html(html) == 'Example'

    # multiple h1 tags
    # only the first one should be returned
    html2 = '<h1>Example</h1><h1>Example 2</h1>'
    assert get_h1_from_html(html2) == 'Example'

    # no h1 tags
    html3 = '<h2>Example</h2>'
    assert get_h1_from_html(html3) is None

    # nested h1 tag
    html4 = '<div><h1>Example</h1></div>'
    assert get_h1_from_html(html4) == 'Example'


def test_clean_llms_data() -> None:
    # Test case 1: Normal case where sections with fewer links are moved to index
    data: LLMSData = {
        'title': 'Test LLMS',
        'description': None,
        'details': None,
        'sections': {
            'section_1': {
                'title': 'Section 1',
                'links': [{'url': 'http://example.com', 'title': 'Example', 'description': None}],
            },
            'section_2': {
                'title': 'Section 2',
                'links': [
                    {'url': 'http://example2.com', 'title': 'Example 2', 'description': None},
                    {'url': 'http://example3.com', 'title': 'Example 3', 'description': None},
                ],
            },
        },
    }

    clean_llms_data(data, section_min_links=2)

    assert 'section_1' not in data['sections']  # Section 1 should be removed
    assert 'section_2' in data['sections']  # Section 2 should remain
    assert '/' in data['sections']  # Index section should be created
    assert len(data['sections']['/']['links']) == 1  # The link from section_1 should be moved to index

    # Test case 2: If all sections meet the minimum link count, nothing changes
    data2: LLMSData = {
        'title': 'Test LLMS',
        'description': None,
        'details': None,
        'sections': {
            'section_1': {
                'title': 'Section 1',
                'links': [
                    {'url': 'http://example.com', 'title': 'Example', 'description': None},
                    {'url': 'http://example2.com', 'title': 'Example 2', 'description': None},
                ],
            }
        },
    }

    clean_llms_data(data2, section_min_links=2)

    assert 'section_1' in data2['sections']  # Section 1 should remain
    assert '/' not in data2['sections']  # Index section should not be created

    # Test case 4: Empty sections dictionary
    data4: LLMSData = {'title': 'Empty LLMS', 'description': None, 'details': None, 'sections': {}}

    clean_llms_data(data4, section_min_links=2)

    assert data4['sections'] == {}  # Sections should remain empty

    # Test case 5: Sections already have an index section
    data5: LLMSData = {
        'title': 'LLMS with Index',
        'description': None,
        'details': None,
        'sections': {
            '/': {'title': 'Index', 'links': [{'url': 'http://index.com', 'title': 'Index Link', 'description': None}]},
            'section_1': {
                'title': 'Section 1',
                'links': [{'url': 'http://example.com', 'title': 'Example', 'description': None}],
            },
        },
    }

    clean_llms_data(data5, section_min_links=2)

    assert 'section_1' not in data5['sections']  # Section 1 should be removed
    assert '/' in data5['sections']  # Index should remain
    assert len(data5['sections']['/']['links']) == 2  # Index should now contain both the old and new links


def test_get_url_path_dir() -> None:
    url = 'https://example.com/dir/subdir/page'
    _dir = '/dir/subdir'
    assert get_url_path_dir(url) == _dir

    url2 = 'https://example.com/page'
    _dir2 = '/'
    assert get_url_path_dir(url2) == _dir2

    url3 = 'https://example.com/dir/page/'
    _dir3 = '/dir'
    assert get_url_path_dir(url3) == _dir3

    url4 = 'https://example.com'
    assert get_url_path_dir(url4) == '/'


def test_normalize_url() -> None:
    url = 'https://example.com/'
    url_normalized = 'https://example.com'
    assert normalize_url(url) == url_normalized

    url2 = 'https://example.com/dir/page'
    url2_normalized = 'https://example.com/dir/page'
    assert normalize_url(url2) == url2_normalized

    url3 = 'https://example.com/dir/page/'
    url3_normalized = 'https://example.com/dir/page'
    assert normalize_url(url3) == url3_normalized


def test_get_hostname_path_string_from_url() -> None:
    url = 'https://example.com/path'
    assert get_hostname_path_string_from_url(url) == 'example.com/path'

    url2 = 'https://example.com/path/'
    assert get_hostname_path_string_from_url(url2) == 'example.com/path/'


# Meta description of https://docs.apify.com/api/v2 as reported in issue #1 (whole Markdown document).
API_V2_ISSUE_DESCRIPTION = (
    '\r\n> **UPDATE 2024-07-09:**\r\n'
    '> We have rolled out this new Apify API Documentation. In case of any issues, please '
    '[report here](https://github.com/apify/openapi/issues).\r\n'
    '> The old API Documentation is still [available here](https://docs.apify.com/api/v2-old).\r\n'
    '\r\n'
    'The Apify API (version 2) provides programmatic access to the [Apify platform](https://docs.apify.com).'
)
# Live meta description of https://docs.apify.com/api/v2 as of 2026-09: a single line cut in the middle of a link.
API_V2_LIVE_DESCRIPTION = 'The Apify API (version 2) provides programmatic access to the [Apify'
# Live meta description of https://docs.apify.com/api/v2/actor-delete: leaked anchor markup before the text.
API_V2_ENDPOINT_DESCRIPTION = (
    "<span id='/reference/actors/actor-object/delete-actor'></span>"
    "<span id='/reference/actors/delete-actor'></span>"
    "<span id='tag/ActorsActor-object/operation/act_delete'></span>Deletes an Actor with the specified ID."
)


def test_is_description_suitable_accepts_normal_descriptions() -> None:
    assert is_description_suitable('Learn how to install Apify CLI using installation scripts, Homebrew, or NPM.')
    # a stray line break inside normal text is fine, it is collapsed later
    assert is_description_suitable('Learn how to install Apify CLI\nusing Homebrew or NPM.')
    assert is_description_suitable('Learn how to install Apify CLI\r\nusing Homebrew or NPM.')
    # things that only look like Markdown markers
    assert is_description_suitable('#1 rated web scraping platform.')
    assert is_description_suitable('2024. The year of AI agents.')
    assert is_description_suitable('Vercel Agent [Beta] investigates production issues.')
    assert is_description_suitable('Compare prices - fast and free.')
    # a complete Markdown link is flattened to its text by cleaning
    assert is_description_suitable(
        'The Apify API provides programmatic access to the [Apify platform](https://docs.apify.com).'
    )
    # HTML leaked into the meta content does not count towards the length
    assert is_description_suitable(API_V2_ENDPOINT_DESCRIPTION)


def test_is_description_suitable_rejects_markdown_documents() -> None:
    assert not is_description_suitable(API_V2_ISSUE_DESCRIPTION)
    # the same document minified into a single line
    minified = ' '.join(API_V2_ISSUE_DESCRIPTION.split())
    assert '\n' not in minified
    assert not is_description_suitable(minified)
    # truncated in the middle of a Markdown link (live docs.apify.com/api/v2)
    assert not is_description_suitable(API_V2_LIVE_DESCRIPTION)
    assert not is_description_suitable('# Apify API')
    assert not is_description_suitable('Endpoints:\n- Actors\n- Datasets')
    assert not is_description_suitable('Steps:\n1. Install\n2. Run')
    assert not is_description_suitable('Example:\n```\napify run\n```')
    assert not is_description_suitable('First paragraph.\n\nSecond paragraph.')
    assert not is_description_suitable('Broken link](https://example.com) here')


def test_is_description_suitable_rejects_empty_and_long() -> None:
    assert not is_description_suitable(None)
    assert not is_description_suitable('')
    assert not is_description_suitable(' \n\t ')
    assert is_description_suitable('a' * DESCRIPTION_MAX_LENGTH)
    assert not is_description_suitable('a' * (DESCRIPTION_MAX_LENGTH + 1))
    assert not is_description_suitable('word ' * 100)


def test_clean_description() -> None:
    assert clean_description(None) is None
    assert clean_description(' \n ') is None
    assert clean_description('  Learn how\r\n  to install\tthe CLI.  ') == 'Learn how to install the CLI.'
    assert clean_description('Tom &amp; Jerry &quot;docs&quot;') == 'Tom & Jerry "docs"'
    assert clean_description(API_V2_ENDPOINT_DESCRIPTION) == 'Deletes an Actor with the specified ID.'
    flattened = clean_description('Access to the [Apify platform](https://docs.apify.com).')
    assert flattened == 'Access to the Apify platform.'
    # comparison operators are not HTML tags
    assert clean_description('Use a < b > c filters.') == 'Use a < b > c filters.'


def test_get_suitable_description() -> None:
    assert get_suitable_description(API_V2_ISSUE_DESCRIPTION) is None
    assert get_suitable_description(API_V2_LIVE_DESCRIPTION) is None
    assert get_suitable_description(API_V2_ENDPOINT_DESCRIPTION) == 'Deletes an Actor with the specified ID.'
    assert get_suitable_description('Learn how\nto install.') == 'Learn how to install.'
    # the result of cleaning is still considered suitable (main.py re-checks it)
    cleaned = get_suitable_description('Learn about [Actors](https://docs.apify.com/actors).')
    assert cleaned == 'Learn about Actors.'
    assert is_description_suitable(cleaned)

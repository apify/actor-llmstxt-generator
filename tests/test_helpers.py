import bs4

from src.helpers import (
    DESCRIPTION_MAX_LENGTH,
    clean_description,
    compute_scope,
    find_common_title_suffixes,
    get_h1_from_html,
    get_page_title,
    get_scope_path,
    get_suitable_description,
    get_url_path,
    get_url_path_dir,
    humanize_path_segment,
    is_description_suitable,
    is_in_scope,
    is_optional_url,
    looks_like_non_html,
    matches_any_glob,
    normalize_url,
    strip_title_suffix,
    url_key,
)


def test_url_key() -> None:
    assert url_key('HTTPS://Example.com:443/Docs/#intro') == 'https://example.com/Docs'
    assert url_key('https://example.com/docs/?a=1#x') == 'https://example.com/docs?a=1'
    assert url_key('http://example.com:8080/') == 'http://example.com:8080'
    assert url_key('https://example.com/docs') == url_key('https://example.com/docs/')


def test_get_scope_path() -> None:
    assert get_scope_path('https://example.com') == ''
    assert get_scope_path('https://example.com/') == ''
    assert get_scope_path('https://example.com/docs/') == '/docs'
    assert get_scope_path('https://example.com/docs/index.html') == '/docs'
    assert get_scope_path('https://example.com/v1.2') == '/v1.2'


def test_compute_scope() -> None:
    # no redirect
    assert compute_scope('https://docs.apify.com/cli/docs', None) == (frozenset({'docs.apify.com'}), '/cli/docs')
    assert compute_scope('https://docs.apify.com/cli/docs', 'https://docs.apify.com/cli/docs/') == (
        frozenset({'docs.apify.com'}),
        '/cli/docs',
    )
    # scheme or host redirect keeps the path
    assert compute_scope('http://example.com/docs', 'https://www.example.com/docs') == (
        frozenset({'example.com', 'www.example.com'}),
        '/docs',
    )
    # redirect to a leaf page scopes the crawl to the directory of that page
    assert compute_scope(
        'https://docs.pydantic.dev/latest/', 'https://pydantic.dev/docs/validation/latest/get-started/'
    ) == (frozenset({'docs.pydantic.dev', 'pydantic.dev'}), '/docs/validation/latest')
    assert compute_scope('https://platform.openai.com/docs', 'https://developers.openai.com/api/docs') == (
        frozenset({'platform.openai.com', 'developers.openai.com'}),
        '/api',
    )


def test_is_in_scope() -> None:
    hosts = frozenset({'docs.apify.com'})
    assert is_in_scope('https://docs.apify.com/cli/docs', hosts, '/cli/docs')
    assert is_in_scope('https://docs.apify.com/cli/docs/installation', hosts, '/cli/docs')
    # whole path segments only
    assert not is_in_scope('https://docs.apify.com/cli/docs-old', hosts, '/cli/docs')
    assert not is_in_scope('https://docs.apify.com/sdk', hosts, '/cli/docs')
    # other hosts, including hosts that only start with the same string
    assert not is_in_scope('https://docs.apify.com.evil.com/cli/docs', hosts, '/cli/docs')
    assert not is_in_scope('https://apify.com/cli/docs', hosts, '/cli/docs')
    assert not is_in_scope('mailto:docs@apify.com', hosts, '')
    assert is_in_scope('https://docs.apify.com/anything', hosts, '')


def test_looks_like_non_html() -> None:
    assert looks_like_non_html('https://example.com/file.pdf')
    assert looks_like_non_html('https://example.com/llms.txt')
    assert looks_like_non_html('https://example.com/img/logo.PNG')
    assert not looks_like_non_html('https://example.com/docs/page')
    assert not looks_like_non_html('https://example.com/docs/page.html')
    assert not looks_like_non_html('https://example.com/docs/v1.2')


def test_matches_any_glob() -> None:
    assert matches_any_glob('https://example.com/docs/1.9/intro', ['*/docs/1.*'])
    assert not matches_any_glob('https://example.com/docs/intro', ['*/docs/1.*'])
    assert not matches_any_glob('https://example.com/docs/intro', [])


def test_is_optional_url() -> None:
    assert is_optional_url('https://docs.apify.com/cli/docs/changelog', '/cli/docs')
    assert is_optional_url('https://docs.apify.com/cli/docs/1.9', '/cli/docs')
    assert is_optional_url('https://docs.apify.com/cli/docs/next/installation', '/cli/docs')
    assert is_optional_url('https://example.com/blog/post', '')
    assert is_optional_url('https://example.com/legal/privacy', '')
    assert not is_optional_url('https://docs.apify.com/cli/docs/installation', '/cli/docs')
    # API version paths are not old docs versions
    assert not is_optional_url('https://docs.apify.com/api/v2/actors', '')
    # a crawl started inside the blog is not all optional
    assert not is_optional_url('https://example.com/blog/post', '/blog')


def test_get_page_title() -> None:
    html = """<html><head><title>Agent | Vercel Docs</title></head>
    <body><h1>
      Vercel Agent<span class="badge">Public Beta</span><a class="hash-link" href="#agent">#</a>
    </h1></body></html>"""
    assert get_page_title(bs4.BeautifulSoup(html, 'html.parser')) == 'Vercel Agent Public Beta'
    # h1 without <title> must be used (it used to be dropped because of an operator precedence bug)
    assert get_page_title(bs4.BeautifulSoup('<body><h1>Only heading</h1></body>', 'html.parser')) == 'Only heading'
    html_og = '<head><title>T | Site</title><meta property="og:title" content="OG title"></head>'
    assert get_page_title(bs4.BeautifulSoup(html_og, 'html.parser')) == 'OG title'
    assert get_page_title(bs4.BeautifulSoup('<head><title> T  | Site </title></head>', 'html.parser')) == 'T | Site'
    assert get_page_title(bs4.BeautifulSoup('<body></body>', 'html.parser')) is None
    # mkdocs permalink
    html_mkdocs = '<h1>Models<a class="headerlink" href="#models">¶</a></h1>'
    assert get_page_title(bs4.BeautifulSoup(html_mkdocs, 'html.parser')) == 'Models'
    assert get_page_title(bs4.BeautifulSoup('<h1>Hello <b>world</b>!</h1>', 'html.parser')) == 'Hello world!'


def test_title_suffixes() -> None:
    titles = [
        'Auth | Supabase Docs',
        'Storage | Supabase Docs',
        'Realtime | Supabase Docs',
        'Getting Started | Supabase Docs',
        'Self-Hosting Auth',
        None,
    ]
    suffixes = find_common_title_suffixes(titles)
    assert suffixes == [' | Supabase Docs']
    assert strip_title_suffix('Auth | Supabase Docs', suffixes) == 'Auth'
    assert strip_title_suffix('Self-Hosting Auth', suffixes) == 'Self-Hosting Auth'
    # the title is never stripped to nothing
    assert strip_title_suffix(' | Supabase Docs', suffixes) == ' | Supabase Docs'

    nested = [
        'Install | CLI | Apify Documentation',
        'Vars | CLI | Apify Documentation',
        'Ref | CLI | Apify Documentation',
    ]
    nested_suffixes = find_common_title_suffixes(nested)
    assert nested_suffixes[0] == ' | CLI | Apify Documentation'
    assert strip_title_suffix(nested[0], nested_suffixes) == 'Install'
    # too few titles to tell
    assert find_common_title_suffixes(['A | Site', 'B | Site']) == []


def test_humanize_path_segment() -> None:
    assert humanize_path_segment('getting-started') == 'Getting started'
    assert humanize_path_segment('api_reference') == 'Api reference'
    assert humanize_path_segment('') == ''


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

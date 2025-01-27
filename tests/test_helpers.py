from src.helpers import get_hostname_path_string_from_url, get_url_path_dir, normalize_url


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

from src.helpers import get_hostname_path_string_from_url, normalize_url


def test_normalize_url() -> None:
    url = 'https://example.com/'
    url_normalized = 'https://example.com'
    assert normalize_url(url) == url_normalized

def test_get_hostname_path_string_from_url() -> None:
    url = 'https://example.com/path'
    assert get_hostname_path_string_from_url(url) == 'example.com/path'

    url2 = 'https://example.com/path/'
    assert get_hostname_path_string_from_url(url2) == 'example.com/path/'

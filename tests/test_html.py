from src.helpers import get_description_from_html


def test_description_meta_tag() -> None:
    html = '<html><head><meta name="description" content="testdesc"></head><body></body></html>'
    assert get_description_from_html(html) == 'testdesc'


def test_description_meta_tag_with_capital_d() -> None:
    html = '<html><head><meta name="Description" content="testdec"></head><body></body></html>'
    assert get_description_from_html(html) == 'testdec'


def test_no_description_meta_tag() -> None:
    html = '<html><head></head><body></body></html>'
    assert get_description_from_html(html) is None

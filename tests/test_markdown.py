from bs4 import BeautifulSoup

from src.markdown import html_to_markdown, pack_markdown, unpack_markdown


def test_pack_markdown_round_trip() -> None:
    text = '# Title\n\nSome **content** with unicode: žluťoučký kůň.\n' * 100
    packed = pack_markdown(text)
    assert packed is not None
    assert len(packed) < len(text.encode()) / 4
    assert unpack_markdown(packed) == text
    assert pack_markdown(None) is None
    assert pack_markdown('') is None
    assert unpack_markdown(None) is None


def test_html_to_markdown() -> None:
    html = """<html><body><nav><a href="/x">Menu</a></nav><main><h1>Title</h1>
    <p>Hello <a href="page">link</a> <img src="a.png"></p><ul><li>one</li><li>two</li></ul>
    <script>alert(1)</script></main><footer>Footer</footer></body></html>"""
    markdown = html_to_markdown(BeautifulSoup(html, 'lxml'), base_url='https://e.com/docs/')
    assert markdown == '# Title\n\nHello [link](https://e.com/docs/page)\n\n- one\n- two'


def test_html_to_markdown_without_content() -> None:
    assert html_to_markdown(BeautifulSoup('', 'lxml'), base_url='https://e.com/') is None

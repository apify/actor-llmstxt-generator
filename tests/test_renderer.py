from typing import TYPE_CHECKING

from src.renderer import render_llms_txt

if TYPE_CHECKING:
    from src.mytypes import LLMSData


def test_render_llms_txt() -> None:
    data: LLMSData = {
        'title': 'docs.apify.com',
        'details': None,
        'description': None,
        'sections': {
           '/': {
               'title': 'Index',
               'links': [
                   {
                       'url': 'https://docs.apify.com/academy',
                       'title': 'Web Scraping Academy', 'description': 'Learn everything about web scraping.'
                   }
               ]
           }
        }
    }

    expected_output = """# docs.apify.com

## Index

- [Web Scraping Academy](https://docs.apify.com/academy): Learn everything about web scraping.

"""

    assert render_llms_txt(data) == expected_output

def test_render_llms_txt_with_description() -> None:
    data: LLMSData = {
        'title': 'docs.apify.com',
        'description': 'Apify documentation',
        'details': None,
        'sections': {
           '/': {
               'title': 'Index',
               'links': [
                   {
                       'url': 'https://docs.apify.com/academy',
                       'title': 'Web Scraping Academy', 'description': 'Learn everything about web scraping.'
                   }
               ]
           }
        }
    }

    expected_output = """# docs.apify.com

> Apify documentation

## Index

- [Web Scraping Academy](https://docs.apify.com/academy): Learn everything about web scraping.

"""

    assert render_llms_txt(data) == expected_output

def test_render_llms_txt_with_description_and_details() -> None:
    data: LLMSData = {
        'title': 'docs.apify.com',
        'description': 'Apify documentation',
        'details': 'This is the documentation for Apify',
        'sections': {
           '/': {
               'title': 'Index',
               'links': [
                   {
                       'url': 'https://docs.apify.com/academy',
                       'title': 'Web Scraping Academy', 'description': 'Learn everything about web scraping.'
                   }
               ]
           }
        }
    }

    expected_output = """# docs.apify.com

> Apify documentation

This is the documentation for Apify

## Index

- [Web Scraping Academy](https://docs.apify.com/academy): Learn everything about web scraping.

"""

    assert render_llms_txt(data) == expected_output

def test_render_llms_txt_with_no_sections() -> None:
    data: LLMSData = {
        'title': 'docs.apify.com',
        'description': 'Apify documentation',
        'details': None,
        'sections': {}
    }

    expected_output = """# docs.apify.com

> Apify documentation

"""

    assert render_llms_txt(data) == expected_output

def test_render_llms_txt_with_multiple_sections() -> None:
    data: LLMSData = {
        'title': 'docs.apify.com',
        'description': 'Apify documentation',
        'details': None,
        'sections': {
           '/': {
               'title': 'Index',
               'links': [
                   {
                       'url': 'https://docs.apify.com/academy',
                       'title': 'Web Scraping Academy', 'description': 'Learn everything about web scraping.'
                   }
               ]
           },
           '/guides': {
               'title': 'Guides',
               'links': [
                   {
                       'url': 'https://docs.apify.com/guides/getting-started',
                       'title': 'Getting Started', 'description': 'Learn how to get started with Apify.'
                   }
               ]
           }
        }
    }

    expected_output = """# docs.apify.com

> Apify documentation

## Index

- [Web Scraping Academy](https://docs.apify.com/academy): Learn everything about web scraping.

## Guides

- [Getting Started](https://docs.apify.com/guides/getting-started): Learn how to get started with Apify.

"""

    assert render_llms_txt(data) == expected_output

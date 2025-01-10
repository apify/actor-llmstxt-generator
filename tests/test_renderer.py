import unittest

from src.helpers import render_llms_txt


class RenderUnitTests(unittest.TestCase):
    def test_render_llms_txt(self) -> None:
        data = {
            'title': 'docs.apify.com',
            'sections': [
               {
                   'title': 'Index',
                   'links': [
                       {
                           'url': 'https://docs.apify.com/academy',
                           'title': 'Web Scraping Academy', 'description': 'Learn everything about web scraping.'
                       }
                   ]
               }
            ]
        }

        expected_output = """# docs.apify.com

## Index

- [Web Scraping Academy](https://docs.apify.com/academy): Learn everything about web scraping.
"""

        assert render_llms_txt(data) == expected_output

    def test_render_llms_txt_with_description(self) -> None:
        data = {
            'title': 'docs.apify.com',
            'description': 'Apify documentation',
            'sections': [
               {
                   'title': 'Index',
                   'links': [
                       {
                           'url': 'https://docs.apify.com/academy',
                           'title': 'Web Scraping Academy', 'description': 'Learn everything about web scraping.'
                       }
                   ]
               }
            ]
        }

        expected_output = """# docs.apify.com

> Apify documentation

## Index

- [Web Scraping Academy](https://docs.apify.com/academy): Learn everything about web scraping.
"""

        assert render_llms_txt(data) == expected_output

    def test_render_llms_txt_with_description_and_details(self) -> None:
        data = {
            'title': 'docs.apify.com',
            'description': 'Apify documentation',
            'details': 'This is the documentation for Apify',
            'sections': [
               {
                   'title': 'Index',
                   'links': [
                       {
                           'url': 'https://docs.apify.com/academy',
                           'title': 'Web Scraping Academy', 'description': 'Learn everything about web scraping.'
                       }
                   ]
               }
            ]
        }

        expected_output = """# docs.apify.com

> Apify documentation

This is the documentation for Apify

## Index

- [Web Scraping Academy](https://docs.apify.com/academy): Learn everything about web scraping.
"""

        assert render_llms_txt(data) == expected_output

    def test_render_llms_txt_with_no_sections(self) -> None:
        data = {
            'title': 'docs.apify.com',
            'description': 'Apify documentation',
        }

        expected_output = """# docs.apify.com

> Apify documentation

"""

        assert render_llms_txt(data) == expected_output

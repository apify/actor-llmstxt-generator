import pytest

from src.builder import build_llms_data
from src.curation import apply_curation, build_prompt, parse_response
from tests.test_builder import make_result


def test_build_prompt_lists_all_pages() -> None:
    data, _, entries = build_llms_data(make_result())
    prompt = build_prompt(data, entries, 'https://docs.example.com/cli/docs')
    for index, entry in enumerate(entries):
        assert f'{index} | {entry.title} |' in prompt


def test_parse_response() -> None:
    assert parse_response('{"title": "X"}') == {'title': 'X'}
    assert parse_response('```json\n{"title": "X"}\n```') == {'title': 'X'}
    with pytest.raises(TypeError):
        parse_response('[1, 2]')


def test_apply_curation() -> None:
    data, _, entries = build_llms_data(make_result(), link_to_markdown=False)
    titles = [entry.title for entry in entries]
    quick_start, installation, variables, deploy, secrets, changelog = (
        titles.index(t)
        for t in ['Quick start', 'Installation', 'Environment variables', 'Deploy', 'Secrets', 'Changelog']
    )
    curation = {
        'title': 'Example CLI',
        'summary': 'Command-line tool for Example.',
        'details': '',
        'sections': [
            {'title': 'Getting started', 'pages': [installation, quick_start, 999, 'x']},
            {'title': 'Guides', 'pages': [deploy, secrets, installation]},
            {'title': 'Optional', 'pages': [variables]},
            {'title': 'Empty', 'pages': []},
        ],
        'optional': [changelog],
        'exclude': [],
    }

    curated = apply_curation(data, entries, curation, link_to_markdown=False)

    assert curated['title'] == 'Example CLI'
    assert curated['description'] == 'Command-line tool for Example.'
    assert curated['details'] is None
    sections = {section['title']: [link['title'] for link in section['links']] for section in curated['sections']}
    # invalid indexes are ignored, each page is used once, the model cannot create its own Optional section
    assert sections == {
        'Getting started': ['Installation', 'Quick start'],
        'Guides': ['Deploy', 'Secrets'],
        # pages the model did not place are kept in Optional
        'Optional': ['Changelog', 'Environment variables'],
    }
    assert [section['title'] for section in curated['sections']][-1] == 'Optional'


def test_apply_curation_excludes_pages() -> None:
    data, _, entries = build_llms_data(make_result())
    curation = {'sections': [{'title': 'All', 'pages': [0]}], 'exclude': list(range(1, len(entries)))}
    curated = apply_curation(data, entries, curation, link_to_markdown=False)
    assert [section['title'] for section in curated['sections']] == ['All']
    # keeps the original title and description when the model does not return them
    assert curated['title'] == data['title']
    assert curated['description'] == data['description']


def test_apply_curation_without_sections_fails() -> None:
    data, _, entries = build_llms_data(make_result())
    with pytest.raises(ValueError, match='no usable sections'):
        apply_curation(data, entries, {'sections': 'nope'}, link_to_markdown=False)

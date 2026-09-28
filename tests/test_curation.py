import pytest

from src.builder import PageEntry, build_llms_data
from src.curation import BATCH_SIZE, _curate_in_batches, apply_curation, build_prompt, curate_with_ai, parse_response
from src.mytypes import CrawledPage, LLMSData
from tests.test_builder import make_result


def _synthetic_entries(count: int, *, optional_count: int = 0) -> list[PageEntry]:
    """Builds `count` bare-bones pages, the first `optional_count` of them flagged as heuristically optional."""
    entries = []
    for index in range(count):
        url = f'https://example.com/page-{index}'
        crawled: CrawledPage = {
            'url': url,
            'key': url,
            'title': f'Page {index}',
            'html_title': f'Page {index}',
            'description': None,
            'markdown_url': None,
            'markdown': None,
            'outlinks': [],
            'depth': 1,
        }
        entries.append(
            PageEntry(
                page=crawled,
                title=f'Page {index}',
                description=None,
                order=index,
                section_dir='/',
                optional=index < optional_count,
            )
        )
    return entries


_DATA: LLMSData = {'title': 'Big Site', 'description': 'A big site.', 'details': None, 'sections': []}


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


async def test_curate_with_ai_uses_single_call_at_or_below_batch_size(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = _synthetic_entries(BATCH_SIZE)
    calls: list[str] = []

    async def fake_call_model(_payload: object, _token: str, _timeout_secs: float, *, log_label: str) -> dict:
        calls.append(log_label)
        return {
            'title': 'Site',
            'summary': 'A site.',
            'details': '',
            'sections': [{'title': 'Docs', 'pages': list(range(10_000))}],
            'optional': [],
            'exclude': [],
        }

    monkeypatch.setattr('src.curation._call_model', fake_call_model)
    curated = await curate_with_ai(_DATA, entries, start_url='https://example.com', token='tok', link_to_markdown=False)

    assert calls == ['single call']
    assert [section['title'] for section in curated['sections']] == ['Docs']
    assert len(curated['sections'][0]['links']) == BATCH_SIZE


async def test_curate_with_ai_batches_above_batch_size(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = _synthetic_entries(BATCH_SIZE + 1)
    calls: list[str] = []

    async def fake_call_model(_payload: object, _token: str, _timeout_secs: float, *, log_label: str) -> dict:
        calls.append(log_label)
        if log_label == 'section merge':
            return {
                'title': 'Site',
                'summary': 'A site.',
                'details': '',
                'sections': ['Docs'],
                'mapping': {'docs': 'Docs'},
            }
        return {'sections': [{'title': 'docs', 'pages': list(range(10_000))}], 'optional': [], 'exclude': []}

    monkeypatch.setattr('src.curation._call_model', fake_call_model)
    curated = await curate_with_ai(_DATA, entries, start_url='https://example.com', token='tok', link_to_markdown=False)

    assert sorted(c for c in calls if c.startswith('batch ')) == ['batch 1/2', 'batch 2/2']
    assert calls.count('section merge') == 1
    assert [section['title'] for section in curated['sections']] == ['Docs']
    assert len(curated['sections'][0]['links']) == BATCH_SIZE + 1


async def test_curate_with_ai_scales_to_a_thousand_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """A large site never truncates or loses pages: every page ends up placed exactly once."""
    entries = _synthetic_entries(1000, optional_count=50)
    calls: list[str] = []

    async def fake_call_model(_payload: object, _token: str, _timeout_secs: float, *, log_label: str) -> dict:
        calls.append(log_label)
        if log_label == 'section merge':
            return {
                'title': 'Big Site',
                'summary': 'A big site.',
                'details': '',
                'sections': ['Docs'],
                'mapping': {'docs': 'Docs'},
            }
        return {'sections': [{'title': 'docs', 'pages': list(range(10_000))}], 'optional': [], 'exclude': []}

    monkeypatch.setattr('src.curation._call_model', fake_call_model)
    curated = await curate_with_ai(_DATA, entries, start_url='https://example.com', token='tok', link_to_markdown=False)

    batch_calls = [c for c in calls if c.startswith('batch ')]
    assert len(batch_calls) == -(-950 // BATCH_SIZE)  # ceil(950 main pages / BATCH_SIZE)
    assert calls.count('section merge') == 1
    assert [section['title'] for section in curated['sections']] == ['Docs', 'Optional']
    assert len(curated['sections'][0]['links']) == 950  # everything the model placed
    assert len(curated['sections'][1]['links']) == 50  # heuristically-optional pages, never sent to the model
    assert sum(len(section['links']) for section in curated['sections']) == len(entries)


async def test_curate_in_batches_merges_equivalent_section_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    """Section titles that differ only by batch phrasing are merged into one canonical section."""
    remainder = max(1, BATCH_SIZE // 2)
    entries = _synthetic_entries(BATCH_SIZE + remainder)  # forces exactly 2 batches: BATCH_SIZE + remainder

    async def fake_call_model(_payload: object, _token: str, _timeout_secs: float, *, log_label: str) -> dict:
        if log_label == 'batch 1/2':
            return {
                'sections': [{'title': 'Getting Started', 'pages': list(range(BATCH_SIZE))}],
                'optional': [],
                'exclude': [],
            }
        if log_label == 'batch 2/2':
            return {
                'sections': [{'title': 'getting started', 'pages': list(range(remainder))}],
                'optional': [],
                'exclude': [],
            }
        if log_label == 'section merge':
            return {
                'title': 'Site',
                'summary': 'A site.',
                'details': '',
                'sections': ['Getting started'],
                'mapping': {'Getting Started': 'Getting started'},
            }
        raise AssertionError(log_label)

    monkeypatch.setattr('src.curation._call_model', fake_call_model)
    curation = await _curate_in_batches(_DATA, entries, 'https://example.com', 'tok', 'model', 30)

    assert curation['sections'] == [{'title': 'Getting started', 'pages': list(range(len(entries)))}]

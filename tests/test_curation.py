import asyncio
import json
import re
import time
from datetime import UTC, datetime, timedelta

import pytest

from src.builder import PageEntry, build_llms_data
from src.curation import (
    ASSIGN_CHUNK_SIZE,
    BATCH_SIZE,
    MAX_CONCURRENT_CALLS,
    MAX_RESPONSE_TOKENS,
    MAX_SECTIONS,
    MIN_CALL_SECS,
    _assignment_items,
    _Budget,
    _curate_in_batches,
    _final_section_number,
    _TransientError,
    _UnusableResponseError,
    apply_curation,
    build_prompt,
    curate_with_ai,
    estimate_ai_seconds,
    parse_response,
)
from src.main import MAX_CRAWL_PAGES
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


def _numbered_lines(payload: dict) -> int:
    """Counts the `index | ...` lines (pages or section titles) in the user prompt of a chat payload."""
    return len([line for line in payload['messages'][1]['content'].splitlines() if re.match(r'\d+ \| ', line)])


def _proposed_sections(payload: dict) -> int:
    """Counts the proposed sections (`index | title | pages | paths` lines) in a section assignment prompt."""
    content = payload['messages'][1]['content']
    return len([line for line in content.splitlines() if re.match(r'\d+ \| .* \| \d+ \| ', line)])


def _fake_merge(payload: dict, log_label: str, final_titles: tuple[str, ...] = ('Docs',)) -> dict | None:
    """Answers the merge calls of `_curate_in_batches` (every proposed section goes to the first final section)."""
    if log_label == 'section merge':
        return {'title': 'Site', 'summary': 'A site.', 'details': '', 'sections': list(final_titles)}
    if log_label.startswith('section assignment'):
        return {'assign': {str(index): 0 for index in range(_proposed_sections(payload))}}
    return None


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

    async def fake_call_model(payload: dict, _token: str, _timeout_secs: float, *, log_label: str) -> dict:
        calls.append(log_label)
        if (answer := _fake_merge(payload, log_label)) is not None:
            return answer
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

    async def fake_call_model(payload: dict, _token: str, _timeout_secs: float, *, log_label: str) -> dict:
        calls.append(log_label)
        if (answer := _fake_merge(payload, log_label)) is not None:
            return answer
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

    async def fake_call_model(payload: dict, _token: str, _timeout_secs: float, *, log_label: str) -> dict:
        if (answer := _fake_merge(payload, log_label, ('Getting started',))) is not None:
            return answer
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
        raise AssertionError(log_label)

    monkeypatch.setattr('src.curation._call_model', fake_call_model)
    curation = await _curate_in_batches(_DATA, entries, 'https://example.com', 'tok', 'model', _Budget(None))

    assert curation['sections'] == [{'title': 'Getting started', 'pages': list(range(len(entries)))}]


async def test_curate_with_ai_stops_at_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """A slow model can't push the run past its deadline: the AI step raises `TimeoutError` at the deadline."""
    entries = _synthetic_entries(BATCH_SIZE * 3)
    timeouts: list[float] = []

    async def slow_call_model(_payload: object, _token: str, timeout_secs: float, *, log_label: str) -> dict:  # noqa: ARG001
        timeouts.append(timeout_secs)
        await asyncio.sleep(60)
        raise AssertionError('unreachable')

    monkeypatch.setattr('src.curation._call_model', slow_call_model)
    monkeypatch.setattr('src.curation.MIN_CALL_SECS', 0.5)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await curate_with_ai(
            _DATA,
            entries,
            start_url='https://example.com',
            token='tok',
            deadline=datetime.now(UTC) + timedelta(seconds=2),
        )
    assert time.monotonic() - started < 2.5
    # every call got a timeout capped by the time left, not the static CALL_TIMEOUT_SECS
    assert timeouts
    assert all(timeout <= 2 for timeout in timeouts)


async def test_curate_with_ai_skips_calls_without_time_left(monkeypatch: pytest.MonkeyPatch) -> None:
    async def call_model(*_args: object, **_kwargs: object) -> dict:
        raise AssertionError('no call should be started')

    monkeypatch.setattr('src.curation._call_model', call_model)
    with pytest.raises(TimeoutError):
        await curate_with_ai(
            _DATA,
            _synthetic_entries(5),
            start_url='https://example.com',
            token='tok',
            deadline=datetime.now(UTC) + timedelta(seconds=MIN_CALL_SECS - 1),
        )


async def test_curate_in_batches_gives_the_merge_calls_only_the_time_left(monkeypatch: pytest.MonkeyPatch) -> None:
    """The merge calls run after the batches, so their timeout is what is left, not another full call timeout."""
    timeouts: dict[str, float] = {}
    monkeypatch.setattr('src.curation.MIN_CALL_SECS', 0.1)

    async def call_model(payload: dict, _token: str, timeout_secs: float, *, log_label: str) -> dict:
        timeouts[log_label] = timeout_secs
        if (answer := _fake_merge(payload, log_label)) is not None:
            return answer
        await asyncio.sleep(1)
        return {'sections': [{'title': 'Docs', 'pages': list(range(BATCH_SIZE))}]}

    monkeypatch.setattr('src.curation._call_model', call_model)
    budget = _Budget(datetime.now(UTC) + timedelta(seconds=3))
    await _curate_in_batches(_DATA, _synthetic_entries(BATCH_SIZE * 2), 'https://example.com', 'tok', 'model', budget)

    assert timeouts['batch 1/2'] <= 3
    assert timeouts['section merge'] <= 2
    assert timeouts['section assignment 1/1'] <= 2


async def test_curate_in_batches_limits_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    running = 0
    max_running = 0

    async def call_model(payload: dict, _token: str, _timeout_secs: float, *, log_label: str) -> dict:
        nonlocal running, max_running
        if (answer := _fake_merge(payload, log_label)) is not None:
            return answer
        running += 1
        max_running = max(max_running, running)
        await asyncio.sleep(0.01)
        running -= 1
        return {'sections': [{'title': 'Docs', 'pages': list(range(BATCH_SIZE))}]}

    monkeypatch.setattr('src.curation._call_model', call_model)
    entries = _synthetic_entries(BATCH_SIZE * MAX_CONCURRENT_CALLS * 3)
    curated = await curate_with_ai(_DATA, entries, start_url='https://example.com', token='tok')

    assert max_running == MAX_CONCURRENT_CALLS
    assert sum(len(section['links']) for section in curated['sections']) == len(entries)


async def test_call_is_retried_once_after_a_transient_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def call_model(*_args: object, **_kwargs: object) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _TransientError('HTTP 429')
        return {'sections': [{'title': 'Docs', 'pages': [0]}]}

    monkeypatch.setattr('src.curation._call_model', call_model)
    monkeypatch.setattr('src.curation.asyncio.sleep', _no_sleep)
    await curate_with_ai(_DATA, _synthetic_entries(1), start_url='https://example.com', token='tok')
    assert calls == 2


async def test_client_errors_are_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def call_model(*_args: object, **_kwargs: object) -> dict:
        nonlocal calls
        calls += 1
        raise RuntimeError('HTTP 402')

    monkeypatch.setattr('src.curation._call_model', call_model)
    with pytest.raises(RuntimeError):
        await curate_with_ai(_DATA, _synthetic_entries(1), start_url='https://example.com', token='tok')
    assert calls == 1


async def _no_sleep(_secs: float) -> None:
    return None


async def test_merge_classifies_every_proposed_section_at_the_maximum_page_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At the input schema's maximum page count, with every batch inventing its own section titles, each proposed
    section is assigned individually, in requests whose answers stay far below the response cap, and the final
    sections follow the assignment instead of collapsing into one. Every page is still placed exactly once."""
    entries = _synthetic_entries(MAX_CRAWL_PAGES)
    finals = tuple(f'Final {i}' for i in range(MAX_SECTIONS))
    assignment_sizes: list[int] = []

    async def call_model(payload: dict, _token: str, _timeout_secs: float, *, log_label: str) -> dict:
        if log_label == 'section merge':
            return {'title': 'Site', 'sections': list(finals)}
        if log_label.startswith('section assignment'):
            lines = [
                line
                for line in payload['messages'][1]['content'].splitlines()
                if re.match(r'\d+ \| .* \| \d+ \| ', line)
            ]
            assignment_sizes.append(len(lines))
            # "Topic <batch>-<i>" goes to final section i
            return {'assign': {line.split(' | ')[0]: int(line.split(' | ')[1].split('-')[1]) for line in lines}}
        part = int(log_label.split()[1].split('/')[0])
        # 10 sections per batch, all with titles unique to the batch (the worst case for the merge)
        return {
            'sections': [
                {'title': f'Topic {part}-{i}', 'pages': list(range(i * 6, i * 6 + 6))} for i in range(MAX_SECTIONS)
            ]
        }

    monkeypatch.setattr('src.curation._call_model', call_model)
    curated = await curate_with_ai(_DATA, entries, start_url='https://example.com', token='tok')

    batch_sizes = [min(BATCH_SIZE, MAX_CRAWL_PAGES - start) for start in range(0, MAX_CRAWL_PAGES, BATCH_SIZE)]
    assert sum(assignment_sizes) == sum(min(MAX_SECTIONS, -(-size // 6)) for size in batch_sizes)
    assert max(assignment_sizes) == ASSIGN_CHUNK_SIZE
    worst_case_answer = json.dumps({'assign': {str(i): MAX_SECTIONS - 1 for i in range(ASSIGN_CHUNK_SIZE)}})
    # ~4 characters per token for English, numbers and JSON punctuation tokenize denser; 3 is conservative
    assert len(worst_case_answer) / 3 < MAX_RESPONSE_TOKENS / 4
    assert [section['title'] for section in curated['sections']] == list(finals)
    # Final i holds exactly the pages of every batch's "Topic <batch>-i"
    assert len(curated['sections'][MAX_SECTIONS - 1]['links']) == 6 * sum(size >= BATCH_SIZE for size in batch_sizes)
    assert sum(len(section['links']) for section in curated['sections']) == len(entries)


async def test_unassigned_proposed_sections_keep_matching_or_own_titles(monkeypatch: pytest.MonkeyPatch) -> None:
    async def call_model(_payload: dict, _token: str, _timeout_secs: float, *, log_label: str) -> dict:
        if log_label == 'section merge':
            return {'sections': ['Guides', 'Reference']}
        if log_label.startswith('section assignment'):
            return {'assign': {'0': 1, '99': 0, '1': 'x'}}  # only the first is usable
        part = int(log_label.split()[1].split('/')[0])
        title = 'API' if part == 1 else 'guides'
        return {'sections': [{'title': title, 'pages': list(range(BATCH_SIZE))}]}

    monkeypatch.setattr('src.curation._call_model', call_model)
    budget = _Budget(None)
    curation = await _curate_in_batches(_DATA, _synthetic_entries(BATCH_SIZE * 2), 'https://e.com', 't', 'm', budget)

    # "API" was assigned to "Reference", "guides" was not assigned but matches "Guides" case-insensitively
    assert [(section['title'], len(section['pages'])) for section in curation['sections']] == [
        ('Guides', BATCH_SIZE),
        ('Reference', BATCH_SIZE),
    ]


def test_estimate_ai_seconds_grows_with_the_number_of_rounds() -> None:
    assert estimate_ai_seconds(BATCH_SIZE) == 90
    assert estimate_ai_seconds(BATCH_SIZE + 1) == 120
    assert estimate_ai_seconds(BATCH_SIZE * MAX_CONCURRENT_CALLS * 2) == 180


async def test_batch_is_split_when_the_answer_does_not_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A batch answer cut off at the response cap is retried as two half-size batches, not a failed AI step."""
    labels: list[str] = []

    async def call_model(payload: dict, _token: str, _timeout_secs: float, *, log_label: str) -> dict:
        labels.append(log_label)
        if (answer := _fake_merge(payload, log_label)) is not None:
            return answer
        page_count = _numbered_lines(payload)
        if page_count > BATCH_SIZE // 2:
            raise _UnusableResponseError('cut off')
        return {'sections': [{'title': 'Docs', 'pages': list(range(page_count))}]}

    monkeypatch.setattr('src.curation._call_model', call_model)
    entries = _synthetic_entries(BATCH_SIZE * 2)
    curated = await curate_with_ai(_DATA, entries, start_url='https://example.com', token='tok')

    assert sorted(label for label in labels if label.startswith('batch ') and label.endswith(('a', 'b'))) == [
        'batch 1/2a',
        'batch 1/2b',
        'batch 2/2a',
        'batch 2/2b',
    ]
    assert [section['title'] for section in curated['sections']] == ['Docs']
    # every page placed exactly once, in order
    assert [link['title'] for link in curated['sections'][0]['links']] == [entry.title for entry in entries]


async def test_single_call_falls_back_to_batches_when_the_answer_does_not_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    labels: list[str] = []

    async def call_model(payload: dict, _token: str, _timeout_secs: float, *, log_label: str) -> dict:
        labels.append(log_label)
        if log_label == 'single call':
            raise _UnusableResponseError('cut off')
        if (answer := _fake_merge(payload, log_label)) is not None:
            return answer
        return {'sections': [{'title': 'Docs', 'pages': list(range(BATCH_SIZE))}]}

    monkeypatch.setattr('src.curation._call_model', call_model)
    curated = await curate_with_ai(_DATA, _synthetic_entries(BATCH_SIZE), start_url='https://example.com', token='t')

    assert labels[0] == 'single call'
    assert sorted(labels[1:3]) == ['batch 1/2', 'batch 2/2']
    assert curated['title'] == 'Site'
    assert len(curated['sections'][0]['links']) == BATCH_SIZE


async def test_assignment_is_split_when_the_answer_does_not_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    sizes: list[int] = []

    async def call_model(payload: dict, _token: str, _timeout_secs: float, *, log_label: str) -> dict:
        if log_label.startswith('section assignment'):
            count = _proposed_sections(payload)
            sizes.append(count)
            if count > ASSIGN_CHUNK_SIZE // 2:
                raise _UnusableResponseError('cut off')
        if (answer := _fake_merge(payload, log_label)) is not None:
            return answer
        part = int(log_label.split()[1].split('/')[0])
        return {'sections': [{'title': f'Topic {part}-{i}', 'pages': list(range(i * 6, i * 6 + 6))} for i in range(10)]}

    monkeypatch.setattr('src.curation._call_model', call_model)
    entries = _synthetic_entries(BATCH_SIZE * 4)
    curated = await curate_with_ai(_DATA, entries, start_url='https://example.com', token='tok')

    assert sorted(sizes) == sorted([ASSIGN_CHUNK_SIZE, ASSIGN_CHUNK_SIZE // 2, ASSIGN_CHUNK_SIZE // 2])
    assert [section['title'] for section in curated['sections']] == ['Docs']
    assert len(curated['sections'][0]['links']) == len(entries)


async def test_split_batch_halves_keep_all_their_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each half of a split batch may use MAX_SECTIONS sections; none of the pages fall through to Optional."""

    async def call_model(payload: dict, _token: str, _timeout_secs: float, *, log_label: str) -> dict:
        if (answer := _fake_merge(payload, log_label)) is not None:
            return answer
        count = _numbered_lines(payload)
        if count == BATCH_SIZE:
            raise _UnusableResponseError('cut off')
        size = count // MAX_SECTIONS
        return {
            'sections': [
                {'title': f'{log_label} {i}', 'pages': list(range(i * size, (i + 1) * size))}
                for i in range(MAX_SECTIONS)
            ]
        }

    monkeypatch.setattr('src.curation._call_model', call_model)
    entries = _synthetic_entries(BATCH_SIZE * 2)
    curated = await curate_with_ai(_DATA, entries, start_url='https://example.com', token='tok')

    assert [section['title'] for section in curated['sections']] == ['Docs']
    assert len(curated['sections'][0]['links']) == len(entries)


def test_assignment_answers_in_common_shapes_are_understood() -> None:
    finals = ['Guides', 'Reference']
    answers: list[dict] = [
        {'assign': {'0': 1, '1': 0}},
        {'assign': {'0': '1', '1': '0'}},
        {'assign': {'0': 'Reference', '1': 'guides'}},
        {'assign': [1, 0]},
        {'0': 1, '1': 0},
    ]
    for answer in answers:
        pairs = [(key, _final_section_number(value, finals)) for key, value in _assignment_items(answer)]
        assert pairs == [(0, 1), (1, 0)], answer
    assert _final_section_number(True, finals) is None  # noqa: FBT003
    assert _final_section_number(5, finals) is None
    assert _final_section_number('Unknown', finals) is None

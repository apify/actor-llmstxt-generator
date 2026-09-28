"""Optional AI curation of the `llms.txt` structure through the Apify OpenRouter proxy.

Pages already flagged as optional by URL heuristics (`PageEntry.optional`, e.g. changelogs, blog posts, old
documentation versions -- see `src/helpers.py::is_optional_url`) are never sent to the model: they are appended
to the Optional section directly. This keeps the model's job to genuinely ambiguous pages and keeps the request
small even on huge sites.

The remaining pages are placed in a single call for small/medium sites (`curate_with_ai`), or split into
concurrent batches for large ones (`_curate_in_batches`, see `BATCH_SIZE`), with a final small call to merge
the section names each batch proposed into one consistent, ordered list and write the file's title/summary.
Both paths converge on the same `apply_curation` call, so they share the same validation, deduplication and
fallback-to-Optional behavior.

Time is bounded by the `deadline` passed by the caller (the run timeout minus the time needed to save the output):
every call's timeout is capped by the time left, a call is not started when too little is left, and the whole
step is cancelled at the deadline, so a slow model degrades to the non-AI structure instead of timing the run out.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import impit

from src.builder import OPTIONAL_SECTION_TITLE

if TYPE_CHECKING:
    from src.builder import PageEntry
    from src.mytypes import LLMSData, SectionDict

logger = logging.getLogger('apify')

OPENROUTER_URL = 'https://openrouter.apify.actor/api/v1/chat/completions'
DEFAULT_MODEL = 'qwen/qwen3-30b-a3b-instruct-2507'
# The OpenRouter proxy caps every response at 2048 tokens for all Apify plans, not only the free tier -- the
# free tier's own penalty is a 10x price markup, not a smaller cap. `max_tokens` is kept a bit under that cap.
MAX_RESPONSE_TOKENS = 2000
MAX_SECTIONS = 10
# Number of pages placed per model call once there are too many for one request (see `_curate_in_batches`).
# A naive "~2 tokens per index" estimate is NOT safe in practice: measured live against 604 real pages at
# BATCH_SIZE=150, the model used 1067-2000+ completion tokens per batch -- one batch was truncated exactly at
# the MAX_RESPONSE_TOKENS cap, because models don't reliably emit compact JSON even when asked to, and because
# section count/title verbosity varies with page content, not just page count. At BATCH_SIZE=60, re-verified
# live against 599 real pages (10 concurrent batches), completion tokens per batch were 197-404 -- a 5-10x
# margin below the cap. Batches run concurrently, so wall-clock time does not scale with the number of pages.
BATCH_SIZE = 60
# Batches in flight at once. 10 concurrent batches were verified live; more would risk the proxy's rate limits,
# and one failed batch fails the whole AI step.
MAX_CONCURRENT_CALLS = 10
# Timeout of a single model call, lowered when the run has less time left (see `_Budget`).
CALL_TIMEOUT_SECS = 120.0
# A batch whose answer doesn't fit into the response cap is split in halves down to this many pages.
MIN_SPLIT_PAGES = 8
# A call is not started with less time left than this, it would most likely not finish anyway.
MIN_CALL_SECS = 10.0
# Section titles sent to the merge call. The merge response lists each of them by index (1-3 tokens each), so even
# this many stay far below the response cap. Labels beyond it (the ones with the fewest pages) keep their own title.
MAX_MERGE_LABELS = 150
_JSON_FENCE_RE = re.compile(r'^```(?:json)?\s*|\s*```$')

SYSTEM_PROMPT = """You curate llms.txt files (https://llmstxt.org): short Markdown indexes that help AI coding \
agents find the most useful pages of a website. Use only the information you are given, never invent facts. \
Answer with a single minified JSON object and nothing else: no markdown code fences, no whitespace or line \
breaks other than the single spaces required inside strings."""

USER_PROMPT = """Website: {site_title}
Start URL: {start_url}
Site description: {site_description}

Pages (index | title | path | description):
{pages}

Organize these pages into an llms.txt structure. Return JSON with these keys:
- "title": the name of the project or product the pages document, short, without taglines (e.g. "Apify CLI").
- "summary": one factual sentence (max 200 characters) saying what it is and what the pages cover. No marketing.
- "details": 1-3 short factual sentences with context an AI agent needs to use these pages well, or "".
- "sections": 2-{max_sections} sections grouped by what a developer wants to do (e.g. "Getting started", \
"Guides", "API reference"). Each is {{"title": str, "pages": [page indexes]}}. Put the most important \
section and pages first.
- "optional": page indexes that are secondary (changelogs, blog posts, legal pages, old versions, marketing).
- "exclude": page indexes that are useless for an AI agent (duplicates, login or error pages, tag listings).
Every page index must appear exactly once in "sections", "optional" or "exclude"."""

BATCH_USER_PROMPT = """Website: {site_title}
Site description: {site_description}

This is batch {part} of {total} of a larger set of pages from the same site -- you are only seeing this \
subset, not the whole site. Pages (index | title | path | description):
{pages}

Organize ONLY these pages into groups. Return JSON with these keys:
- "sections": 1-{max_sections} sections grouped by what a developer wants to do (e.g. "Getting started", \
"Guides", "API reference"). Each is {{"title": str, "pages": [page indexes]}}. Put the most important \
section and pages first. Use short, generic section titles, since they will be merged with sections \
proposed for other batches of the same site.
- "optional": page indexes that are secondary (changelogs, blog posts, legal pages, old versions, marketing).
- "exclude": page indexes that are useless for an AI agent (duplicates, login or error pages, tag listings).
Every index from 0 to {max_index} must appear exactly once in "sections", "optional" or "exclude"."""

MERGE_SYSTEM_PROMPT = """You are finishing an llms.txt file (https://llmstxt.org) whose pages were grouped in \
separate batches because there were too many to place in one request. Merge the section names proposed for \
each batch into one consistent, ordered list, and write the file's title and summary. Use only the \
information you are given, never invent facts. Answer with a single minified JSON object and nothing else: \
no markdown code fences, no whitespace or line breaks other than the single spaces required inside strings."""

MERGE_USER_PROMPT = """Website: {site_title}
Start URL: {start_url}
Site description: {site_description}

Section titles proposed for different batches of this site's pages (index | title | number of pages). The same \
real section may have been named slightly differently in different batches:
{labels}

Return JSON with these keys:
- "title": the name of the project or product the pages document, short, without taglines (e.g. "Apify CLI").
- "summary": one factual sentence (max 200 characters) saying what it is and what the pages cover. No marketing.
- "details": 1-3 short factual sentences with context an AI agent needs to use these pages well, or "".
- "sections": the final list of at most {max_sections} sections, most important first. Each is \
{{"title": str, "labels": [indexes of the proposed titles merged into it]}}. Merge equivalent proposed titles \
(e.g. "Getting Started" and "Quickstart" become one section).
Every proposed title index from 0 to {max_index} must appear in exactly one section."""


def _page_lines(entries: list[PageEntry]) -> str:
    """Renders `index | title | path | description` lines for a list of pages, indexed from 0."""
    return '\n'.join(
        f'{index} | {entry.title} | {urlparse(entry.page["url"]).path or "/"} | {(entry.description or "-")[:200]}'
        for index, entry in enumerate(entries)
    )


def build_prompt(data: LLMSData, entries: list[PageEntry], start_url: str) -> str:
    """Builds the user prompt listing all pages, for the single-call path."""
    return USER_PROMPT.format(
        site_title=data['title'],
        start_url=start_url,
        site_description=data['description'] or '-',
        pages=_page_lines(entries),
        max_sections=MAX_SECTIONS,
    )


def _build_batch_prompt(data: LLMSData, chunk: list[PageEntry], part: int, total: int) -> str:
    """Builds the user prompt for one batch of pages, indexed locally within the batch (0 to len(chunk) - 1)."""
    return BATCH_USER_PROMPT.format(
        site_title=data['title'],
        site_description=data['description'] or '-',
        pages=_page_lines(chunk),
        part=part,
        total=total,
        max_sections=MAX_SECTIONS,
        max_index=len(chunk) - 1,
    )


def _build_merge_prompt(data: LLMSData, start_url: str, labels: list[tuple[str, int]]) -> str:
    """Builds the user prompt for the final call that merges batches' section titles into one list."""
    return MERGE_USER_PROMPT.format(
        site_title=data['title'],
        start_url=start_url,
        site_description=data['description'] or '-',
        labels='\n'.join(f'{index} | {label} | {count}' for index, (label, count) in enumerate(labels)),
        max_sections=MAX_SECTIONS,
        max_index=len(labels) - 1,
    )


def parse_response(content: str) -> dict[str, Any]:
    """Parses the JSON object from the model response."""
    parsed = json.loads(_JSON_FENCE_RE.sub('', content.strip()))
    if not isinstance(parsed, dict):
        raise TypeError('Model response is not a JSON object')
    return parsed


def _indexes(value: Any, count: int) -> list[int]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, int) and not isinstance(item, bool) and 0 <= item < count]


def apply_curation(
    data: LLMSData, entries: list[PageEntry], curation: dict[str, Any], *, link_to_markdown: bool
) -> LLMSData:
    """Applies the curation returned by the model. Pages the model did not place go to the Optional section."""
    count = len(entries)
    used: set[int] = set()
    sections: list[SectionDict] = []

    def take(indexes: list[int]) -> list[int]:
        result = [index for index in dict.fromkeys(indexes) if index not in used]
        used.update(result)
        return result

    raw_sections = curation.get('sections')
    for raw_section in (raw_sections if isinstance(raw_sections, list) else [])[:MAX_SECTIONS]:
        if not isinstance(raw_section, dict) or not isinstance(title := raw_section.get('title'), str):
            continue
        if title.strip().lower() == OPTIONAL_SECTION_TITLE.lower() or not title.strip():
            continue
        indexes = take(_indexes(raw_section.get('pages'), count))
        if indexes:
            links = [entries[index].to_link(link_to_markdown=link_to_markdown) for index in indexes]
            sections.append({'title': title.strip(), 'links': links})

    if not sections:
        raise ValueError('Model returned no usable sections')

    take(_indexes(curation.get('exclude'), count))
    optional = take(_indexes(curation.get('optional'), count))
    optional += [index for index in range(count) if index not in used]
    if optional:
        sections.append(
            {
                'title': OPTIONAL_SECTION_TITLE,
                'links': [entries[index].to_link(link_to_markdown=link_to_markdown) for index in optional],
            }
        )

    def text(key: str, max_length: int) -> str | None:
        value = curation.get(key)
        return value.strip()[:max_length] if isinstance(value, str) and value.strip() else None

    return {
        'title': text('title', 100) or data['title'],
        'description': text('summary', 300) or data['description'],
        'details': text('details', 600),
        'sections': sections,
    }


def _add_preplaced_optional(data: LLMSData, preplaced: list[PageEntry], *, link_to_markdown: bool) -> LLMSData:
    """Appends pages that were heuristically flagged as optional (and never sent to the model) to Optional."""
    if not preplaced:
        return data
    links = [entry.to_link(link_to_markdown=link_to_markdown) for entry in preplaced]
    sections = list(data['sections'])
    if sections and sections[-1]['title'] == OPTIONAL_SECTION_TITLE:
        sections[-1] = {'title': OPTIONAL_SECTION_TITLE, 'links': sections[-1]['links'] + links}
    else:
        sections.append({'title': OPTIONAL_SECTION_TITLE, 'links': links})
    return {**data, 'sections': sections}


def _chat_payload(model: str, user_prompt: str, *, system: str = SYSTEM_PROMPT) -> dict[str, Any]:
    return {
        'model': model,
        'max_tokens': MAX_RESPONSE_TOKENS,
        'temperature': 0,
        'response_format': {'type': 'json_object'},
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user_prompt},
        ],
    }


class _TransientError(RuntimeError):
    """An error worth retrying: rate limiting or a server error of the proxy."""


class _UnusableResponseError(ValueError):
    """The model's answer was cut off at the response token cap or is not valid JSON.

    Retrying the same request would most likely give the same answer at temperature 0, but asking about fewer
    pages (or section titles) gives the model less to write, see `_curate_in_batches`.
    """


class _Budget:
    """Time left for the AI step, see the module docstring."""

    def __init__(self, deadline: datetime | None) -> None:
        self._end = None if deadline is None else time.monotonic() + (deadline - datetime.now(UTC)).total_seconds()

    def remaining(self) -> float | None:
        return None if self._end is None else self._end - time.monotonic()

    def call_timeout(self) -> float:
        """Returns the timeout for the next call, raises `TimeoutError` when there is too little time left."""
        remaining = self.remaining()
        if remaining is None:
            return CALL_TIMEOUT_SECS
        if remaining < MIN_CALL_SECS:
            raise TimeoutError(f'Only {max(remaining, 0):.0f} s left before the run timeout, skipping the AI call')
        return min(CALL_TIMEOUT_SECS, remaining)


async def _call_with_retry(payload: dict[str, Any], token: str, budget: _Budget, *, log_label: str) -> dict[str, Any]:
    """Calls the model, retrying once after a rate limit, server or network error when there is time left.

    Invalid or truncated JSON is not retried: at temperature 0 the model would most likely answer the same.
    """
    try:
        return await _call_model(payload, token, budget.call_timeout(), log_label=log_label)
    except _TransientError as exc:
        logger.warning(f'AI curation ({log_label}) failed, retrying once: {exc}')
    except (RuntimeError, ValueError, TypeError, KeyError, TimeoutError):
        # client errors (bad model ID, no credit, ...), unusable responses and the run deadline
        raise
    except Exception as exc:  # network errors and timeouts of the HTTP client
        logger.warning(f'AI curation ({log_label}) failed, retrying once: {exc}')
    await asyncio.sleep(2)
    return await _call_model(payload, token, budget.call_timeout(), log_label=log_label)


async def _call_model(payload: dict[str, Any], token: str, timeout_secs: float, *, log_label: str) -> dict[str, Any]:
    """Sends one chat completion request to the OpenRouter proxy and returns the parsed JSON response."""
    async with impit.AsyncClient(timeout=timeout_secs) as client:
        response = await client.post(
            OPENROUTER_URL,
            content=json.dumps(payload).encode(),
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        )
    if response.status_code != 200:  # noqa: PLR2004
        error = _TransientError if response.status_code == 429 or response.status_code >= 500 else RuntimeError  # noqa: PLR2004
        raise error(f'OpenRouter proxy returned HTTP {response.status_code}: {response.text[:500]}')
    body = json.loads(response.text)
    choice = body['choices'][0]
    content = choice['message']['content'] or ''
    usage = body.get('usage') or {}
    completion_tokens = usage.get('completion_tokens') or 0
    logger.info(
        f'AI curation ({log_label}) with {payload["model"]}: {usage.get("prompt_tokens")} prompt tokens, '
        f'{completion_tokens} completion tokens'
    )
    if completion_tokens > MAX_RESPONSE_TOKENS * 0.6:
        logger.warning(
            f'AI curation ({log_label}) used {completion_tokens} of the {MAX_RESPONSE_TOKENS} response tokens, '
            f'the answer starts with: {content[:300]!r}'
        )
    if choice.get('finish_reason') == 'length':
        raise _UnusableResponseError(f'The answer was cut off at {completion_tokens} tokens')
    try:
        return parse_response(content)
    except (ValueError, TypeError) as exc:
        raise _UnusableResponseError(f'The answer is not a JSON object ({exc}): {content[:200]!r}') from exc


def _concat_placements(
    first: dict[str, Any], first_count: int, second: dict[str, Any], second_count: int
) -> dict[str, Any]:
    """Joins the batch answers for two consecutive halves of a chunk, shifting the second half's page indexes."""

    def sections(answer: dict[str, Any]) -> list[dict[str, Any]]:
        value = answer.get('sections')
        return [section for section in value if isinstance(section, dict)] if isinstance(value, list) else []

    def shifted(value: Any) -> list[int]:
        return [first_count + index for index in _indexes(value, second_count)]

    return {
        'sections': [
            *({**section, 'pages': _indexes(section.get('pages'), first_count)} for section in sections(first)),
            *({**section, 'pages': shifted(section.get('pages'))} for section in sections(second)),
        ],
        'optional': _indexes(first.get('optional'), first_count) + shifted(second.get('optional')),
        'exclude': _indexes(first.get('exclude'), first_count) + shifted(second.get('exclude')),
    }


async def _curate_in_batches(
    data: LLMSData,
    main_entries: list[PageEntry],
    start_url: str,
    token: str,
    model: str,
    budget: _Budget,
    batch_size: int = BATCH_SIZE,
) -> dict[str, Any]:
    """Places pages in concurrent batches, then merges the section titles each batch proposed into one list."""
    chunks = [main_entries[i : i + batch_size] for i in range(0, len(main_entries), batch_size)]
    total = len(chunks)
    logger.info(f'AI curation: {len(main_entries)} pages split into {total} batches of up to {batch_size}.')
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_CALLS)

    async def place(part: int, chunk: list[PageEntry], label: str) -> dict[str, Any]:
        """Places a chunk, splitting it in halves while the answer doesn't fit into the response cap."""
        payload = _chat_payload(model, _build_batch_prompt(data, chunk, part + 1, total))
        try:
            async with semaphore:
                answer = await _call_with_retry(payload, token, budget, log_label=label)
            sections = answer.get('sections')
            # capped per answer, so the halves of a split batch can have up to MAX_SECTIONS each
            return {**answer, 'sections': sections[:MAX_SECTIONS] if isinstance(sections, list) else []}
        except _UnusableResponseError as exc:
            if len(chunk) < 2 * MIN_SPLIT_PAGES:
                raise
            logger.warning(f'AI curation ({label}): {exc}. Splitting the batch in two.')
        half = len(chunk) // 2
        first, second = await asyncio.gather(
            place(part, chunk[:half], f'{label}a'), place(part, chunk[half:], f'{label}b')
        )
        return _concat_placements(first, half, second, len(chunk) - half)

    batch_results = await asyncio.gather(
        *(place(part, chunk, f'batch {part + 1}/{total}') for part, chunk in enumerate(chunks))
    )

    # (raw section title as this batch phrased it, global page indexes), in encounter order
    placed: list[tuple[str, list[int]]] = []
    optional_indexes: list[int] = []
    exclude_indexes: list[int] = []
    offset = 0
    for chunk, result in zip(chunks, batch_results, strict=True):
        count = len(chunk)
        raw_sections = result.get('sections')
        for raw_section in raw_sections if isinstance(raw_sections, list) else []:
            if not isinstance(raw_section, dict) or not isinstance(title := raw_section.get('title'), str):
                continue
            if not title.strip() or title.strip().lower() == OPTIONAL_SECTION_TITLE.lower():
                continue
            local_indexes = _indexes(raw_section.get('pages'), count)
            if local_indexes:
                placed.append((title.strip(), [offset + index for index in local_indexes]))
        optional_indexes.extend(offset + index for index in _indexes(result.get('optional'), count))
        exclude_indexes.extend(offset + index for index in _indexes(result.get('exclude'), count))
        offset += count

    if not placed:
        raise ValueError('Model returned no usable sections in any batch')

    # merge titles that differ only by case/whitespace before asking the model, keyed by the lowercased title
    label_pages: dict[str, int] = {}
    label_display: dict[str, str] = {}
    for label, indexes in placed:
        label_display.setdefault(label.lower(), label)
        label_pages[label.lower()] = label_pages.get(label.lower(), 0) + len(indexes)
    # the biggest labels, in the order they were first proposed (batches follow the navigation order)
    kept = set(sorted(label_pages, key=lambda key: -label_pages[key])[:MAX_MERGE_LABELS])
    merge_labels = [key for key in label_display if key in kept]
    if len(label_pages) > len(merge_labels):
        logger.info(f'AI curation: merging the {len(merge_labels)} biggest of {len(label_pages)} proposed sections.')

    while True:
        merge_payload = _chat_payload(
            model,
            _build_merge_prompt(data, start_url, [(label_display[key], label_pages[key]) for key in merge_labels]),
            system=MERGE_SYSTEM_PROMPT,
        )
        try:
            merge_result = await _call_with_retry(merge_payload, token, budget, log_label='section merge')
            break
        except _UnusableResponseError as exc:
            if len(merge_labels) <= MAX_SECTIONS:
                raise
            # labels left out keep their own titles, like the ones beyond MAX_MERGE_LABELS
            kept = set(sorted(merge_labels, key=lambda key: -label_pages[key])[: len(merge_labels) // 2])
            merge_labels = [key for key in merge_labels if key in kept]
            logger.warning(f'AI curation (section merge): {exc}. Retrying with the {len(merge_labels)} biggest.')

    # lowercased proposed title -> final section title, in the final order
    title_of: dict[str, str] = {}
    order: list[str] = []
    raw_sections = merge_result.get('sections')
    for raw_section in raw_sections if isinstance(raw_sections, list) else []:
        if not isinstance(raw_section, dict) or not isinstance(title := raw_section.get('title'), str):
            continue
        if not title.strip():
            continue
        for index in _indexes(raw_section.get('labels'), len(merge_labels)):
            title_of.setdefault(merge_labels[index], title.strip())
        if title.strip() not in order:
            order.append(title.strip())

    pages_by_title: dict[str, list[int]] = {}
    for label, indexes in placed:
        # labels the model did not map (or that were not sent to it) keep their own title
        title = title_of.get(label.lower(), label_display[label.lower()])
        if title not in order:
            order.append(title)
        pages_by_title.setdefault(title, []).extend(indexes)

    def text(key: str) -> str | None:
        value = merge_result.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    return {
        'title': text('title'),
        'summary': text('summary'),
        'details': text('details'),
        'sections': [{'title': title, 'pages': pages_by_title[title]} for title in order if title in pages_by_title],
        'optional': optional_indexes,
        'exclude': exclude_indexes,
    }


async def curate_with_ai(
    data: LLMSData,
    entries: list[PageEntry],
    *,
    start_url: str,
    token: str,
    model: str = DEFAULT_MODEL,
    link_to_markdown: bool = True,
    deadline: datetime | None = None,
) -> LLMSData:
    """Asks the model to curate the `llms.txt` structure and returns the curated data.

    Pages already flagged as optional by URL heuristics are excluded from the request and appended to the
    Optional section directly (see the module docstring). The rest are placed in one call for small/medium
    sites, or split into concurrent batches with a final merge call for large ones (`BATCH_SIZE`).

    Raises `TimeoutError` when the `deadline` is reached before the curation finishes.
    """
    main_entries = [entry for entry in entries if not entry.optional]
    preplaced = [entry for entry in entries if entry.optional]
    if not main_entries:
        raise ValueError('No pages left to curate: every page was heuristically flagged as optional')

    budget = _Budget(deadline)
    remaining = budget.remaining()
    async with asyncio.timeout(None if remaining is None else max(remaining, 0.0)):
        curation = None
        if len(main_entries) <= BATCH_SIZE:
            payload = _chat_payload(model, build_prompt(data, main_entries, start_url))
            try:
                curation = await _call_with_retry(payload, token, budget, log_label='single call')
            except _UnusableResponseError as exc:
                if len(main_entries) < 2 * MIN_SPLIT_PAGES:
                    raise
                logger.warning(f'AI curation (single call): {exc}. Retrying in two batches.')
        if curation is None:
            batch_size = BATCH_SIZE if len(main_entries) > BATCH_SIZE else -(-len(main_entries) // 2)
            curation = await _curate_in_batches(data, main_entries, start_url, token, model, budget, batch_size)

    curated = apply_curation(data, main_entries, curation, link_to_markdown=link_to_markdown)
    return _add_preplaced_optional(curated, preplaced, link_to_markdown=link_to_markdown)


def estimate_ai_seconds(page_count: int) -> float:
    """Rough wall-clock time the AI step needs for this many pages, used to reserve time for it in the run.

    One call usually takes 5-30 s; batches run `MAX_CONCURRENT_CALLS` at a time and are followed by the merge call.
    """
    batches = -(-page_count // BATCH_SIZE)
    if batches <= 1:
        return 90.0
    rounds = -(-batches // MAX_CONCURRENT_CALLS)
    return 60.0 * rounds + 60.0

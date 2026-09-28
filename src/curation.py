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
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
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

Section titles proposed for different batches of this site's pages, with how many pages ended up in each \
(the same real section may have been named slightly differently in different batches):
{labels}

Return JSON with these keys:
- "title": the name of the project or product the pages document, short, without taglines (e.g. "Apify CLI").
- "summary": one factual sentence (max 200 characters) saying what it is and what the pages cover. No marketing.
- "details": 1-3 short factual sentences with context an AI agent needs to use these pages well, or "".
- "sections": the final ordered list of at most {max_sections} canonical section titles, merging equivalent \
proposed titles (e.g. "Getting Started" and "Quickstart" become one), most important first.
- "mapping": a JSON object mapping EVERY proposed title listed above (use its exact text as the key) to one \
of the "sections" titles."""


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


def _build_merge_prompt(data: LLMSData, start_url: str, label_counts: dict[str, int]) -> str:
    """Builds the user prompt for the final call that merges batches' section titles into one list."""
    labels = '\n'.join(f'- "{label}" ({count} pages)' for label, count in label_counts.items())
    return MERGE_USER_PROMPT.format(
        site_title=data['title'],
        start_url=start_url,
        site_description=data['description'] or '-',
        labels=labels,
        max_sections=MAX_SECTIONS,
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


async def _call_model(payload: dict[str, Any], token: str, timeout_secs: float, *, log_label: str) -> dict[str, Any]:
    """Sends one chat completion request to the OpenRouter proxy and returns the parsed JSON response."""
    async with impit.AsyncClient(timeout=timeout_secs) as client:
        response = await client.post(
            OPENROUTER_URL,
            content=json.dumps(payload).encode(),
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        )
    if response.status_code != 200:  # noqa: PLR2004
        raise RuntimeError(f'OpenRouter proxy returned HTTP {response.status_code}: {response.text[:500]}')
    body = json.loads(response.text)
    content = body['choices'][0]['message']['content']
    usage = body.get('usage') or {}
    logger.info(
        f'AI curation ({log_label}) with {payload["model"]}: {usage.get("prompt_tokens")} prompt tokens, '
        f'{usage.get("completion_tokens")} completion tokens'
    )
    return parse_response(content)


async def _curate_in_batches(
    data: LLMSData, main_entries: list[PageEntry], start_url: str, token: str, model: str, timeout_secs: float
) -> dict[str, Any]:
    """Places pages in concurrent batches, then merges the section titles each batch proposed into one list."""
    chunks = [main_entries[i : i + BATCH_SIZE] for i in range(0, len(main_entries), BATCH_SIZE)]
    total = len(chunks)
    logger.info(f'AI curation: {len(main_entries)} pages split into {total} batches of up to {BATCH_SIZE}.')

    async def place(part: int, chunk: list[PageEntry]) -> dict[str, Any]:
        payload = _chat_payload(model, _build_batch_prompt(data, chunk, part + 1, total))
        return await _call_model(payload, token, timeout_secs, log_label=f'batch {part + 1}/{total}')

    batch_results = await asyncio.gather(*(place(part, chunk) for part, chunk in enumerate(chunks)))

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
            if not title.strip():
                continue
            local_indexes = _indexes(raw_section.get('pages'), count)
            if local_indexes:
                placed.append((title.strip(), [offset + index for index in local_indexes]))
        optional_indexes.extend(offset + index for index in _indexes(result.get('optional'), count))
        exclude_indexes.extend(offset + index for index in _indexes(result.get('exclude'), count))
        offset += count

    if not placed:
        raise ValueError('Model returned no usable sections in any batch')

    # merge titles that differ only by case/whitespace before asking the model to canonicalize them, so the
    # merge prompt (and its output) stays small regardless of how many batches there were
    label_counts: dict[str, int] = {}
    label_display: dict[str, str] = {}
    for label, indexes in placed:
        display = label_display.setdefault(label.lower(), label)
        label_counts[display] = label_counts.get(display, 0) + len(indexes)

    merge_payload = _chat_payload(model, _build_merge_prompt(data, start_url, label_counts), system=MERGE_SYSTEM_PROMPT)
    merge_result = await _call_model(merge_payload, token, timeout_secs, log_label='section merge')

    raw_mapping = merge_result.get('mapping')
    mapping = {
        key.strip().lower(): value.strip()
        for key, value in (raw_mapping.items() if isinstance(raw_mapping, dict) else [])
        if isinstance(key, str) and isinstance(value, str) and value.strip()
    }
    raw_order = merge_result.get('sections')
    order = (
        [title.strip() for title in raw_order if isinstance(title, str) and title.strip()]
        if isinstance(raw_order, list)
        else []
    )

    pages_by_title: dict[str, list[int]] = {}
    for label, indexes in placed:
        title = mapping.get(label.lower(), label)
        if title not in pages_by_title:
            pages_by_title[title] = []
            if title not in order:
                order.append(title)
        pages_by_title[title].extend(indexes)

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
    timeout_secs: float = 120,
) -> LLMSData:
    """Asks the model to curate the `llms.txt` structure and returns the curated data.

    Pages already flagged as optional by URL heuristics are excluded from the request and appended to the
    Optional section directly (see the module docstring). The rest are placed in one call for small/medium
    sites, or split into concurrent batches with a final merge call for large ones (`BATCH_SIZE`).
    """
    main_entries = [entry for entry in entries if not entry.optional]
    preplaced = [entry for entry in entries if entry.optional]
    if not main_entries:
        raise ValueError('No pages left to curate: every page was heuristically flagged as optional')

    if len(main_entries) <= BATCH_SIZE:
        payload = _chat_payload(model, build_prompt(data, main_entries, start_url))
        curation = await _call_model(payload, token, timeout_secs, log_label='single call')
    else:
        curation = await _curate_in_batches(data, main_entries, start_url, token, model, timeout_secs)

    curated = apply_curation(data, main_entries, curation, link_to_markdown=link_to_markdown)
    return _add_preplaced_optional(curated, preplaced, link_to_markdown=link_to_markdown)

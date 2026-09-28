"""Optional AI curation of the `llms.txt` structure through the Apify OpenRouter proxy."""

from __future__ import annotations

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
DEFAULT_MODEL = 'openai/gpt-4.1-mini'
# free Apify plans are limited to 2048 tokens per response by the proxy
MAX_RESPONSE_TOKENS = 2000
MAX_SECTIONS = 10
_JSON_FENCE_RE = re.compile(r'^```(?:json)?\s*|\s*```$')

SYSTEM_PROMPT = """You curate llms.txt files (https://llmstxt.org): short Markdown indexes that help AI coding \
agents find the most useful pages of a website. Use only the information you are given, never invent facts. \
Answer with a single JSON object and nothing else."""

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


def build_prompt(data: LLMSData, entries: list[PageEntry], start_url: str) -> str:
    """Builds the user prompt listing all pages."""
    lines = []
    for index, entry in enumerate(entries):
        description = (entry.description or '-')[:200]
        lines.append(f'{index} | {entry.title} | {urlparse(entry.page["url"]).path or "/"} | {description}')
    return USER_PROMPT.format(
        site_title=data['title'],
        start_url=start_url,
        site_description=data['description'] or '-',
        pages='\n'.join(lines),
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
    """Asks the model to curate the `llms.txt` structure and returns the curated data."""
    payload = {
        'model': model,
        'max_tokens': MAX_RESPONSE_TOKENS,
        'temperature': 0,
        'response_format': {'type': 'json_object'},
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user', 'content': build_prompt(data, entries, start_url)},
        ],
    }
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
        f'AI curation with {model}: {usage.get("prompt_tokens")} prompt tokens, '
        f'{usage.get("completion_tokens")} completion tokens'
    )
    return apply_curation(data, entries, parse_response(content), link_to_markdown=link_to_markdown)

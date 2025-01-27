from __future__ import annotations

from typing import TypedDict


class LinkDict(TypedDict):
    """Dictionary representing a single link in the `llms.txt` file."""

    url: str
    title: str
    description: str | None


class SectionDict(TypedDict):
    """Dictionary representing a single section in the `llms.txt` file."""

    title: str
    links: list[LinkDict]


class LLMSData(TypedDict):
    """Dictionary representing the data structure of the `llms.txt` file."""

    title: str
    description: str | None
    details: str | None
    sections: dict[str, SectionDict]

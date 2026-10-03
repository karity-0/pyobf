from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Replacement:
    """A half-open character range in the current source, not byte offsets."""

    start: int
    end: int
    text: str


def apply_replacements(text: str, edits: Sequence[Replacement]) -> str:
    ordered = sorted(edits, key=lambda edit: (edit.start, edit.end))
    previous: Replacement | None = None
    for edit in ordered:
        if not 0 <= edit.start <= edit.end <= len(text):
            raise ValueError("Replacement range is outside the source")
        if previous and (edit.start < previous.end or edit.start == previous.start):
            raise ValueError("Replacement ranges overlap or share a start")
        previous = edit
    if not ordered:
        return text
    parts: list[str] = []
    cursor = 0
    for edit in ordered:
        parts.extend((text[cursor:edit.start], edit.text))
        cursor = edit.end
    parts.append(text[cursor:])
    return "".join(parts)

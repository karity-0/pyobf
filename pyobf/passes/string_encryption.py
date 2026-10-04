from __future__ import annotations

import re
import secrets
from typing import Sequence

from ..macros import find_string_macros
from ..source import SourceDocument
from .base import PrePass, Replacement


def encrypted_string_expression(value: str) -> str:
    """Emit a self-contained decoder without builtin name lookups."""
    data = value.encode("utf-8", "surrogatepass")
    key = int.from_bytes(secrets.token_bytes(max(1, len(data))), "big") or 1
    encrypted = int.from_bytes(data, "big") ^ key
    return (
        f"(lambda _c, _k: (_c ^ _k).to_bytes({len(data)}, 'big')"
        f".decode('utf-8', 'surrogatepass'))(0x{encrypted:x}, 0x{key:x})"
    )


class StringEncryptionPass(PrePass):
    """Encrypt only explicit string macros, with a fresh mask per occurrence.

    The output carries its decoding key: this hides plaintext in source, rather
    than storing a secret. The inline decoder has no imports, helper globals,
    or builtin name lookups that user code can shadow.
    """

    def run(self, source: SourceDocument) -> Sequence[Replacement]:
        edits: list[Replacement] = []
        for macro in find_string_macros(source):
            expression = encrypted_string_expression(macro.value)
            # Retain every physical newline so surrounding code keeps its lines.
            newlines = re.findall(r"\r\n|\r|\n", source.text[macro.start:macro.end])
            if newlines:
                expression = "(" + expression + "".join(newlines) + ")"
            edits.append(Replacement(macro.start, macro.end, expression))
        return edits

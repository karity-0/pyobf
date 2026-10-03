from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass, field, replace
from functools import cached_property
from pathlib import Path


@dataclass(frozen=True)
class SourceDocument:
    text: str
    filename: str = "<untitled>"
    encoding: str = "utf-8"
    _original_bytes: bytes | None = field(default=None, repr=False)

    @classmethod
    def from_bytes(cls, data: bytes, filename: str = "<untitled>") -> SourceDocument:
        # Encoding cookies live on physical lines, including CR-only files.
        header = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        encoding, _ = tokenize.detect_encoding(io.BytesIO(header).readline)
        return cls(data.decode(encoding), filename, encoding, data)

    @classmethod
    def read(cls, path: str | Path) -> SourceDocument:
        path = Path(path)
        return cls.from_bytes(path.read_bytes(), str(path))

    def with_text(self, text: str) -> SourceDocument:
        if text == self.text:
            return self
        return replace(self, text=text, _original_bytes=None)

    def to_bytes(self) -> bytes:
        if self._original_bytes is not None:
            return self._original_bytes
        return self.text.encode(self.encoding)

    @cached_property
    def _line_starts(self) -> tuple[int, ...]:
        # Python treats CR, LF, and CRLF as line endings, but not form feed.
        return (0, *(match.end() for match in re.finditer(r"\r\n|\r|\n", self.text)))

    def position(self, row: int, column: int) -> int:
        """Convert tokenize's 1-based row / character column to a source offset."""
        # ENDMARKER can point to a virtual line after an unterminated final line.
        if row == len(self._line_starts) + 1 and column == 0:
            return len(self.text)
        return self._line_starts[row - 1] + column

    def line_text(self, row: int) -> str:
        if not 1 <= row <= len(self._line_starts):
            return ""
        start = self._line_starts[row - 1]
        end = self._line_starts[row] if row < len(self._line_starts) else len(self.text)
        return self.text[start:end]

    def span(self, node: ast.AST) -> tuple[int, int]:
        """Convert AST UTF-8 byte columns to Python string offsets."""
        if getattr(node, "end_lineno", None) is None:
            raise ValueError(f"{type(node).__name__} has no source span")
        def offset(line: int, column: int) -> int:
            start = self._line_starts[line - 1]
            end = self._line_starts[line] if line < len(self._line_starts) else len(self.text)
            prefix = self.text[start:end].encode("utf-8")[:column].decode("utf-8")
            return start + len(prefix)

        return offset(node.lineno, node.col_offset), offset(node.end_lineno, node.end_col_offset)

    def source_for(self, node: ast.AST) -> str:
        start, end = self.span(node)
        return self.text[start:end]

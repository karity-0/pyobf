from __future__ import annotations

import ast
import io
import tokenize
from dataclasses import dataclass

from .source import SourceDocument


@dataclass(frozen=True)
class ProtectionMarker:
    row: int
    column: int
    indent: str
    options: tuple[str, ...] | None


@dataclass(frozen=True)
class ProtectionRegion:
    start: ProtectionMarker
    end: ProtectionMarker


def marker_error(source: SourceDocument, marker: ProtectionMarker, message: str) -> SyntaxError:
    return SyntaxError(message, (source.filename, marker.row, marker.column + 1, source.line_text(marker.row)))


def find_protection_regions(source: SourceDocument) -> tuple[ProtectionRegion, ...]:
    """Recognize reserved directives on their own physical statement lines."""
    tokens: list[tokenize.TokenInfo] = []
    try:
        tokens.extend(tokenize.generate_tokens(io.StringIO(source.text, newline=None).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # Retain tokens yielded so far; AST validation handles ordinary errors.
        pass
    stack: list[ProtectionMarker] = []
    regions: list[ProtectionRegion] = []
    index = 0
    while index + 1 < len(tokens):
        token, name = tokens[index:index + 2]
        if not (token.type == tokenize.OP and token.string == "@" and name.type == tokenize.NAME
                and name.string in {"protect_start", "protect_end"}):
            index += 1
            continue
        row, column = token.start
        indent = source.line_text(row)[:column]
        marker = ProtectionMarker(row, column, indent, None)
        if indent.strip():
            raise marker_error(source, marker, "protect 매크로는 독립된 줄에 써야 합니다")
        line_tokens = []
        end_index = index + 1
        while end_index < len(tokens):
            current = tokens[end_index]
            if current.type in (tokenize.COMMENT, tokenize.NEWLINE, tokenize.NL, tokenize.ENDMARKER):
                break
            if current.start[0] != row or current.end[0] != row:
                raise marker_error(source, marker, "protect 매크로 옵션은 한 줄에 써야 합니다")
            line_tokens.append(current)
            end_index += 1
        text = source.text[source.position(*name.start):source.position(*line_tokens[-1].end)]
        if name.string == "protect_start":
            try:
                call = ast.parse(text, mode="eval").body
            except SyntaxError:
                raise marker_error(source, marker, "@protect_start(cff, junk) 형식으로 써야 합니다") from None
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != "protect_start" or call.keywords or not call.args:
                raise marker_error(source, marker, "protect 옵션으로 cff, bcf, junk, proxy, morph 또는 integrity를 지정하세요")
            if any(not isinstance(arg, ast.Name) or arg.id not in {"cff", "bcf", "junk", "proxy", "morph", "integrity"} for arg in call.args):
                raise marker_error(source, marker, "지원하는 protect 옵션은 cff, bcf, junk, proxy, morph, integrity입니다")
            options = tuple(arg.id for arg in call.args)
            if len(set(options)) != len(options):
                raise marker_error(source, marker, "protect 옵션이 중복되었습니다")
            stack.append(ProtectionMarker(row, column, indent, options))
        else:
            if text != "protect_end":
                raise marker_error(source, marker, "종료 매크로는 @protect_end로 써야 합니다")
            if not stack:
                raise marker_error(source, marker, "@protect_end에 대응하는 @protect_start가 없습니다")
            start = stack.pop()
            if start.indent.expandtabs(8) != indent.expandtabs(8):
                raise marker_error(source, marker, "protect 시작과 끝의 들여쓰기가 같아야 합니다")
            regions.append(ProtectionRegion(start, marker))
        index = end_index
    if stack:
        raise marker_error(source, stack[-1], "protect 영역을 닫는 @protect_end가 필요합니다")
    return tuple(sorted(regions, key=lambda region: region.start.row))

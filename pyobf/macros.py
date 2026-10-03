from __future__ import annotations

import ast
import io
import tokenize
from dataclasses import dataclass

from .source import SourceDocument


@dataclass(frozen=True)
class StringMacro:
    start: int
    end: int
    value: str


def _error(source: SourceDocument, token: tokenize.TokenInfo, message: str) -> SyntaxError:
    row, column = token.start
    return SyntaxError(message, (source.filename, row, column + 1, source.line_text(row)))


def find_string_macros(source: SourceDocument) -> tuple[StringMacro, ...]:
    """Read @{str_literal} without matching markers inside strings or comments.

    @ and { must be adjacent. Decorators and spaced matrix multiplication keep
    their Python meaning. Only constant str values are supported, never eval.
    """
    tokens: list[tokenize.TokenInfo] = []
    token_error: tokenize.TokenError | None = None
    try:
        tokens.extend(tokenize.generate_tokens(io.StringIO(source.text, newline=None).readline))
    except tokenize.TokenError as error:
        # Keep tokens already yielded so an unclosed macro gets its own diagnostic.
        token_error = error
    except (IndentationError, SyntaxError):
        # The AST stage will report ordinary Python errors against the full source.
        return ()

    macros: list[StringMacro] = []
    index = 0
    while index + 1 < len(tokens):
        marker, opening = tokens[index:index + 2]
        if not (
            marker.type == tokenize.OP and marker.string == "@"
            and opening.type == tokenize.OP and opening.string == "{"
            and marker.end == opening.start
        ):
            index += 1
            continue

        depth = 1
        closing_index = index + 2
        while closing_index < len(tokens):
            current = tokens[closing_index]
            if current.type == tokenize.OP:
                if current.string == "{":
                    depth += 1
                elif current.string == "}":
                    depth -= 1
                    if depth == 0:
                        break
            closing_index += 1
        if depth:
            raise _error(source, marker, "문자열 매크로를 닫는 }가 필요합니다")

        closing = tokens[closing_index]
        start = source.position(*marker.start)
        body_start = source.position(*opening.end)
        body_end = source.position(*closing.start)
        body = source.text[body_start:body_end]
        try:
            expression = ast.parse("(" + body + "\n)", mode="eval").body
        except SyntaxError:
            raise _error(source, marker, "@{...}에는 문자열 리터럴만 넣을 수 있습니다") from None
        if not isinstance(expression, ast.Constant) or not isinstance(expression.value, str):
            raise _error(source, marker, "@{...}에는 문자열 리터럴만 넣을 수 있습니다")
        macros.append(StringMacro(start, source.position(*closing.end), expression.value))
        index = closing_index + 1

    if token_error is not None:
        message, (row, column) = token_error.args
        # An ordinary unclosed Python construct, rather than a malformed macro.
        line = source.line_text(row)
        raise SyntaxError(message, (source.filename, row, column + 1, line)) from token_error
    return tuple(macros)

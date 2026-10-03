from __future__ import annotations

import ast
import re
from typing import Sequence

from ..analysis import analyze
from ..edits import apply_replacements
from ..names import NameAllocator
from ..protection import ProtectionRegion, find_protection_regions, marker_error
from ..source import SourceDocument
from .base import PrePass, Replacement
from .cff import ControlFlowFlatteningPass
from .junk import JunkCodePass


class ProtectionPass(PrePass):
    """Lower paired region macros through reusable AST block passes."""

    def run(self, source: SourceDocument) -> Sequence[Replacement]:
        regions = find_protection_regions(source)
        if not regions:
            return []
        starts = {region.start.row: region for region in regions}
        ends = {region.end.row: region for region in regions}
        marker_rows = starts.keys() | ends.keys()
        markers = [marker for region in regions for marker in (region.start, region.end)]
        edits = []
        for marker in markers:
            start = source.position(marker.row, 0)
            length = len(source.line_text(marker.row).rstrip("\r\n"))
            edits.append(Replacement(start, start + length, marker.indent + "pass"))
        context = analyze(source.with_text(apply_replacements(source.text, edits)))
        names = NameAllocator(context.symbols, source.text)
        # Pair endpoints must belong to exactly the same AST statement list.
        owners: dict[int, tuple[int, str]] = {}
        for node in ast.walk(context.tree):
            for field, value in ast.iter_fields(node):
                if isinstance(value, list):
                    for child in value:
                        if isinstance(child, ast.Pass) and child.lineno in marker_rows:
                            owners[child.lineno] = (id(node), field)
        for region in regions:
            if owners.get(region.start.row) is None or owners.get(region.start.row) != owners.get(region.end.row):
                raise marker_error(source, region.end, "protect 시작과 끝은 같은 Python 블록 안에 있어야 합니다")

        lowered: dict[int, list[ast.stmt]] = {}
        junk, cff = JunkCodePass(), ControlFlowFlatteningPass()
        scope_types = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

        def visit(node):
            for field, value in ast.iter_fields(node):
                if isinstance(value, list) and value and all(isinstance(child, ast.stmt) for child in value):
                    setattr(node, field, lower_list(value, node, field, field == "body" and isinstance(node, scope_types)))
                elif isinstance(value, list):
                    for child in value:
                        if isinstance(child, ast.AST):
                            visit(child)
                elif isinstance(value, ast.AST):
                    visit(value)

        def lower_list(body, owner, field, can_docstring=False):
            output = []
            index = 0
            while index < len(body):
                statement = body[index]
                region = starts.get(getattr(statement, "lineno", None)) if isinstance(statement, ast.Pass) else None
                if region is None:
                    visit(statement)
                    output.append(statement)
                    index += 1
                    continue
                closing = index + 1
                while closing < len(body) and getattr(body[closing], "lineno", None) != region.end.row:
                    closing += 1
                inner = lower_list(body[index + 1:closing], owner, field, can_docstring and not output)
                following = next((item for item in body[closing + 1:] if not (
                    isinstance(item, ast.Pass) and item.lineno in marker_rows
                )), None)
                if can_docstring and not output and not inner and isinstance(following, ast.Expr) and isinstance(following.value, ast.Constant) and isinstance(following.value.value, str):
                    # Empty regions must not displace a following real docstring.
                    lowered[region.start.row] = []
                    index = closing + 1
                    continue
                prefix = []
                if can_docstring and not output and inner and isinstance(inner[0], ast.Expr) and isinstance(inner[0].value, ast.Constant) and isinstance(inner[0].value.value, str):
                    # A region directive before a docstring must not erase __doc__.
                    prefix, inner = [inner[0]], inner[1:]
                if "junk" in region.start.options:
                    inner = junk.run(inner, names)
                if "cff" in region.start.options:
                    inner = cff.run(inner, names)
                transformed = prefix + (inner or [ast.Pass()])
                lowered[region.start.row] = transformed
                output.extend(transformed)
                index = closing + 1
            return output

        visit(context.tree)
        replacements = []
        previous_end = 0
        for region in regions:
            if region.start.row < previous_end:
                continue  # The enclosing region already emits transformed children.
            previous_end = region.end.row
            body = ast.Module(body=lowered[region.start.row], type_ignores=[])
            rendered = ast.unparse(ast.fix_missing_locations(body))
            # Keep escaped Unicode representable in a legacy-encoded source file.
            rendered = rendered.encode(source.encoding, "backslashreplace").decode(source.encoding)
            newline_match = re.search(r"\r\n|\r|\n", source.line_text(region.start.row))
            newline = newline_match.group() if newline_match else "\n"
            rendered = newline.join(region.start.indent + line if line else "" for line in rendered.split("\n"))
            start = source.position(region.start.row, 0)
            end = source.position(region.end.row, 0) + len(source.line_text(region.end.row).rstrip("\r\n"))
            replacements.append(Replacement(start, end, rendered))
        return replacements

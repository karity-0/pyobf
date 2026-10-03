from __future__ import annotations

import ast
import io
import symtable
import tokenize
from dataclasses import dataclass
from types import MappingProxyType
from typing import Iterator, Mapping

from .source import SourceDocument


@dataclass(frozen=True)
class NodeReference:
    node: ast.AST
    parent: ast.AST | None
    field: str | None
    index: int | None


@dataclass(frozen=True)
class AnalysisContext:
    source: SourceDocument
    tree: ast.Module
    nodes: tuple[NodeReference, ...]
    by_type: Mapping[str, tuple[NodeReference, ...]]
    tokens: tuple[tokenize.TokenInfo, ...]
    symbols: symtable.SymbolTable

    def find(self, node_type: type[ast.AST]) -> Iterator[NodeReference]:
        """Include subclasses, so find(ast.expr) discovers every expression."""
        return (ref for ref in self.nodes if isinstance(ref.node, node_type))


def analyze(source: SourceDocument) -> AnalysisContext:
    tree = ast.parse(source.text, filename=source.filename, type_comments=True)
    # ast.parse/symtable alone allow e.g. a return outside a function.
    # Compile for validation only; never evaluate or execute the resulting code.
    compile(tree, source.filename, "exec", dont_inherit=True)
    symbols = symtable.symtable(source.text, source.filename, "exec")
    tokens = tuple(tokenize.generate_tokens(io.StringIO(source.text, newline=None).readline))
    nodes: list[NodeReference] = []
    by_type: dict[str, list[NodeReference]] = {}
    pending = [NodeReference(tree, None, None, None)]
    while pending:
        ref = pending.pop()
        nodes.append(ref)
        by_type.setdefault(type(ref.node).__name__, []).append(ref)
        children: list[NodeReference] = []
        for name, value in ast.iter_fields(ref.node):
            if isinstance(value, ast.AST):
                children.append(NodeReference(value, ref.node, name, None))
            elif isinstance(value, list):
                children.extend(
                    NodeReference(child, ref.node, name, index)
                    for index, child in enumerate(value)
                    if isinstance(child, ast.AST)
                )
        pending.extend(reversed(children))
    return AnalysisContext(
        source, tree, tuple(nodes),
        MappingProxyType({name: tuple(refs) for name, refs in by_type.items()}),
        tokens, symbols,
    )

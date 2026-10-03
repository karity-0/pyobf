from __future__ import annotations

import secrets
import symtable


class NameAllocator:
    """Reserve names across all lexical scopes before adding block helpers."""

    def __init__(self, symbols: symtable.SymbolTable, source: str) -> None:
        self.random = secrets.SystemRandom()
        self.source = source
        self.used: set[str] = set()
        pending = [symbols]
        while pending:
            scope = pending.pop()
            self.used.update(scope.get_identifiers())
            pending.extend(scope.get_children())

    def new(self) -> str:
        while True:
            name = "_p" + secrets.token_hex(6)
            if name not in self.used and name not in self.source:
                self.used.add(name)
                return name

from __future__ import annotations

from abc import ABC, abstractmethod
import ast
from typing import Sequence

from ..analysis import AnalysisContext
from ..edits import Replacement
from ..source import SourceDocument
from ..names import NameAllocator


class PrePass(ABC):
    @abstractmethod
    def run(self, source: SourceDocument) -> Sequence[Replacement]:
        """Lower custom syntax to valid Python before AST analysis."""
        raise NotImplementedError


class BasePass(ABC):
    @abstractmethod
    def run(self, context: AnalysisContext) -> Sequence[Replacement]:
        """Inspect AST/tokens/symbols and propose non-overlapping source edits.

        Use context.source.span(node) for offsets. AST mutation alone does not
        emit code; edits preserve surrounding comments and formatting.
        """
        raise NotImplementedError


class BlockPass(ABC):
    @abstractmethod
    def run(self, statements: list[ast.stmt], names: NameAllocator) -> list[ast.stmt]:
        """Transform one selected statement block, retaining its lexical scope."""
        raise NotImplementedError

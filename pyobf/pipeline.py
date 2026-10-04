from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .analysis import AnalysisContext, analyze
from .edits import Replacement, apply_replacements
from .passes import BasePass, PrePass, ProtectionPass, StringEncryptionPass, StripInfoPass
from .source import SourceDocument


@dataclass(frozen=True)
class PassRecord:
    name: str
    replacements: int


@dataclass(frozen=True)
class PipelineResult:
    source: SourceDocument
    analysis: AnalysisContext
    applied_passes: tuple[str, ...]
    pass_records: tuple[PassRecord, ...] = ()

    @property
    def replacement_count(self) -> int:
        return sum(record.replacements for record in self.pass_records)


class Pipeline:
    """Analyze, apply registered source passes, and reanalyze each result.

    Pre-passes lower custom syntax before AST passes inspect valid Python.
    Default passes lower explicit macros, then strip source information.
    Parsing checks syntax only; each pass is responsible for semantic safety.
    Input scripts are never executed.
    """

    def __init__(self, *, strip_info: bool = True, preserve_module_names: bool = False) -> None:
        self._pre_passes: list[PrePass] = [StringEncryptionPass(), ProtectionPass()]
        self._passes: list[BasePass] = [StripInfoPass()] if strip_info else []
        self._preserve_module_names = preserve_module_names

    def add(self, pass_: BasePass | PrePass) -> Pipeline:
        if isinstance(pass_, PrePass):
            self._pre_passes.append(pass_)
        elif isinstance(pass_, BasePass):
            self._passes.append(pass_)
        else:
            raise TypeError("Expected a BasePass or PrePass")
        return self

    def run(self, source: SourceDocument | str) -> PipelineResult:
        if isinstance(source, str):
            source = SourceDocument(source)
        applied: list[str] = []
        records: list[PassRecord] = []
        preserve_names = False
        seals = []
        for pass_ in self._pre_passes:
            edits = tuple(pass_.run(source))
            preserve_names |= getattr(pass_, "preserve_names", False)
            if getattr(pass_, 'integrity', None) is not None:
                seals.append(pass_.integrity)
            source = source.with_text(apply_replacements(source.text, edits))
            if edits:
                name = type(pass_).__name__
                applied.append(name)
                records.append(PassRecord(name, len(edits)))
        context = analyze(source)
        for pass_ in self._passes:
            edits = tuple(pass_.run(context, preserve_names=preserve_names, preserve_module_names=True)
                          if isinstance(pass_, StripInfoPass) and self._preserve_module_names
                          else pass_.run(context, preserve_names=preserve_names) if isinstance(pass_, StripInfoPass) else pass_.run(context))
            source = source.with_text(apply_replacements(source.text, edits))
            context = analyze(source)
            name = type(pass_).__name__
            applied.append(name)
            records.append(PassRecord(name, len(edits)))
        for seal in seals:
            source = seal.seal(source)
            context = analyze(source)
            applied.append('IntegrityPass')
            records.append(PassRecord('IntegrityPass', 1))
        return PipelineResult(source, context, tuple(applied), tuple(records))

    @staticmethod
    def _apply(text: str, edits: Sequence[Replacement]) -> str:
        return apply_replacements(text, edits)

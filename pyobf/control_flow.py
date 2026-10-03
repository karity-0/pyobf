"""Lower structured statement lists to basic blocks in one lexical scope."""
from __future__ import annotations

import ast
from dataclasses import dataclass, field

from .names import NameAllocator


@dataclass(frozen=True)
class LoopTargets:
    breaking: int | None = None
    continuing: int | None = None


@dataclass(frozen=True)
class Jump:
    target: int


@dataclass(frozen=True)
class Branch:
    test: ast.expr
    yes: int
    no: int


@dataclass(frozen=True)
class IteratorNext:
    iterator: str
    value: str
    target: ast.expr
    body: int
    exhausted: int
    asynchronous: bool = False


@dataclass
class BasicBlock:
    statements: list[ast.stmt] = field(default_factory=list)
    terminator: Jump | Branch | IteratorNext = field(default_factory=lambda: Jump(0))
    loop: LoopTargets = field(default_factory=LoopTargets)
    atomic: bool = False


class LoopBoundaryVisitor(ast.NodeVisitor):
    """Ignore exits owned by native nested loops or another lexical scope."""

    def __init__(self):
        self.depth = 0
        self.breaking = False
        self.continuing = False

    def visit_Break(self, node):
        self.breaking |= not self.depth

    def visit_Continue(self, node):
        self.continuing |= not self.depth

    def visit_For(self, node):
        self.depth += 1
        for statement in node.body:
            self.visit(statement)
        self.depth -= 1
        for statement in node.orelse:
            self.visit(statement)

    visit_AsyncFor = visit_For
    visit_While = visit_For

    def visit_FunctionDef(self, node):
        pass

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef


def assign(name: str, value: ast.expr) -> ast.Assign:
    return ast.Assign(targets=[ast.Name(id=name, ctx=ast.Store())], value=value)


class ControlFlowGraph:
    """Zero is exit; all other IDs identify statement/branch/loop basic blocks.

    Try/with/match remain atomic so exception, cleanup and pattern scopes are not
    flattened across their boundaries. Their enclosing-loop exits are still routed.
    """

    def __init__(self, names: NameAllocator):
        self.names = names
        self.blocks: dict[int, BasicBlock] = {}
        self.helpers: dict[str, str] = {}
        self.temporaries: list[str] = []
        self.control = names.new()
        self.external_exits: dict[bool, int] = {}

    def reserve(self) -> int:
        key = len(self.blocks) + 1
        self.blocks[key] = BasicBlock()
        return key

    def block(self, statements, terminator, loop=None, atomic=False) -> int:
        key = self.reserve()
        self.blocks[key] = BasicBlock(statements, terminator, loop or LoopTargets(), atomic)
        return key

    def helper(self, builtin: str) -> str:
        if builtin not in self.helpers:
            self.helpers[builtin] = self.names.new()
        return self.helpers[builtin]

    def temporary(self) -> str:
        name = self.names.new()
        self.temporaries.append(name)
        return name

    def exit_target(self, loop: LoopTargets, breaking: bool) -> int:
        target = loop.breaking if breaking else loop.continuing
        if target is not None:
            return target
        if breaking not in self.external_exits:
            self.external_exits[breaking] = self.block(
                [assign(self.control, ast.Constant(1 if breaking else 2))], Jump(0),
            )
        return self.external_exits[breaking]

    def lower(self, statements: list[ast.stmt], following=0, loop=None) -> int:
        loop = loop or LoopTargets()
        entry = following
        for statement in reversed(statements):
            entry = self.statement(statement, entry, loop)
        return entry

    def statement(self, node: ast.stmt, following: int, loop: LoopTargets) -> int:
        if isinstance(node, ast.If):
            yes = self.lower(node.body, following, loop)
            no = self.lower(node.orelse, following, loop)
            return self.block([], Branch(node.test, yes, no))
        if isinstance(node, ast.While):
            head = self.reserve()
            exhausted = self.lower(node.orelse, following, loop)
            body = self.lower(node.body, head, LoopTargets(following, head))
            self.blocks[head] = BasicBlock([], Branch(node.test, body, exhausted))
            return head
        if isinstance(node, (ast.For, ast.AsyncFor)):
            asynchronous = isinstance(node, ast.AsyncFor)
            self.helper('anext' if asynchronous else 'next')
            self.helper('StopAsyncIteration' if asynchronous else 'StopIteration')
            iterator, value = self.temporary(), self.temporary()
            head = self.reserve()
            otherwise = self.lower(node.orelse, following, loop)
            release = [assign(iterator, ast.Constant(None))]
            exhausted = self.block(release, Jump(otherwise))
            broken = self.block([assign(iterator, ast.Constant(None))], Jump(following))
            body = self.lower(node.body, head, LoopTargets(broken, head))
            self.blocks[head] = BasicBlock([], IteratorNext(iterator, value, node.target, body, exhausted, asynchronous))
            prepare = assign(iterator, ast.Call(
                func=ast.Name(id=self.helper('aiter' if asynchronous else 'iter'), ctx=ast.Load()),
                args=[node.iter], keywords=[],
            ))
            return self.block([prepare], Jump(head))
        if isinstance(node, ast.Break):
            return self.block([], Jump(self.exit_target(loop, True)))
        if isinstance(node, ast.Continue):
            return self.block([], Jump(self.exit_target(loop, False)))
        finder = LoopBoundaryVisitor()
        finder.visit(node)
        targets = LoopTargets(
            self.exit_target(loop, True) if finder.breaking else loop.breaking,
            self.exit_target(loop, False) if finder.continuing else loop.continuing,
        )
        return self.block([node], Jump(following), targets, finder.breaking or finder.continuing)

"""Lower structured statement lists to basic blocks in one lexical scope."""
from __future__ import annotations

import ast
import copy
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
        self.loops: list[tuple[int, set[int]]] = []

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
            before = set(self.blocks)
            body = self.lower(node.body, head, LoopTargets(following, head))
            self.blocks[head] = BasicBlock([], Branch(node.test, body, exhausted))
            self.loops.append((head, set(self.blocks) - before))
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
            before = set(self.blocks)
            body = self.lower(node.body, head, LoopTargets(broken, head))
            self.blocks[head] = BasicBlock([], IteratorNext(iterator, value, node.target, body, exhausted, asynchronous))
            self.loops.append((head, set(self.blocks) - before))
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

    def unroll(self, *, max_blocks=384, max_added=192, max_added_nodes=4096) -> None:
        """Clone loop lanes, retaining every test/next and native exit boundary.

        Work from outer loops to inner ones. Inner lane expansion is optional;
        cloned inner loops already retain all of their native graph edges.
        """
        added = 0
        added_nodes = 0
        for head, body in reversed(self.loops):
            selected = body | {head}
            lanes = self.names.random.choice((2, 3))
            cost = len(selected) * (lanes - 1)
            weight = 0
            for key in selected:
                block = self.blocks[key]
                nodes = list(block.statements)
                if isinstance(block.terminator, Branch):
                    nodes.append(block.terminator.test)
                elif isinstance(block.terminator, IteratorNext):
                    nodes.append(block.terminator.target)
                weight += sum(sum(1 for _ in ast.walk(node)) for node in nodes) + 1
            node_cost = weight * (lanes - 1)
            if cost + added > max_added or cost + len(self.blocks) > max_blocks or node_cost + added_nodes > max_added_nodes:
                continue
            originals = {key: copy.deepcopy(self.blocks[key]) for key in selected}
            mappings = [{key: key for key in selected}]
            for _ in range(lanes - 1):
                mappings.append({key: self.reserve() for key in sorted(selected)})
            for lane, mapping in enumerate(mappings):
                def target(key):
                    if key == head:
                        return mappings[(lane + 1) % lanes][head]
                    return mapping.get(key, key)

                for key, original in originals.items():
                    block = copy.deepcopy(original)
                    term = block.terminator
                    if isinstance(term, Jump):
                        block.terminator = Jump(target(term.target))
                    elif isinstance(term, Branch):
                        block.terminator = Branch(term.test, target(term.yes), target(term.no))
                    else:
                        block.terminator = IteratorNext(
                            term.iterator, term.value, term.target,
                            target(term.body), target(term.exhausted), term.asynchronous,
                        )
                    block.loop = LoopTargets(target(block.loop.breaking), target(block.loop.continuing))
                    self.blocks[mapping[key]] = block
            added += cost
            added_nodes += node_cost

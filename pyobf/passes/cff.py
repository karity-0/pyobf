from __future__ import annotations

import ast
import copy

from ..control_flow import Branch, ControlFlowGraph, IteratorNext, Jump, LoopTargets, assign
from ..names import NameAllocator
from .base import BlockPass


class _Declarations(ast.NodeTransformer):
    """Global/nonlocal are compile-time declarations; shuffled order is unsafe."""

    def __init__(self) -> None:
        self.declarations: list[ast.stmt] = []

    def visit_Global(self, node):
        self.declarations.append(node)
        return ast.copy_location(ast.Pass(), node)

    visit_Nonlocal = visit_Global

    def visit_FunctionDef(self, node):
        return node

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef


class _AtomicExits(ast.NodeTransformer):
    """Unwind native try/with/finally before resuming a CFG loop target.

    A dispatcher continue carries the pending state through Python's native cleanup
    machinery. A finally return/raise/exit can override it exactly as in the source.
    """

    def __init__(self, state: str, labels: dict[int, int], loop: LoopTargets):
        self.state, self.labels, self.loop = state, labels, loop
        self.depth = 0

    def _exit(self, node, target):
        if self.depth:
            return node
        assert target is not None
        return [assign(self.state, ast.Constant(self.labels[target])), ast.Continue()]

    def visit_Break(self, node):
        return self._exit(node, self.loop.breaking)

    def visit_Continue(self, node):
        return self._exit(node, self.loop.continuing)

    def _visit_loop(self, node):
        self.depth += 1
        node.body = self._visit_statements(node.body)
        self.depth -= 1
        node.orelse = self._visit_statements(node.orelse)
        return node

    visit_For = _visit_loop
    visit_AsyncFor = _visit_loop
    visit_While = _visit_loop

    def _visit_statements(self, statements):
        output = []
        for statement in statements:
            transformed = self.visit(statement)
            output.extend(transformed if isinstance(transformed, list) else [transformed])
        return output

    def visit_FunctionDef(self, node):
        return node

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef


class ControlFlowFlatteningPass(BlockPass):
    """Flatten branches, loop heads/bodies and exits through a randomized CFG.

    Preserve lexical scopes and native exception/context-manager boundaries. A
    balanced dispatcher avoids deep elif chains; iteration catches exhaustion only
    around next(), never around user target assignment or the original loop body.
    """

    def run(self, statements: list[ast.stmt], names: NameAllocator, *, builtin_proxy=None, morph=False) -> list[ast.stmt]:
        if not statements:
            return [ast.Pass()]
        statements = copy.deepcopy(statements)
        declarations = _Declarations()
        statements = [declarations.visit(statement) for statement in statements]
        graph = ControlFlowGraph(names)
        entry = graph.lower(statements)
        if morph:
            graph.unroll()
        state = names.new()
        labels = dict(zip(graph.blocks, names.random.sample(range(1, 1 << 30), len(graph.blocks))))
        labels[0] = 0

        def jump(owner, target):
            return assign(state, ast.BinOp(
                left=ast.Name(id=state, ctx=ast.Load()), op=ast.BitXor(),
                right=ast.Constant(labels[owner] ^ labels[target]),
            ))

        def render(key, block):
            exits = _AtomicExits(state, labels, block.loop)
            body = [exits.visit(statement) for statement in block.statements]
            if block.atomic:
                # A caught/suppressed exception may cancel a pending native exit.
                # Successful fallthrough must then use this block's normal edge.
                body.append(assign(state, ast.Constant(labels[key])))
            terminator = block.terminator
            if isinstance(terminator, Jump):
                body.append(jump(key, terminator.target))
            elif isinstance(terminator, Branch):
                body.append(ast.If(
                    test=terminator.test,
                    body=[jump(key, terminator.yes)], orelse=[jump(key, terminator.no)],
                ))
            elif isinstance(terminator, IteratorNext):
                next_call = ast.Call(
                    func=ast.Name(id=graph.helpers['anext' if terminator.asynchronous else 'next'], ctx=ast.Load()),
                    args=[ast.Name(id=terminator.iterator, ctx=ast.Load())], keywords=[],
                )
                if terminator.asynchronous:
                    next_call = ast.Await(value=next_call)
                body.append(ast.Try(
                    body=[assign(terminator.value, next_call)],
                    handlers=[ast.ExceptHandler(
                        type=ast.Name(id=graph.helpers['StopAsyncIteration' if terminator.asynchronous else 'StopIteration'], ctx=ast.Load()),
                        name=None, body=[jump(key, terminator.exhausted)],
                    )],
                    orelse=[
                        ast.Assign(targets=[terminator.target], value=ast.Name(id=terminator.value, ctx=ast.Load())),
                        assign(terminator.value, ast.Constant(None)),
                        jump(key, terminator.body),
                    ], finalbody=[],
                ))
            return body

        leaves = sorted((labels[key], render(key, block)) for key, block in graph.blocks.items())

        def dispatch(branches):
            if len(branches) == 1:
                return branches[0][1]
            middle = len(branches) // 2
            predicate = ast.Compare(
                left=ast.Name(id=state, ctx=ast.Load()), ops=[ast.Lt()],
                comparators=[ast.Constant(branches[middle][0])],
            )
            return [ast.If(test=predicate, body=dispatch(branches[:middle]), orelse=dispatch(branches[middle:]))]

        temporaries = list(graph.helpers.values()) + graph.temporaries
        output = declarations.declarations + [assign(state, ast.Constant(labels[entry]))]
        if temporaries:
            output.append(ast.Assign(
                targets=[ast.Name(id=name, ctx=ast.Store()) for name in temporaries], value=ast.Constant(None),
            ))
        if graph.external_exits:
            output.append(assign(graph.control, ast.Constant(0)))
        protected = []
        if graph.helpers:
            if builtin_proxy is None:
                protected.append(ast.ImportFrom(
                    module='builtins', names=[ast.alias(name=name, asname=alias) for name, alias in graph.helpers.items()], level=0,
                ))
            else:
                protected.extend(assign(alias, builtin_proxy.expression(name, builtin_only=True)) for name, alias in graph.helpers.items())
        protected.append(ast.While(
            test=ast.Compare(left=ast.Name(id=state, ctx=ast.Load()), ops=[ast.NotEq()], comparators=[ast.Constant(0)]),
            body=dispatch(leaves), orelse=[],
        ))
        for breaking, code in ((True, 1), (False, 2)):
            if breaking in graph.external_exits:
                protected.append(ast.If(
                    test=ast.Compare(left=ast.Name(id=graph.control, ctx=ast.Load()), ops=[ast.Eq()], comparators=[ast.Constant(code)]),
                    body=[ast.Break() if breaking else ast.Continue()], orelse=[],
                ))
        cleanup = [state, *temporaries, *([graph.control] if graph.external_exits else [])]
        output.append(ast.Try(
            body=protected, handlers=[], orelse=[],
            finalbody=[ast.Delete(targets=[ast.Name(id=name, ctx=ast.Del()) for name in cleanup])],
        ))
        return output

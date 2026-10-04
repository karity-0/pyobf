"""Bounded structural rewrites, with lexical and exception scopes kept intact."""
from __future__ import annotations

import ast
import copy

from ..control_flow import LoopBoundaryVisitor
from .base import BlockPass
from .native_loops import unroll_native_loop


def _docstring(node):
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def _constant(node):
    return isinstance(node, ast.Constant) and type(node.value) in (str, bytes, int, float, complex, bool, type(None))


def _mark(nodes):
    for node in nodes:
        node._morph_generated = True
    return nodes


class MorphPass(BlockPass):
    """Diversify branches/blocks, unroll native loops and extract closed helpers.

    Helper extraction accepts only initial, fresh, constant local assignments;
    function fusion accepts only constant-return functions, retaining signatures,
    defaults, docstrings and original function objects. Arbitrary expressions are
    deliberately never reordered, reassociated or moved to another call frame.
    """

    def run(self, statements, names, *, builtin_proxy=None, cff_selected=False,
            reflective=False, class_scope=False):
        if class_scope or reflective:
            return statements
        transformer = _Morph(names, builtin_proxy, cff_selected)
        return transformer.statements(copy.deepcopy(statements), merge=True)


class _Morph(ast.NodeTransformer):
    def __init__(self, names, proxy, defer_loops):
        self.names, self.proxy = names, proxy
        self.defer_loops = defer_loops
        self.loop_depth = 0
        self.budget = 96

    def visit(self, node):
        if getattr(node, '_morph_generated', False):
            return node
        return super().visit(node)

    def spend(self):
        if self.budget <= 0:
            return False
        self.budget -= 1
        return True

    def statements(self, body, *, merge=False):
        if merge:
            body = self.merge_functions(body)
        output = []
        for node in body:
            result = self.visit(node)
            output.extend(result if isinstance(result, list) else [result])
        # Partition straight-line runs without introducing a lexical/call scope.
        partitioned, index = [], 0
        while index < len(output):
            run = []
            for node in output[index:index + 4]:
                if getattr(node, '_morph_generated', False) or not isinstance(node, (ast.Assign, ast.AugAssign, ast.Expr)) or _docstring(node):
                    break
                run.append(node)
            if len(run) >= 2 and self.spend():
                width = self.names.random.randint(1, len(run))
                partitioned.extend(_mark([ast.If(test=ast.Constant(True), body=run[:width], orelse=[])]))
                index += width
            else:
                partitioned.append(output[index])
                index += 1
        return partitioned

    def visit_ClassDef(self, node):
        # Class namespaces can have user-defined assignment/lookup behavior.
        return node

    def visit_FunctionDef(self, node):
        previous = self.defer_loops, self.loop_depth
        self.defer_loops, self.loop_depth = False, 0
        self.split_function(node)
        node.body = self.statements(node.body, merge=True)
        self.defer_loops, self.loop_depth = previous
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def _atomic(self, node):
        # The surrounding CFF keeps these boundaries native. Rewrite their
        # contents separately, without moving statements across cleanup scopes.
        previous = self.defer_loops, self.loop_depth
        self.defer_loops, self.loop_depth = False, 0
        node = self.generic_visit(node)
        self.defer_loops, self.loop_depth = previous
        return node

    visit_Try = _atomic
    visit_With = _atomic
    visit_AsyncWith = _atomic
    visit_Match = _atomic
    visit_TryStar = _atomic

    def visit_If(self, node):
        rewrite = self.spend()
        tail = []
        while rewrite and node.body and node.orelse and isinstance(node.body[-1], (ast.Assign, ast.Expr)) and ast.dump(node.body[-1]) == ast.dump(node.orelse[-1]):
            tail.insert(0, node.body.pop())
            node.orelse.pop()
        node.body = node.body or [ast.Pass()]
        node.body = self.statements(node.body)
        node.orelse = self.statements(node.orelse)
        if rewrite and self.names.random.choice((True, False)):
            node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
            node.body, node.orelse = node.orelse or [ast.Pass()], node.body
        return [node, *tail]

    def _loop(self, node):
        literal = None
        if isinstance(node, ast.For) and isinstance(node.iter, (ast.List, ast.Tuple)) and len(node.iter.elts) <= 6 and all(_constant(item) for item in node.iter.elts):
            exits = LoopBoundaryVisitor()
            for statement in node.body:
                exits.visit(statement)
            has_declarations = any(isinstance(item, (ast.Global, ast.Nonlocal)) for statement in node.body for item in ast.walk(statement))
            weight = sum(sum(1 for _ in ast.walk(statement)) for statement in node.body)
            if not exits.breaking and not exits.continuing and not has_declarations and weight * max(1, len(node.iter.elts)) <= 1024:
                literal = node.iter.elts
        outermost = self.loop_depth == 0
        self.loop_depth += 1
        node.body = self.statements(node.body)
        self.loop_depth -= 1
        node.orelse = self.statements(node.orelse)
        if literal is not None and not self.defer_loops and self.spend():
            expanded = []
            for value in literal:
                expanded.append(ast.Assign(targets=[copy.deepcopy(node.target)], value=copy.deepcopy(value)))
                expanded.extend(copy.deepcopy(node.body))
            if expanded:
                # Keep expressions from accidentally becoming function docstrings.
                replacement = [ast.If(test=ast.Constant(True), body=expanded, orelse=[])]
            else:
                # Empty loops still establish local bindings, cells and generator
                # flags at compile time. Retain these in a dead same-scope block.
                replacement = [ast.If(test=ast.Constant(False), body=[
                    ast.Assign(targets=[node.target], value=ast.Constant(None)), *node.body,
                ], orelse=[])]
            return _mark(replacement + node.orelse)
        if not self.defer_loops and outermost and self.spend():
            transformed = unroll_native_loop(node, self.names, builtin_proxy=self.proxy)
            return _mark(transformed) if isinstance(transformed, list) else transformed
        return node

    visit_For = _loop
    visit_AsyncFor = _loop
    visit_While = _loop

    def split_function(self, function):
        # Retain decorators/annotations, generators and all declaration scopes.
        if function.decorator_list or any(isinstance(n, (ast.Global, ast.Nonlocal, ast.Yield, ast.YieldFrom)) for n in ast.walk(function)):
            return
        start = int(bool(function.body and _docstring(function.body[0])))
        parameters = {arg.arg for arg in (*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs)}
        parameters.update(arg.arg for arg in (function.args.vararg, function.args.kwarg) if arg)
        chunk, identifiers = [], set()
        for node in function.body[start:start + 6]:
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and _constant(node.value)):
                break
            name = node.targets[0].id
            if name in parameters or name in identifiers:
                break
            chunk.append(node)
            identifiers.add(name)
        if len(chunk) < 2 or not self.spend():
            return
        helper = self.names.new()
        definition = ast.FunctionDef(
            name=helper, args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=[ast.Return(value=ast.Tuple(elts=[node.value for node in chunk], ctx=ast.Load()))],
            decorator_list=[],
        )
        invoke = ast.Assign(
            targets=[ast.Tuple(elts=[node.targets[0] for node in chunk], ctx=ast.Store())],
            value=ast.Call(func=ast.Name(id=helper, ctx=ast.Load()), args=[], keywords=[]),
        )
        replacement = _mark([definition, ast.Try(
            body=[invoke], handlers=[], orelse=[],
            finalbody=[ast.Delete(targets=[ast.Name(id=helper, ctx=ast.Del())])],
        )])
        function.body[start:start + len(chunk)] = replacement

    def merge_functions(self, body):
        # Only consecutive undecorated constant-return functions are candidates.
        output, index = [], 0
        while index < len(body):
            group = []
            for node in body[index:index + 6]:
                if not isinstance(node, ast.FunctionDef) or node.decorator_list or getattr(node, '_morph_generated', False):
                    break
                content = node.body[int(bool(node.body and _docstring(node.body[0]))):]
                if len(content) != 1 or not isinstance(content[0], ast.Return) or not _constant(content[0].value):
                    break
                group.append((node, content[0].value))
            if len(group) < 2 or not self.spend():
                output.append(body[index])
                index += 1
                continue
            helper, selector = self.names.new(), self.names.new()
            slots = self.names.random.sample(range(1, 1 << 30), len(group))
            choices = list(zip(slots, group))
            self.names.random.shuffle(choices)
            dispatch = [ast.If(
                test=ast.Compare(left=ast.Name(id=selector, ctx=ast.Load()), ops=[ast.Eq()], comparators=[ast.Constant(slot)]),
                body=[ast.Return(value=value)], orelse=[],
            ) for slot, (_, value) in choices]
            definition = ast.FunctionDef(
                name=helper, args=ast.arguments(posonlyargs=[], args=[ast.arg(arg=selector)], kwonlyargs=[], kw_defaults=[], defaults=[]),
                body=dispatch, decorator_list=[],
            )
            output.extend(_mark([definition]))
            for slot, (function, _) in zip(slots, group):
                function.body[-1] = ast.Return(value=ast.Call(
                    func=ast.Name(id=helper, ctx=ast.Load()), args=[ast.Constant(slot)], keywords=[],
                ))
                output.append(function)
            index += len(group)
        return output

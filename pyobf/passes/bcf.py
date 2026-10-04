"""Add bounded bogus control-flow edges around real code, without flattening."""
from __future__ import annotations

import ast
import copy

from .base import BlockPass
from .bogus_paths import BogusPaths


def _docstring(node):
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def _declaration(node):
    return isinstance(node, (ast.Global, ast.Nonlocal)) or isinstance(node, ast.ImportFrom) and node.module == '__future__'


def _frame_weight(node):
    if isinstance(node, (ast.Try, ast.ExceptHandler)):
        return 2  # Conservative allowance for handler/finally compiler blocks.
    return int(isinstance(node, (ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)))


def _native_depth(node):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
        return 0
    return _frame_weight(node) + max((_native_depth(child) for child in ast.iter_child_nodes(node)), default=0)


class BogusControlFlowPass(BlockPass):
    """Route live blocks through opaque integer predicates and fake successors.

    No user expressions or bindings are cloned into dead paths. Native scopes,
    iteration and cleanup remain intact; temporary seeds are always deleted.
    Run after CFF so its already flattened branches keep their original topology.
    """

    def run(self, statements, names, *, reflective=False, class_scope=False):
        if reflective or class_scope:
            return statements
        return _BogusFlow(names).statements(copy.deepcopy(statements))


class _BogusFlow(ast.NodeTransformer):
    def __init__(self, names):
        self.names = names
        self.remaining = 24
        self.frames = 0
        self.paths = BogusPaths(names.random)

    def visit(self, node):
        if getattr(node, '_bcf_generated', False):
            return node
        weight = _frame_weight(node)
        self.frames += weight
        result = super().visit(node)
        self.frames -= weight
        return result

    def statements(self, body, *, docstring=False):
        output = []
        index = 0
        if docstring and body and _docstring(body[0]):
            output.append(body[0])
            index = 1
        # Visit leaves first: CFF state edges receive bogus successors before
        # enclosing dispatch branches, without growing unbounded guard chains.
        body = body[:index] + [self.visit(node) for node in body[index:]]
        while index < len(body):
            node = body[index]
            if _declaration(node) or getattr(node, '_bcf_generated', False) or self.remaining <= 0 or self.frames + _native_depth(node) >= 14:
                output.append(node)
                index += 1
                continue
            self.remaining -= 1
            width = self.names.random.randint(1, 3)
            selected = []
            for node in body[index:index + width]:
                if _declaration(node) or getattr(node, '_bcf_generated', False) or self.frames + _native_depth(node) >= 14:
                    break
                selected.append(node)
            output.extend(self.wrap(selected))
            index += len(selected)
        return output

    def generic_visit(self, node):
        for field, value in ast.iter_fields(node):
            if isinstance(value, list) and value and all(isinstance(item, ast.stmt) for item in value):
                setattr(node, field, self.statements(value))
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, ast.AST):
                        self.visit(item)
            elif isinstance(value, ast.AST):
                self.visit(value)
        return node

    def visit_FunctionDef(self, node):
        previous, self.frames = self.frames, 0
        node.body = self.statements(node.body, docstring=True)
        self.frames = previous
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        # Do not write seeds into metaclass-controlled class namespaces. Methods
        # have ordinary function-local namespaces and can be transformed safely.
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.visit(item)
        return node

    def wrap(self, statements):
        seed = self.names.new()
        initial = self.names.random.randrange(256, 1 << 24)
        mask = self.names.random.randrange(256, 1 << 24)
        divisor = self.names.random.randrange(3, 257)
        shift = self.names.random.randrange(1, 9)
        predicates = [
            f'({seed} * ({seed} + 1)) & 1 == 0',
            f'(({seed} ^ {mask}) ^ {mask}) == {seed}',
            f'(({seed} << {shift}) >> {shift}) == {seed}',
            f'({seed} * {seed}) % 4 in (0, 1)',
            f'({seed} - {seed} % {divisor}) % {divisor} == 0',
        ]
        predicate = ast.parse(self.names.random.choice(predicates), mode='eval').body
        fake = self.paths.build(seed)
        if self.names.random.choice((False, True)):
            predicate = ast.UnaryOp(op=ast.Not(), operand=predicate)
            live, dead = fake, statements
        else:
            live, dead = statements, fake
        result = [
            ast.Assign(targets=[ast.Name(id=seed, ctx=ast.Store())], value=ast.Constant(initial)),
            ast.Try(body=[ast.If(test=predicate, body=live, orelse=dead)], handlers=[], orelse=[],
                    finalbody=[ast.Delete(targets=[ast.Name(id=seed, ctx=ast.Del())])]),
        ]
        for node in result:
            node._bcf_generated = True
        return result

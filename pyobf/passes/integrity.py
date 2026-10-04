"""Seal final source structure and derive protected integers from its digest."""
from __future__ import annotations

import ast
import hashlib
import re
import secrets
import struct

from ..edits import Replacement, apply_replacements


def canonical(node, pool):
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == pool:
        return ('payload', pool)
    if isinstance(node, ast.AST):
        return (type(node).__name__, tuple((field, canonical(value, pool))
                for field, value in ast.iter_fields(node) if field != 'lineno' and value is not None and value != []))
    if isinstance(node, list):
        return tuple(canonical(value, pool) for value in node)
    if type(node) is int:
        return ('integer', hex(node))
    return node


def source_digest(text, pool):
    tree = ast.parse(text, type_comments=True)
    return hashlib.sha256(repr(canonical(tree, pool)).encode('utf-8', 'surrogatepass')).digest()


class IntegrityPass:
    """Plan constant sites before rendering; seal only after every other pass.

    Each site has an independent mask. Encoded payload is the only excluded AST
    assignment, so filling it cannot change the digest or create a hash cycle.
    Runtime requires actual source, including the GUI launcher's source snapshot.
    """

    def __init__(self, names):
        self.names = names
        def allocate():
            while True:
                # Preserve names through strip without triggering class mangling.
                name = '__' + names.new() + '__'
                if name not in names.used and name not in names.source:
                    names.used.add(name)
                    return name
        self.decode, self.pool, self.key, self.normalize, self.load = [allocate() for _ in range(5)]
        self.ast, self.hashlib, self.linecache, self.tokenize, self.sys = [allocate() for _ in range(5)]
        self.builtins = allocate()
        self.struct, self.floating = allocate(), allocate()
        self.io = allocate()
        self.salt = secrets.token_bytes(32)
        self.values = {}

    def expression(self, value):
        slot = self.names.random.randrange(1, 1 << 32)
        while slot in self.values:
            slot = self.names.random.randrange(1, 1 << 32)
        self.values[slot] = value
        return ast.Call(func=ast.Name(id=self.decode, ctx=ast.Load()), args=[ast.Constant(slot)], keywords=[])

    def protect(self, statements):
        plan = self

        class Constants(ast.NodeTransformer):
            def visit_Constant(self, node):
                def floating(value):
                    bits = int.from_bytes(struct.pack('>d', value), 'big')
                    return ast.Call(func=ast.Name(id=plan.floating, ctx=ast.Load()), args=[plan.expression(bits)], keywords=[])
                if type(node.value) is int:
                    return ast.copy_location(plan.expression(node.value), node)
                if type(node.value) is float:
                    return ast.copy_location(floating(node.value), node)
                if type(node.value) is complex:
                    return ast.copy_location(ast.Call(func=ast.Attribute(value=ast.Name(id=plan.builtins, ctx=ast.Load()), attr='complex', ctx=ast.Load()),
                                                     args=[floating(node.value.real), floating(node.value.imag)], keywords=[]), node)
                return node

            def visit_arg(self, node):
                return node  # Annotation expressions are runtime/public metadata.

            def visit_AnnAssign(self, node):
                node.target = self.visit(node.target)
                if node.value is not None:
                    node.value = self.visit(node.value)
                return node

            def visit_FunctionDef(self, node):
                node.decorator_list = [self.visit(item) for item in node.decorator_list]
                node.args.defaults = [self.visit(item) for item in node.args.defaults]
                node.args.kw_defaults = [self.visit(item) if item is not None else None for item in node.args.kw_defaults]
                node.body = [self.visit(item) for item in node.body]
                return node

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_ClassDef(self, node):
                node.decorator_list = [self.visit(item) for item in node.decorator_list]
                node.bases = [self.visit(item) for item in node.bases]
                node.keywords = [self.visit(item) for item in node.keywords]
                node.body = [self.visit(item) for item in node.body]
                return node  # Keep type-parameter annotation metadata intact.

            def visit_Lambda(self, node):
                node.args.defaults = [self.visit(item) for item in node.args.defaults]
                node.args.kw_defaults = [self.visit(item) if item is not None else None for item in node.args.kw_defaults]
                node.body = self.visit(node.body)
                return node

            def visit_Match(self, node):
                node.subject = self.visit(node.subject)
                for case in node.cases:
                    if case.guard is not None:
                        case.guard = self.visit(case.guard)
                    case.body = [self.visit(item) for item in case.body]
                return node  # Patterns require literal syntax, never decoder calls.

            def visit_TypeAlias(self, node):
                return node

        transformer = Constants()
        output = [transformer.visit(statement) for statement in statements]
        # A region with no integers must also depend on integrity. Removing this
        # expression still corrupts the other sites because it changes the digest.
        gate = ast.Expr(value=ast.Subscript(
            value=ast.Tuple(elts=[ast.Constant(None)], ctx=ast.Load()),
            slice=self.expression(0), ctx=ast.Load(),
        ))
        return [gate, *output]

    def header(self):
        header = f'''import builtins as {self.builtins}
import ast as {self.ast}
import hashlib as {self.hashlib}
import linecache as {self.linecache}
import tokenize as {self.tokenize}
import sys as {self.sys}
import struct as {self.struct}
import io as {self.io}

def {self.normalize}(_n):
    if isinstance(_n, {self.ast}.Assign) and len(_n.targets) == 1 and isinstance(_n.targets[0], {self.ast}.Name) and _n.targets[0].id == {self.pool!r}:
        return ('payload', {self.pool!r})
    if isinstance(_n, {self.ast}.AST):
        return (type(_n).__name__, tuple((_f, {self.normalize}(_v)) for _f, _v in {self.ast}.iter_fields(_n) if _f != 'lineno' and _v is not None and _v != []))
    if isinstance(_n, list):
        return tuple({self.normalize}(_v) for _v in _n)
    if type(_n) is int:
        return ('integer', hex(_n))
    return _n

def {self.load}():
    _filename = {self.sys}._getframe(1).f_code.co_filename
    _cached = {self.linecache}.cache.get(_filename)
    try:
        if _cached is not None and len(_cached) == 4 and _cached[1] is None:
            _source = ''.join(_cached[2])
        else:
            with {self.builtins}.open(_filename, 'rb') as _file:
                _data = _file.read()
            _header = _data.replace(b'\\r\\n', b'\\n').replace(b'\\r', b'\\n')
            _encoding, _ = {self.tokenize}.detect_encoding({self.io}.BytesIO(_header).readline)
            _source = _data.decode(_encoding)
        return {self.hashlib}.sha256(repr({self.normalize}({self.ast}.parse(_source, type_comments=True))).encode('utf-8', 'surrogatepass')).digest()
    except (OSError, UnicodeError, SyntaxError) as _error:
        raise RuntimeError('Integrity requires readable Python source') from _error

{self.key} = {self.load}()
{self.pool} = {{}}

def {self.decode}(_slot):
    return {self.pool}[_slot] ^ int.from_bytes({self.hashlib}.blake2s({self.key} + {self.salt!r} + _slot.to_bytes(8, 'big')).digest(), 'big')

def {self.floating}(_value):
    return {self.struct}.unpack('>d', _value.to_bytes(8, 'big'))[0]
'''
        builtin_names = {'isinstance', 'len', 'type', 'tuple', 'list', 'repr', 'int', 'hex',
                         'OSError', 'UnicodeError', 'SyntaxError', 'RuntimeError'}
        alias = self.builtins
        class Builtins(ast.NodeTransformer):
            def visit_Name(self, node):
                if node.id in builtin_names:
                    return ast.copy_location(ast.Attribute(value=ast.Name(id=alias, ctx=ast.Load()),
                                                           attr=node.id, ctx=node.ctx), node)
                return node
        return ast.unparse(ast.fix_missing_locations(Builtins().visit(ast.parse(header)))) + '\n'

    def seal(self, source):
        tree = ast.parse(source.text)
        prefix, index = [], 0
        if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(tree.body[0].value, ast.Constant) and isinstance(tree.body[0].value.value, str):
            prefix.append(tree.body[0])
            index = 1
        while index < len(tree.body) and isinstance(tree.body[index], ast.ImportFrom) and tree.body[index].module == '__future__':
            prefix.append(tree.body[index])
            index += 1
        if prefix:
            insertion = source.position(prefix[-1].end_lineno, 0) + len(source.line_text(prefix[-1].end_lineno))
        elif tree.body:
            first = tree.body[0]
            row = min([first.lineno, *(item.lineno for item in getattr(first, 'decorator_list', []))])
            insertion = source.position(row, 0)
        else:
            insertion = len(source.text)
        match = re.search(r'\r\n|\r|\n', source.text)
        newline = match.group() if match else '\n'
        header = self.header().replace('\n', newline)
        separator = newline if insertion and not source.text[:insertion].endswith(('\n', '\r')) else ''
        source = source.with_text(source.text[:insertion] + separator + header + newline + source.text[insertion:])
        digest = source_digest(source.text, self.pool)
        entries = []
        for slot, value in sorted(self.values.items()):
            mask = int.from_bytes(hashlib.blake2s(digest + self.salt + slot.to_bytes(8, 'big')).digest(), 'big')
            encoded = value ^ mask
            entries.append(f'{slot}: {hex(encoded)}')
        tree = ast.parse(source.text)
        table = next(node for node in tree.body if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == self.pool)
        start, end = source.span(table.value)
        sealed = source.with_text(apply_replacements(source.text, [Replacement(start, end, '{' + ', '.join(entries) + '}')]))
        if source_digest(sealed.text, self.pool) != digest:
            raise RuntimeError('Integrity sealing changed protected structure')
        return sealed

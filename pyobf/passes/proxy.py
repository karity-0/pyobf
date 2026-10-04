"""Route protected builtin references through an encrypted module-level catalog."""
from __future__ import annotations

import ast
import builtins

from .strip_info import Resolver
from .string_encryption import encrypted_string_expression


BUILTIN_NAMES = {name for name in dir(builtins) if not name.startswith('__')} | {'__import__', '__build_class__'}


def encrypted_string(value):
    return ast.parse(encrypted_string_expression(value), mode='eval').body


class BuiltinProxyPass:
    """Return real builtin objects rather than wrappers that change caller frames.

    Native scope analysis protects local/free/module bindings and class namespaces.
    Global overrides and mutations of the builtin dictionary remain visible at use
    time. One catalog is shared across all protected regions in a source document.
    """

    def __init__(self, context, names):
        self.names = names
        self.function, self.catalog, self.globals, self.builtins = [names.new() for _ in range(4)]
        self.slot, self.name = '_i', '_n'
        self.entries = {}
        self.resolver = Resolver(context)
        self.disabled = False
        try:
            self.resolver.visit(context.tree)
        except ValueError:
            self.disabled = True
        self.disabled |= any(isinstance(node, ast.Name) and node.id == '__builtins__' and isinstance(node.ctx, (ast.Store, ast.Del)) for node in ast.walk(context.tree))

    def expression(self, name, *, builtin_only=False):
        key = (name, builtin_only)
        if key not in self.entries:
            used = set(self.entries.values())
            slot = self.names.random.randrange(1, 1 << 30)
            while slot in used:
                slot = self.names.random.randrange(1, 1 << 30)
            self.entries[key] = slot
        return ast.Call(func=ast.Name(id=self.function, ctx=ast.Load()), args=[ast.Constant(self.entries[key])], keywords=[])

    def transform(self, tree, regions):
        proxy = self

        class References(ast.NodeTransformer):
            def visit_Name(self, node):
                if proxy.disabled or not isinstance(node.ctx, ast.Load) or node.id not in BUILTIN_NAMES:
                    return node
                if not any(region.start.row < node.lineno < region.end.row for region in regions):
                    return node
                scope = proxy.resolver.name_scopes.get(node)
                if scope is None or scope.table.get_type() == 'class' or proxy.resolver.names.get(node) is not None or node in proxy.resolver.annotation_nodes:
                    return node
                try:
                    symbol = scope.table.lookup(node.id)
                except KeyError:
                    return node
                if not symbol.is_global():
                    return node
                expression = proxy.expression(node.id)
                if node.id == 'super':
                    try:
                        class_cell = scope.table.lookup('__class__').is_free()
                    except KeyError:
                        class_cell = False
                    if class_cell:
                        # Keep the compiler's __class__ cell for zero-argument super.
                        expression = ast.IfExp(test=ast.Constant(False), body=ast.Name(id='__class__', ctx=ast.Load()), orelse=expression)
                return ast.copy_location(expression, node)

        return References().visit(tree)

    def definitions(self):
        if not self.entries:
            return []
        pairs = sorted(self.entries.items(), key=lambda item: item[1])
        table = ast.Assign(
            targets=[ast.Name(id=self.catalog, ctx=ast.Store())],
            value=ast.Dict(
                keys=[ast.Constant(slot) for _, slot in pairs],
                values=[ast.Tuple(elts=[encrypted_string(name), ast.Constant(not builtin_only)], ctx=ast.Load()) for (name, builtin_only), _ in pairs],
            ),
        )
        # Bootstrap from a function's own dictionaries; identifiers and attributes
        # are decoded once at the top, never exposed at the protected call site.
        function = ast.parse(f'''
def {self.function}({self.slot}):
    {self.name} = {self.catalog}[{self.slot}][0]
    if {self.catalog}[{self.slot}][1] and {self.name} in {self.globals}:
        return {self.globals}[{self.name}]
    if {self.name} in {self.builtins}:
        return {self.builtins}[{self.name}]
    raise {self.builtins}[{encrypted_string_expression('NameError')}]("name '" + {self.name} + "' is not defined", name={self.name}) from None
''').body[0]

        def namespace(attribute, target):
            return ast.Assign(
                targets=[ast.Name(id=target, ctx=ast.Store())],
                value=ast.Call(
                    func=ast.Attribute(value=ast.Attribute(value=ast.Name(id=self.function, ctx=ast.Load()), attr='__class__', ctx=ast.Load()), attr='__getattribute__', ctx=ast.Load()),
                    args=[ast.Name(id=self.function, ctx=ast.Load()), encrypted_string(attribute)], keywords=[],
                ),
            )

        return [table, function, namespace('__globals__', self.globals), namespace('__builtins__', self.builtins)]

"""Strip comments and shorten statically resolved bindings without changing APIs."""
from __future__ import annotations

import ast
import bisect
import copy
import itertools
import keyword
import re
import string
import symtable
import tokenize
import unicodedata
from dataclasses import dataclass, field

from .base import BasePass
from ..edits import Replacement


@dataclass(eq=False)
class Scope:
    table: symtable.SymbolTable
    parent: Scope | None
    node: ast.AST
    children: list[Scope] = field(default_factory=list)
    bindings: dict[str, Binding] = field(default_factory=dict)
    used_tables: set[int] = field(default_factory=set)


@dataclass(eq=False)
class Binding:
    scope: Scope
    name: str
    references: list[tuple[ast.AST, str]] = field(default_factory=list)
    keep: bool = False
    replacement: str | None = None


class Resolver(ast.NodeVisitor):
    def __init__(self, context):
        self.context = context
        self.root = Scope(context.symbols, None, context.tree)
        self.scope = self.root
        self.scopes = [self.root]
        self.functions = []
        self.names = {}  # AST Name -> lexical binding (including comprehension captures).
        self.annotation_names = set()

    def binding(self, name, scope=None):
        scope = scope or self.scope
        try:
            symbol = scope.table.lookup(name)
        except KeyError:
            return None
        kind = scope.table.get_type()
        if kind != 'module' and symbol.is_global():
            return self.binding(name, self.root)
        if symbol.is_free() or symbol.is_nonlocal():
            parent = scope.parent
            while parent:
                if parent.table.get_type() != 'class':
                    try:
                        found = parent.table.lookup(name)
                    except KeyError:
                        found = None
                    if found and found.is_local():
                        return self.binding(name, parent)
                parent = parent.parent
            return None
        if not symbol.is_local():
            return None
        if name not in scope.bindings:
            scope.bindings[name] = Binding(scope, name)
        return scope.bindings[name]

    def reference(self, node, name, kind='name'):
        binding = self.binding(name)
        if binding:
            binding.references.append((node, kind))
        return binding

    def enter(self, node, name):
        for table in self.scope.table.get_children():
            if id(table) not in self.scope.used_tables and table.get_name() == name and table.get_lineno() == node.lineno:
                self.scope.used_tables.add(id(table))
                child = Scope(table, self.scope, node)
                self.scope.children.append(child)
                self.scopes.append(child)
                self.scope = child
                return
        # New language constructs must fail closed instead of guessing a scope.
        raise ValueError('Unsupported symbol-table scope')

    def annotation(self, node):
        if node is not None:
            for item in ast.walk(node):
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    self.annotation_names.update(re.findall(r'[^\W\d]\w*', item.value))
            self.visit(node)

    def visit_Name(self, node):
        self.names[node] = self.reference(node, node.id)

    def visit_FunctionDef(self, node):
        binding = self.reference(node, node.name, 'definition')
        if binding and node.decorator_list:
            binding.keep = True  # Decorators can register a callable by its name.
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in node.args.defaults + [x for x in node.args.kw_defaults if x is not None]:
            self.visit(default)
        for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs + [x for x in (node.args.vararg, node.args.kwarg) if x]:
            self.annotation(arg.annotation)
        self.annotation(node.returns)
        parent = self.scope
        self.enter(node, node.name)
        self.functions.append((node, binding, self.scope))
        self.arguments(node.args)
        for statement in node.body:
            self.visit(statement)
        self.scope = parent

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        for default in node.args.defaults + [x for x in node.args.kw_defaults if x is not None]:
            self.visit(default)
        parent = self.scope
        self.enter(node, 'lambda')
        self.functions.append((node, None, self.scope))
        self.arguments(node.args)
        self.visit(node.body)
        self.scope = parent

    def arguments(self, args):
        for arg in args.posonlyargs + args.args + args.kwonlyargs + [x for x in (args.vararg, args.kwarg) if x]:
            self.reference(arg, arg.arg, 'argument')

    def visit_ClassDef(self, node):
        self.reference(node, node.name, 'class')
        for item in node.decorator_list + node.bases + node.keywords:
            self.visit(item)
        parent = self.scope
        self.enter(node, node.name)
        for statement in node.body:
            self.visit(statement)
        self.scope = parent

    def comprehension(self, node, name):
        self.visit(node.generators[0].iter)  # Evaluated in the enclosing scope.
        parent = self.scope
        self.enter(node, name)
        for index, generator in enumerate(node.generators):
            if index:
                self.visit(generator.iter)
            self.visit(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)
        self.scope = parent

    def visit_ListComp(self, node):
        self.comprehension(node, 'listcomp')

    def visit_SetComp(self, node):
        self.comprehension(node, 'setcomp')

    def visit_DictComp(self, node):
        self.comprehension(node, 'dictcomp')

    def visit_GeneratorExp(self, node):
        self.comprehension(node, 'genexpr')

    def visit_Global(self, node):
        for name in node.names:
            self.reference(node, name, 'declaration')

    visit_Nonlocal = visit_Global

    def visit_Import(self, node):
        for alias in node.names:
            binding = self.reference(alias, alias.asname or alias.name.split('.')[0], 'import')
            if binding and '.' in alias.name and not alias.asname:
                binding.keep = True  # "import a.b" binds a, not a.b.

    def visit_ImportFrom(self, node):
        if node.module != '__future__':
            self.visit_Import(node)

    def visit_ExceptHandler(self, node):
        if node.type:
            self.visit(node.type)
        if node.name:
            self.reference(node, node.name, 'exception')
        for statement in node.body:
            self.visit(statement)

    def visit_MatchAs(self, node):
        if node.pattern:
            self.visit(node.pattern)
        if node.name:
            self.reference(node, node.name, 'capture')

    def visit_MatchStar(self, node):
        if node.name:
            self.reference(node, node.name, 'capture')

    def visit_MatchMapping(self, node):
        for item in node.keys + node.patterns:
            self.visit(item)
        if node.rest:
            self.reference(node, node.rest, 'capture')

    def visit_AnnAssign(self, node):
        self.visit(node.target)
        self.annotation(node.annotation)
        if node.value:
            self.visit(node.value)


REFLECTIVE_NAMES = {'eval', 'exec', 'globals', 'locals', 'vars', 'dir', 'inspect', 'compile', '__import__'}
REFLECTIVE_ATTRIBUTES = {'_getframe', 'currentframe', 'f_locals', 'f_globals', '__dict__', '__code__', '__globals__', '__closure__', '__annotations__', '__name__', '__qualname__', 'modules', 'import_module'}


def uses_reflection(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in REFLECTIVE_NAMES:
            return True
        if isinstance(node, ast.Attribute) and node.attr in REFLECTIVE_ATTRIBUTES:
            return True
        if isinstance(node, ast.alias) and node.name.split('.')[0] in REFLECTIVE_NAMES:
            return True
        if isinstance(node, ast.ImportFrom) and any(alias.name == '*' for alias in node.names):
            return True
        if getattr(node, 'type_params', None):
            return True
    return False


def short_names():
    alphabet = string.ascii_lowercase + string.ascii_uppercase
    for size in itertools.count(1):
        for letters in itertools.product(alphabet, repeat=size):
            yield ''.join(letters)


class StripInfoPass(BasePass):
    """Frequency-weighted lexical renaming; public attributes/signatures stay safe.

    Reflection and star imports disable lexical renaming. Docstrings remain runtime
    data. This removes source names, not Python code objects or traceback locations.
    """

    def run(self, context):
        self.context = context
        self.tokens = context.tokens
        self.token_ranges = [(context.source.position(*t.start), context.source.position(*t.end), t) for t in self.tokens]
        self.token_starts = [a for a, _, _ in self.token_ranges]
        edits = self.comments()
        if uses_reflection(context.tree):
            return edits
        resolver = Resolver(context)
        try:
            resolver.visit(context.tree)
        except ValueError:
            return edits
        bindings = [b for scope in resolver.scopes for b in scope.bindings.values()]
        class_names = {b.name for scope in resolver.scopes if scope.table.get_type() == 'class' for b in scope.bindings.values()}
        exported = set()
        for node in context.tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and any(isinstance(t, ast.Name) and t.id == '__all__' for t in (node.targets if isinstance(node, ast.Assign) else [node.target])):
                if isinstance(node.value, (ast.List, ast.Tuple)) and all(isinstance(x, ast.Constant) and isinstance(x.value, str) for x in node.value.elts):
                    exported.update(x.value for x in node.value.elts)
                else:
                    exported.update(resolver.root.bindings)
        if any(isinstance(node, ast.Name) and node.id == '__all__' and not isinstance(node.ctx, ast.Store) or isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id == '__all__' for node in ast.walk(context.tree)):
            exported.update(resolver.root.bindings)
        keep_module = any(isinstance(n, ast.Name) and n.id == '__main__' or isinstance(n, ast.alias) and n.name == '__main__' or isinstance(n, ast.ImportFrom) and n.module == '__main__' for n in ast.walk(context.tree))
        for b in bindings:
            if b.name.startswith('__') or b.name in resolver.annotation_names or b.scope.table.get_type() == 'class' or any(kind == 'class' for _, kind in b.references) or b.scope is resolver.root and (keep_module or b.name in exported or b.name in class_names):
                b.keep = True
        parents = {child: node for node in ast.walk(context.tree) for child in ast.iter_child_nodes(node)}
        for function, binding, scope in resolver.functions:
            calls = []
            private = binding is not None and not binding.keep and not function.decorator_list
            immediate = parents.get(function)
            if isinstance(function, ast.Lambda) and isinstance(immediate, ast.Call) and immediate.func is function and not any(kw.arg is None for kw in immediate.keywords):
                calls = [immediate]
                private = True
            if private and binding is not None:
                for node, kind in binding.references:
                    if kind == 'definition' and node is function:
                        continue
                    parent = parents.get(node)
                    if kind != 'name' or not isinstance(node.ctx, ast.Load) or not isinstance(parent, ast.Call) or parent.func is not node:
                        private = False
                        break
                    calls.append(parent)
                if any(kw.arg is None for call in calls for kw in call.keywords):
                    private = False
            for arg in function.args.args + function.args.kwonlyargs:
                b = scope.bindings.get(arg.arg)
                if b:
                    b.keep |= not private
            if private:
                for call in calls:
                    for kw in call.keywords:
                        b = scope.bindings.get(kw.arg)
                        if b and any(kind == 'argument' for _, kind in b.references):
                            b.references.append((kw, 'keyword'))
        reserved = set(keyword.kwlist) | set(keyword.softkwlist)
        reserved.update(t.string for t in self.tokens if t.type == tokenize.NAME)
        reserved.update(b.name for b in bindings)
        reserved.update(name for scope in resolver.scopes for name in scope.table.get_identifiers())
        reserved.update(node.arg for node in ast.walk(context.tree) if isinstance(node, ast.keyword) and node.arg)
        allocator = (name for name in short_names() if name not in reserved)
        name = next(allocator)
        for b in sorted(bindings, key=lambda b: -len(b.references) * len(b.name)):
            if b.keep or len(b.name) <= 1:
                continue
            extra = sum(4 + len(b.name) for n, kind in b.references if kind == 'import' and n.asname is None)
            if len(name) >= len(b.name) or len(b.references) * (len(b.name) - len(name)) <= extra:
                continue
            b.replacement = name
            name = next(allocator)
        name_changes = {n: b.replacement for n, b in resolver.names.items() if b and b.replacement}
        fstrings = []
        for node in ast.walk(context.tree):
            if isinstance(node, ast.JoinedStr) and any(n in name_changes for n in ast.walk(node)):
                ancestor = parents.get(node)
                while ancestor and not isinstance(ancestor, ast.JoinedStr):
                    ancestor = parents.get(ancestor)
                if ancestor is None:
                    fstrings.append(node)
        fstring_ranges = [context.source.span(node) for node in fstrings]
        for b in bindings:
            if b.replacement:
                for node, kind in b.references:
                    replacement = self.reference_edit(node, kind, b.name, b.replacement)
                    if replacement and not any(start <= replacement.start and replacement.end <= end for start, end in fstring_ranges):
                        edits.append(replacement)
        # A debug f-string must keep its original label: f'{long=}' -> f'long={a!r}'.
        changes_by_position = {(n.lineno, n.col_offset, n.id): name for n, name in name_changes.items()}
        argument_changes = {(n.lineno, n.col_offset, n.arg): b.replacement for b in bindings if b.replacement for n, kind in b.references if kind == 'argument'}
        keyword_changes = {(n.lineno, n.col_offset, n.arg): b.replacement for b in bindings if b.replacement for n, kind in b.references if kind == 'keyword'}
        class FStringNames(ast.NodeTransformer):
            def visit_Name(self, node):
                node.id = changes_by_position.get((node.lineno, node.col_offset, node.id), node.id)
                return node
            def visit_arg(self, node):
                node.arg = argument_changes.get((node.lineno, node.col_offset, node.arg), node.arg)
                return self.generic_visit(node)
            def visit_keyword(self, node):
                node.arg = keyword_changes.get((node.lineno, node.col_offset, node.arg), node.arg)
                return self.generic_visit(node)
        for node, (start, end) in zip(fstrings, fstring_ranges):
            edits.append(Replacement(start, end, ast.unparse(FStringNames().visit(copy.deepcopy(node)))))
        return list({(edit.start, edit.end): edit for edit in edits}.values())

    def comments(self):
        return [Replacement(start, end, '') for start, end, token in self.token_ranges if token.type == tokenize.COMMENT and not (token.start[0] == 1 and token.string.startswith('#!')) and not (token.start[0] <= 2 and re.search(r'coding[:=]\s*[-\w.]+', token.string)) and not token.string.startswith('# type:')]

    def reference_edit(self, node, kind, original, renamed):
        start, end = self.context.source.span(node)
        if kind == 'name':
            return Replacement(start, end, renamed)
        tokens = [(a, b, t) for a, b, t in self.token_ranges[bisect.bisect_left(self.token_starts, start):bisect.bisect_right(self.token_starts, end)] if b <= end]
        if kind == 'import' and node.asname is None:
            return Replacement(start, end, self.context.source.text[start:end] + ' as ' + renamed)
        if kind == 'exception':
            marker = next(i for i, (_, _, t) in enumerate(tokens) if t.string == 'as')
            a, b, _ = tokens[marker + 1]
            return Replacement(a, b, renamed)
        matches = [(a, b, t) for a, b, t in tokens if t.type == tokenize.NAME and unicodedata.normalize('NFKC', t.string) == original]
        if kind in ('definition', 'class'):
            marker = next(i for i, (_, _, t) in enumerate(tokens) if t.string in ('def', 'class'))
            a, b, _ = tokens[marker + 1]
            return Replacement(a, b, renamed)
        if matches:
            a, b, _ = matches[-1] if kind in ('import', 'exception', 'capture') else matches[0]
            return Replacement(a, b, renamed)
        return None

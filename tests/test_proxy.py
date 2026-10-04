import ast
import contextlib
import io
import re
import textwrap
import unittest
from unittest.mock import patch
from pyobf import Pipeline, SourceDocument
from pyobf.protection import find_protection_regions


def source(text):
    return textwrap.dedent(text).lstrip('\n')


def unmarked(text):
    return '\n'.join('' if re.match(r'\s*@protect_(?:start|end)\b', line) else line for line in text.splitlines())


class BuiltinProxyTests(unittest.TestCase):
    def output(self, code):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            exec(code, {})
        return stream.getvalue()

    def compare(self, code, strip_info=True):
        expected = self.output(unmarked(code))
        for _ in range(2):
            result = Pipeline(strip_info=strip_info).run(code)
            self.assertEqual(self.output(result.source.text), expected)
        return result

    def test_proxy_option_and_invalid_options(self):
        for options in ('proxy', 'cff, proxy', 'proxy, junk', 'cff, junk, proxy'):
            code = f'@protect_start({options})\nprint(ord("A"))\n@protect_end'
            self.assertIn('proxy', find_protection_regions(SourceDocument(code))[0].start.options)
            self.compare(code)
        for options in ('proxy, proxy', 'unknown', 'proxy=1', '"proxy"'):
            with self.assertRaises(SyntaxError):
                Pipeline().run(f'@protect_start({options})\nx = 1\n@protect_end')

    def test_plaintext_names_are_absent_at_calls_and_in_string_constants(self):
        result = self.compare('@protect_start(proxy)\nprint(ord("A"), len("abc"), isinstance(3, int), chr(65))\n@protect_end')
        names = {n.id for n in ast.walk(result.analysis.tree) if isinstance(n, ast.Name)}
        strings = {n.value for n in ast.walk(result.analysis.tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        for name in ('ord', 'len', 'isinstance', 'int', 'chr', 'print', 'NameError', '__globals__', '__builtins__'):
            self.assertNotIn(name, strings)
            self.assertNotIn(name, names)
        self.assertTrue(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Call) for n in ast.walk(result.analysis.tree.body[-1])))

    def test_values_types_exception_classes_and_callbacks(self):
        self.compare(source('''
            @protect_start(proxy)
            conversion = ord
            integer_type = int
            values = list(map(conversion, 'ABC'))
            print(values, integer_type('17'), isinstance(values, list))
            print(sorted(set([3, 1, 3])), tuple(range(3)), dict(one=1))
            print(sum([1, 2]), min(3, 4), max(3, 4), abs(-2), round(1.25, 1))
            print(bytes([65]), bool(1), str(4), float('2.5'), complex(2, 3))
            try: raise ValueError('test')
            except ValueError as error: print(type(error).__name__, str(error))
            print(Ellipsis is ..., NotImplemented is NotImplemented)
            @protect_end
        '''))

    def test_local_global_free_and_comprehension_shadowing(self):
        self.compare(source('''
            ord = lambda value: 99
            def f(len):
                @protect_start(proxy)
                print(ord('A'), len('abc'), [chr(n) for n in (65, 66)])
                @protect_end
            f(lambda value: 17)
            def outer():
                chr = lambda value: 'local'
                def inner():
                    @protect_start(proxy)
                    print(chr(65), [ord for ord in (1, 2)])
                    @protect_end
                return inner
            outer()()
        '''))

    def test_dynamic_globals_and_builtin_mutation(self):
        self.compare(source('''
            import builtins
            previous = builtins.ord
            try:
                @protect_start(proxy)
                print(ord('A'))
                globals()['ord'] = lambda value: 77
                print(ord('A'))
                del globals()['ord']
                builtins.ord = lambda value: 12
                print(ord('A'))
                @protect_end
            finally: builtins.ord = previous
        '''))

    def test_missing_builtin_raises_nameerror_with_original_name(self):
        self.compare(source('''
            import builtins
            original = builtins.ord
            try:
                del builtins.ord
                @protect_start(proxy)
                try: ord('A')
                except NameError as error: print(error.name, str(error))
                @protect_end
            finally: builtins.ord = original
        '''))

    def test_locals_eval_exec_and_vars_keep_original_caller_frame(self):
        result = self.compare(source('''
            def f():
                long_variable = 7
                @protect_start(proxy)
                print(locals()['long_variable'], eval('long_variable + 1'))
                exec('print(long_variable + 2)')
                print(vars()['long_variable'])
                @protect_end
            f()
        '''))
        self.assertIn('long_variable = 7', result.source.text)

    def test_super_retains_implicit_class_cell(self):
        self.compare(source('''
            class Base:
                def value(self): return 3
            class Child(Base):
                def value(self):
                    "method doc"
                    @protect_start(proxy)
                    parent = super()
                    result = parent.value() + 1
                    @protect_end
                    return result
                def captured(self):
                    @protect_start(proxy)
                    helper = lambda: super(Child, self).value()
                    @protect_end
                    return helper()
            print(Child().value(), Child().captured(), Child.value.__doc__)
        '''))

    def test_class_namespace_is_preserved(self):
        self.compare(source('''
            class Meta(type):
                @classmethod
                def __prepare__(cls, name, bases): return {'ord': lambda value: 88}
            class Example(metaclass=Meta):
                @protect_start(proxy)
                answer = ord('A')
                value: int = 3
                @protect_end
            print(Example.answer, Example.__annotations__['value'] is int)
        '''))

    def test_unprotected_source_is_preserved_without_strip(self):
        prefix = '# keep outside\nfirst = ord("B")\n'
        suffix = '\nlast = ord("C")  # keep too\n'
        result = Pipeline(strip_info=False).run(prefix + '@protect_start(proxy)\nvalue = ord("A")\n@protect_end' + suffix)
        self.assertTrue(result.source.text.startswith('# keep outside\n'))
        self.assertIn('first = ord("B")\n', result.source.text)
        self.assertTrue(result.source.text.endswith(suffix))
        self.assertNotIn('value = ord', result.source.text)

    def test_nested_and_sibling_regions_share_one_catalog(self):
        result = self.compare(source('''
            @protect_start(proxy)
            first = ord('A')
            @protect_start(cff)
            for n in range(2): first += len('a')
            @protect_end
            @protect_end
            @protect_start(proxy)
            second = ord('B')
            @protect_end
            print(first, second)
        '''), strip_info=False)
        tables = [n.value for n in result.analysis.tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict)]
        self.assertEqual(len(tables), 1)
        self.assertFalse(any(isinstance(n, ast.ImportFrom) and n.module == 'builtins' for n in ast.walk(result.analysis.tree)))

    def test_proxy_cff_helpers_ignore_global_next_iter_shadowing(self):
        self.compare(source('''
            next = iter = StopIteration = lambda *args: (_ for _ in ()).throw(RuntimeError('shadowed'))
            @protect_start(cff, junk, proxy)
            values = []
            for n in range(4):
                if n % 2: continue
                values.append(ord('A') + n)
            print(values)
            @protect_end
        '''))

    def test_header_order_docstring_shebang_cookie_future(self):
        code = source('''
            #!/usr/bin/env python
            # coding: utf-8
            "module doc"
            from __future__ import annotations
            @protect_start(proxy)
            print(ord('A'))
            @protect_end
        ''')
        result = self.compare(code, strip_info=False)
        self.assertEqual(result.analysis.tree.body[0].value.value, 'module doc')
        self.assertEqual(result.analysis.tree.body[1].module, '__future__')
        self.assertTrue(result.source.text.startswith('#!/usr/bin/env python\n# coding: utf-8\n"module doc"\nfrom __future__ import annotations\n'))

    def test_protected_module_docstring_still_leads(self):
        code = '@protect_start(proxy)\n"module doc"\nprint(ord("A"))\n@protect_end\nprint(__doc__)'
        result = self.compare(code)
        self.assertEqual(result.analysis.tree.body[0].value.value, 'module doc')

    def test_header_does_not_split_first_decorators(self):
        self.compare(source('''
            @staticmethod
            def f():
                @protect_start(proxy)
                return ord('A')
                @protect_end
            print(f.__func__())
        '''))

    def test_future_annotations_are_not_rewritten(self):
        self.compare(source('''
            from __future__ import annotations
            @protect_start(proxy)
            def f(value: int) -> str: return chr(value)
            @protect_end
            print(f(65), f.__annotations__)
        '''), strip_info=False)

    def test_encoding_bom_newline_roundtrip(self):
        for encoding in ('utf-8-sig', 'cp949'):
            for newline in ('\r\n', '\r'):
                cookie = '# coding: cp949\n' if encoding == 'cp949' else ''
                code = cookie + '@protect_start(proxy)\nprint(ord("A"), "안녕")\n@protect_end\n'
                document = SourceDocument.from_bytes(code.replace('\n', newline).encode(encoding))
                result = Pipeline(strip_info=False).run(document).source
                self.assertEqual(result.encoding, document.encoding)
                self.assertIn(newline, result.text)
                self.assertEqual(self.output(result.to_bytes()), '65 안녕\n')

    def test_empty_shadowed_or_builtin_rebinding_regions_add_no_header(self):
        for code in ('@protect_start(proxy)\n@protect_end', 'ord = lambda value: 3\n@protect_start(proxy)\nx = ord("A")\n@protect_end', '__builtins__ = {"ord": lambda value: 9}\n@protect_start(proxy)\nx = ord("A")\n@protect_end'):
            result = Pipeline(strip_info=False).run(code)
            self.assertFalse(any(isinstance(n, ast.FunctionDef) for n in result.analysis.tree.body))

    def test_build_does_not_execute_input(self):
        code = '@protect_start(proxy)\nopen("must-not-create", "w")\nraise RuntimeError("must not execute")\n@protect_end'
        with patch('builtins.open', side_effect=AssertionError('executed input')):
            self.assertTrue(Pipeline().run(code).source.text)

    def test_pipeline_reuse_resets_reflection_flag(self):
        pipeline = Pipeline()
        pipeline.run('@protect_start(proxy)\nx = eval("1")\n@protect_end')
        result = pipeline.run('long_variable_name = 3\nprint(long_variable_name)')
        self.assertNotIn('long_variable_name', result.source.text)

    def test_builds_use_fresh_keys_and_slots(self):
        code = '@protect_start(proxy)\nprint(ord("A"))\n@protect_end'
        self.assertNotEqual(Pipeline().run(code).source.text, Pipeline().run(code).source.text)


if __name__ == '__main__':
    unittest.main()

import ast
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from pyobf import Pipeline, SourceDocument
from pyobf.edits import Replacement, apply_replacements
from pyobf.gui.runner import LAUNCHER
from test_protection import unmarked


class IntegrityTests(unittest.TestCase):
    def run_source(self, document, *, launcher=False):
        if isinstance(document, str):
            document = SourceDocument(document)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sealed.py'
            path.write_bytes(document.to_bytes())
            args = [sys.executable, '-u', str(path)]
            if launcher:
                # The original file intentionally contains different source.
                original = Path(directory) / 'original.py'
                original.write_text('raise AssertionError("wrong source")', encoding='utf-8')
                args = [sys.executable, '-u', '-c', LAUNCHER, str(path), str(original)]
            return subprocess.run(args, capture_output=True, text=True, encoding='utf-8', timeout=8,
                                  env={**__import__('os').environ, 'PYTHONIOENCODING': 'utf-8'})

    def compare(self, code, *, strip_info=True):
        code = textwrap.dedent(code).lstrip('\n')
        expected = self.run_source(unmarked(code))
        self.assertEqual(expected.returncode, 0, expected.stderr)
        result = Pipeline(strip_info=strip_info).run(code)
        actual = self.run_source(result.source)
        self.assertEqual(actual.returncode, 0, actual.stderr)
        self.assertEqual(actual.stdout, expected.stdout)
        self.assertEqual(result.applied_passes[-1], 'IntegrityPass')
        return result

    def remove_gates(self, document):
        tree = ast.parse(document.text)
        edits = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Subscript) and isinstance(node.value.value, ast.Tuple) and len(node.value.value.elts) == 1 and isinstance(node.value.value.elts[0], ast.Constant) and node.value.value.elts[0].value is None and isinstance(node.value.slice, ast.Call):
                start, end = document.span(node)
                edits.append(Replacement(start, end, 'pass'))
        self.assertTrue(edits)
        return document.with_text(apply_replacements(document.text, edits))

    def test_integer_float_complex_and_negative_zero(self):
        self.compare('''
            import math
            @protect_start(integrity)
            CONST = 24
            values = [CONST, 0, -37, 2**100, 1.25, -0.0, 1e309, 3j]
            print(values, math.copysign(1.0, values[5]))
            @protect_end
        ''')

    def test_each_combination_including_native_morph(self):
        for options in ('integrity', 'integrity, proxy', 'integrity, cff', 'integrity, bcf', 'integrity, morph', 'integrity, junk', 'integrity, cff, bcf, proxy, morph, junk'):
            with self.subTest(options=options):
                self.compare(f'''
                    def f(limit):
                        @protect_start({options})
                        total = 24
                        for i in range(limit):
                            if i == 5: break
                            if i % 2: continue
                            total += i + ord('A') - 65
                        else:
                            total += 10
                        @protect_end
                        return total
                    print([f(n) for n in (0, 3, 8)])
                ''')

    def test_string_decoder_mask_is_derived_even_after_gate_removal(self):
        code = '@protect_start(integrity)\nprint(@{"hello world"})\n@protect_end'
        result = Pipeline(strip_info=False).run(code)
        normal = self.run_source(result.source)
        self.assertEqual((normal.returncode, normal.stdout), (0, 'hello world\n'), normal.stderr)
        changed = self.run_source(self.remove_gates(result.source))
        self.assertNotEqual(changed.stdout, normal.stdout)
        self.assertNotEqual(changed.returncode, 0)

    def test_code_edit_corrupts_constants_and_cannot_be_fixed_by_deleting_gate(self):
        result = self.compare('''
            @protect_start(integrity)
            CONST = 24
            print(CONST)
            assert CONST == 24
            @protect_end
        ''', strip_info=False)
        changed = result.source.with_text(result.source.text.replace('print(CONST)', 'print(CONST + 1)'))
        self.assertNotEqual(self.run_source(changed).returncode, 0)
        bypass = self.run_source(self.remove_gates(result.source))
        self.assertNotEqual(bypass.returncode, 0)
        self.assertNotEqual(bypass.stdout, '24\n')

    def test_proxy_lookup_does_not_cancel_a_corrupted_key(self):
        result = self.compare('''
            @protect_start(integrity, proxy)
            print(ord('A'))
            @protect_end
        ''', strip_info=False)
        bypass = self.run_source(self.remove_gates(result.source))
        self.assertNotEqual(bypass.returncode, 0)
        self.assertIn('KeyError', bypass.stderr)

    def test_cff_transition_and_junk_numbers_are_decoder_expressions(self):
        result = self.compare('''
            @protect_start(integrity, cff, junk)
            total = 24
            for i in range(4): total += i
            print(total)
            @protect_end
        ''', strip_info=False)
        xors = [node for node in ast.walk(ast.parse(result.source.text)) if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitXor) and isinstance(node.left, ast.Name)]
        self.assertTrue(xors)
        self.assertTrue(any(isinstance(node.right, ast.Call) and isinstance(node.right.func, ast.Name) for node in xors))

    def test_empty_and_string_only_regions_also_fail_after_semantic_edit(self):
        for code in ('@protect_start(integrity)\n@protect_end\nprint("ok")', '@protect_start(integrity)\nprint("ok")\n@protect_end'):
            result = self.compare(code)
            self.assertNotEqual(self.run_source(result.source.text.replace('"ok"', '"changed"').replace("'ok'", "'changed'")).returncode, 0)

    def test_hash_covers_source_outside_region_and_runtime_helpers(self):
        result = self.compare('outside = 7\n@protect_start(integrity)\nprint(24)\n@protect_end', strip_info=False)
        self.assertNotEqual(self.run_source(result.source.text.replace('outside = 7', 'outside = 8')).returncode, 0)
        self.assertNotEqual(self.run_source(result.source.text.replace("'payload'", "'different'", 1)).returncode, 0)

    def test_whitespace_comments_and_newlines_are_canonicalized(self):
        result = self.compare('@protect_start(integrity)\nprint(24)\n@protect_end', strip_info=False)
        changed = result.source.with_text('# added comment\n\n' + result.source.text.replace('print(', 'print( ').replace('\n', '\r\n'))
        actual = self.run_source(changed)
        self.assertEqual((actual.returncode, actual.stdout), (0, '24\n'), actual.stderr)

    def test_large_integers_and_type_ignore_locations_are_stable(self):
        code = 'large = 0x' + 'f' * 5000 + '\n@protect_start(integrity)\nvalue = 24  # type: ignore\nprint(value, large.bit_length())\n@protect_end'
        result = self.compare(code, strip_info=False)
        actual = self.run_source('# extra line\n' + result.source.text)
        self.assertEqual((actual.returncode, actual.stdout), (0, '24 20000\n'), actual.stderr)

    def test_payload_edits_change_the_restored_value_without_a_hash_cycle(self):
        result = self.compare('@protect_start(integrity)\nCONST = 24\nprint(CONST)\n@protect_end', strip_info=False)
        tree = ast.parse(result.source.text)
        assignment = next(node for node in tree.body if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'CONST')
        slot = assignment.value.args[0].value
        table = next(node for node in tree.body if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict))
        value = next(value for key, value in zip(table.value.keys, table.value.values) if key.value == slot)
        start, end = result.source.span(value)
        original = ast.literal_eval(value)
        changed = result.source.with_text(apply_replacements(result.source.text, [Replacement(start, end, hex(original ^ 1))]))
        actual = self.run_source(changed)
        self.assertEqual((actual.returncode, actual.stdout), (0, '25\n'), actual.stderr)

    def test_header_order_annotations_patterns_and_decorators(self):
        self.compare('''
            "module doc"
            from __future__ import annotations
            def decorate(f): return f
            @protect_start(integrity)
            @decorate
            def f(value: tuple[int, int] = (1, 2)) -> int:
                "function doc"
                match value:
                    case (1, x): return x + 24
                    case _: return 0
            @protect_end
            print(__doc__, f.__doc__, f.__annotations__, f())
        ''', strip_info=False)

    def test_nested_regions_closures_and_generator_cleanup(self):
        self.compare('''
            events = []
            @protect_start(integrity, morph)
            def f():
                a = 2
                b = 3
                def nested():
                    @protect_start(integrity, proxy)
                    return a + b + ord('A')
                    @protect_end
                return nested
            def generator():
                @protect_start(integrity, cff)
                for i in range(3):
                    try: yield i
                    finally: events.append(i)
                @protect_end
            @protect_end
            g = generator()
            print(f()(), next(g))
            g.close()
            print(events)
        ''')

    def test_module_and_function_docstrings_are_kept(self):
        self.compare('''
            @protect_start(integrity)
            "module doc"
            @protect_end
            def f():
                @protect_start(integrity)
                "function doc"
                return 24
                @protect_end
            print(__doc__, f.__doc__, f())
        ''', strip_info=False)

    def test_builtin_shadowing_does_not_break_the_runtime(self):
        self.compare('''
            int = tuple = list = len = type = repr = isinstance = 'shadow'
            @protect_start(integrity)
            value = 24
            print(value, 1.25, 2j)
            @protect_end
        ''', strip_info=False)

    def test_class_and_method_literals_do_not_mangle_helper_names(self):
        self.compare('''
            @protect_start(integrity)
            class C:
                value = 24
                fractional = 1.25
                def f(self):
                    @protect_start(integrity, cff, proxy)
                    result = self.value + ord('A')
                    @protect_end
                    return result
                @staticmethod
                def g(): return 2j
            @protect_end
            print(C.value, C.fractional, C().f(), C.g())
        ''')

    def test_bom_legacy_encoding_and_cr_only_files(self):
        for encoding, bom in (('utf-8', b'\xef\xbb\xbf'), ('cp949', b'')):
            for newline in ('\r', '\r\n'):
                text = f'# coding: {encoding}\n@protect_start(integrity, proxy)\nprint(24, "안녕")\n@protect_end\n'.replace('\n', newline)
                document = SourceDocument.from_bytes(bom + text.encode(encoding))
                result = Pipeline(strip_info=False).run(document)
                self.assertEqual(result.source.encoding, document.encoding)
                self.assertTrue(result.source.to_bytes().startswith(bom))
                actual = self.run_source(result.source)
                self.assertEqual((actual.returncode, actual.stdout), (0, '24 안녕\n'), actual.stderr)

    def test_gui_launcher_uses_executed_snapshot_instead_of_original_file(self):
        result = self.compare('@protect_start(integrity, proxy)\nprint(24)\n@protect_end')
        actual = self.run_source(result.source, launcher=True)
        self.assertEqual((actual.returncode, actual.stdout), (0, '24\n'), actual.stderr)

    def test_unavailable_source_fails_closed(self):
        result = Pipeline().run('@protect_start(integrity)\nprint(24)\n@protect_end')
        with self.assertRaisesRegex(RuntimeError, 'readable Python source'):
            exec(compile(result.source.text, '<no-readable-source>', 'exec'), {})

    def test_sealing_runs_after_custom_passes_and_never_executes_input(self):
        from pyobf.passes import BasePass, Replacement
        class LastPass(BasePass):
            def run(self, context):
                return [Replacement(len(context.source.text), len(context.source.text), '\nextra = 19\n')]
        result = Pipeline().add(LastPass()).run('@protect_start(integrity)\nprint(24)\n@protect_end')
        self.assertEqual(result.applied_passes[-2:], ('LastPass', 'IntegrityPass'))
        self.assertEqual(self.run_source(result.source).returncode, 0)
        pipeline = Pipeline()
        pipeline.run('@protect_start(integrity)\nraise RuntimeError("never execute")\n@protect_end')
        self.assertNotIn('IntegrityPass', pipeline.run('print(24)').applied_passes)
        outputs = [pipeline.run('@protect_start(integrity)\nprint(24)\n@protect_end').source.text for _ in range(3)]
        self.assertEqual(len(set(outputs)), 3)


if __name__ == '__main__':
    unittest.main()

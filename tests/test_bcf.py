import ast
import textwrap
import unittest
from unittest.mock import patch

import test_cff_cfg as cfg_tests
from pyobf import Pipeline, SourceDocument
from pyobf.analysis import analyze
from pyobf.names import NameAllocator
from pyobf.passes import BogusControlFlowPass
from pyobf.passes.bogus_paths import BogusPaths
from test_protection import unmarked


class BogusControlFlowTests(unittest.TestCase):
    def compare(self, code, evaluate, *, options='bcf', runs=3):
        code = textwrap.dedent(code).lstrip('\n').replace('@protect_start(bcf)', f'@protect_start({options})')
        original = {}
        exec(unmarked(code), original)
        expected = evaluate(original)
        for _ in range(runs):
            result = Pipeline(strip_info=False).run(code)
            actual = {}
            exec(result.source.text, actual)
            self.assertEqual(evaluate(actual), expected)
        return result

    def test_opaque_branch_contains_real_code_and_dead_code_has_no_user_bindings(self):
        result = self.compare('@protect_start(bcf)\nresult = 24\n@protect_end', lambda ns: ns['result'])
        tree = ast.parse(result.source.text)
        branch = tree.body[1].body[0]
        self.assertIsInstance(branch, ast.If)
        self.assertNotIsInstance(branch.test, ast.Constant)
        arms = [branch.body, branch.orelse]
        live = [arm for arm in arms if any(isinstance(node, ast.Name) and node.id == 'result' for statement in arm for node in ast.walk(statement))]
        self.assertEqual(len(live), 1)
        fake = next(arm for arm in arms if arm is not live[0])
        self.assertTrue(fake)
        self.assertTrue(all(node.id.startswith('_p') for statement in fake for node in ast.walk(statement) if isinstance(node, ast.Name)))

    def test_bcf_alone_never_invokes_cff(self):
        with patch('pyobf.passes.cff.ControlFlowFlatteningPass.run', side_effect=AssertionError('BCF cannot invoke CFF')):
            self.compare('''
                @protect_start(bcf)
                values = []
                for i in range(5):
                    if i % 2: continue
                    values.append(i)
                @protect_end
            ''', lambda ns: ns['values'])

    def test_side_effects_are_not_duplicated_in_dead_paths(self):
        self.compare('''
            events = []
            class Value:
                def __bool__(self):
                    events.append('truth')
                    return True
            def call(value):
                events.append(value)
                return value
            @protect_start(bcf)
            value = call(1)
            if Value():
                value += call(2)
            else:
                value += call(9)
            @protect_end
        ''', lambda ns: (ns['value'], ns['events']))

    def test_cleanup_on_return_raise_break_continue_and_global_nonlocal(self):
        self.compare('''
            counter = 0
            def factory():
                total = 3
                def f(limit):
                    @protect_start(bcf)
                    global counter
                    nonlocal total
                    for i in range(limit):
                        if i % 2: continue
                        total += i
                        counter += 1
                        if i == 4: break
                    try:
                        if limit < 0: raise ValueError('negative')
                        return total
                    finally:
                        counter += 1
                    @protect_end
                return f
            run = factory()
        ''', lambda ns: ([ns['run'](n) for n in (0, 3, 8)], ns['counter']))
        result = Pipeline(strip_info=False).run('@protect_start(bcf)\nraise ValueError("raised")\n@protect_end')
        namespace = {}
        with self.assertRaisesRegex(ValueError, 'raised'):
            exec(result.source.text, namespace)
        self.assertFalse(any(name.startswith('_p') for name in namespace))

    def test_docs_classes_methods_and_decorator_order(self):
        self.compare('''
            @protect_start(bcf)
            "module doc"
            @protect_end
            events = []
            def decorate(f):
                events.append('decorate')
                return f
            @protect_start(bcf)
            class C:
                "class doc"
                value = 24
                @decorate
                def f(self):
                    "method doc"
                    return self.value + 2
            @decorate
            def f():
                "function doc"
                return C().f()
            @protect_end
        ''', lambda ns: (ns['f'](), ns['f'].__doc__, ns['C'].__doc__, ns['C'].f.__doc__, ns['__doc__'], ns['events']))

    def test_reflection_and_custom_class_namespaces_are_kept_native(self):
        result = self.compare('''
            def f():
                @protect_start(bcf)
                x = 24
                result = dict(locals())
                @protect_end
                return result
        ''', lambda ns: ns['f']())
        self.assertNotIn('finally:', result.source.text)
        self.compare('''
            events = []
            class Mapping(dict):
                def __setitem__(self, k, v):
                    events.append(k)
                    super().__setitem__(k, v)
            class Meta(type):
                @classmethod
                def __prepare__(m, n, b): return Mapping()
            class C(metaclass=Meta):
                if True:
                    @protect_start(bcf)
                    a = 24
                    b = 26
                    @protect_end
        ''', lambda ns: (ns['C'].a, ns['C'].b, ns['events']))

    def test_adjacent_nested_and_empty_regions(self):
        self.compare('''
            def f(n):
                @protect_start(bcf)
                result = n
                @protect_start(bcf, cff)
                for i in range(3): result += i
                @protect_end
                @protect_end
                @protect_start(bcf)
                result *= 2
                @protect_end
                return result
        ''', lambda ns: ns['f'](4))
        for options in ('bcf', 'bcf, cff', 'bcf, junk, morph, proxy'):
            exec(Pipeline().run(f'@protect_start({options})\n@protect_end').source.text, {})

    def test_source_boundaries_and_encoding_are_preserved(self):
        for newline in ('\r\n', '\r'):
            prefix = '# coding: cp949\ndef f(n):\n    before= "안녕"\n'.replace('\n', newline)
            suffix = '\n    after= "keep"\n    return before, result, after\n'.replace('\n', newline)
            body = '    @protect_start(bcf)\n    result = n + 24\n    @protect_end'.replace('\n', newline)
            document = SourceDocument.from_bytes((prefix + body + suffix).encode('cp949'))
            result = Pipeline(strip_info=False).run(document)
            self.assertTrue(result.source.text.startswith(prefix))
            self.assertTrue(result.source.text.endswith(suffix))
            self.assertEqual(result.source.encoding, 'cp949')
            namespace = {}
            exec(result.source.to_bytes(), namespace)
            self.assertEqual(namespace['f'](2), ('안녕', 26, 'keep'))

    def test_all_predicate_variants_and_growth_budget(self):
        context = analyze(SourceDocument('value = 24'))
        names = NameAllocator(context.symbols, context.source.text)
        choices = names.random.choice
        for variant in range(5):
            def choose(options):
                return options[variant] if len(options) == 5 else choices(options)
            with patch.object(names.random, 'choice', side_effect=choose):
                body = BogusControlFlowPass().run(context.tree.body, names)
                namespace = {}
                exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), '<bcf>', 'exec'), namespace)
                self.assertEqual(namespace['value'], 24)
                self.assertFalse(any(name.startswith('_p') for name in namespace))
        code = '@protect_start(bcf)\nvalue = 0\n' + 'value += 1\n' * 600 + '@protect_end'
        result = Pipeline(strip_info=False).run(code)
        self.assertLessEqual(sum(isinstance(node, ast.Try) and any(isinstance(item, ast.Delete) for item in node.finalbody) for node in ast.walk(result.analysis.tree)), 24)
        namespace = {}
        exec(result.source.text, namespace)
        self.assertEqual(namespace['value'], 600)

    def test_fake_families_have_distinct_shapes_and_execute_in_isolation(self):
        context = analyze(SourceDocument('value = 24'))
        paths = BogusPaths(NameAllocator(context.symbols, context.source.text).random)
        signatures = set()
        for family in paths.families:
            for initial in (-100000, -1, 0, 1, 1 << 24):
                body = paths.build('_pseed', family=family)
                tree = ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
                self.assertFalse(any(isinstance(node, (ast.Call, ast.Attribute, ast.FunctionDef, ast.Yield, ast.Await)) for node in ast.walk(tree)))
                self.assertTrue(all(node.id == '_pseed' for node in ast.walk(tree) if isinstance(node, ast.Name)))
                namespace = {'_pseed': initial, '__builtins__': {}}
                exec(compile(tree, '<fake>', 'exec'), namespace)
                self.assertEqual(set(namespace), {'_pseed', '__builtins__'})
            # Compare topology, ignoring random numbers and arithmetic operators.
            def topology(node):
                children = tuple(topology(child) for child in ast.iter_child_nodes(node))
                if isinstance(node, (ast.stmt, ast.IfExp, ast.BoolOp, ast.Subscript)):
                    return (type(node).__name__, children)
                return children
            signatures.add(topology(tree))
        self.assertEqual(len(signatures), len(paths.families))

    def test_family_bag_guarantees_variety_across_guards(self):
        context = analyze(SourceDocument('value = 24'))
        paths = BogusPaths(NameAllocator(context.symbols, context.source.text).random)
        selected = []
        for _ in range(len(paths.families) * 4):
            paths.build('_pseed')
            selected.append(paths.previous)
        for start in range(0, len(selected), len(paths.families)):
            self.assertEqual(set(selected[start:start + len(paths.families)]), set(paths.families))
        self.assertTrue(all(left != right for left, right in zip(selected, selected[1:])))

    def test_deep_native_loops_do_not_exceed_python_compiler_block_limit(self):
        lines = ['def f():', '    value = 0', '    @protect_start(bcf)']
        for level in range(18):
            lines.append('    ' * (level + 1) + f'for i{level} in (1,):')
        lines.append('    ' * 19 + 'value += 1')
        lines.extend(['    @protect_end', '    return value'])
        self.compare('\n'.join(lines), lambda ns: ns['f']())

    def test_default_strip_proxy_morph_junk_and_fresh_layouts(self):
        from contextlib import redirect_stdout
        from io import StringIO
        code = '@protect_start(bcf, cff, proxy, morph, junk)\nlong_value = 24\nprint(long_value + ord("A"), @{"hello"})\n@protect_end'
        outputs = []
        for _ in range(3):
            result = Pipeline().run(code)
            stream = StringIO()
            with redirect_stdout(stream):
                exec(result.source.text, {})
            self.assertEqual(stream.getvalue(), '89 hello\n')
            self.assertNotIn('ord(', result.source.text)
            outputs.append(result.source.text)
        self.assertEqual(len(set(outputs)), 3)
        Pipeline().run('@protect_start(bcf)\nraise RuntimeError("do not execute")\n@protect_end')


class BogusFlowLoopRegressionTests(cfg_tests.BasicBlockCffTests):
    def compare(self, text, evaluate, runs=3):
        for options in ('bcf', 'cff, bcf'):
            with self.subTest(options=options):
                result = super().compare(text.replace('@protect_start(cff', f'@protect_start({options}'), evaluate, runs=2)
        return result

    def test_user_example_is_split_and_for_body_disappears(self):
        script = '''def f(x):
    events = []
    @protect_start(cff)
    for i in range(10):
        if i == x: break
        if i % 2: continue
        events.append(i)
    @protect_end
    return events
'''
        result = self.compare(script, lambda ns: [ns['f'](x) for x in (-1, 0, 1, 5, 10)])
        # CFF removes the user's loop; BCF can independently add literal scans.
        self.assertTrue(all(isinstance(node.iter, ast.Tuple) and isinstance(node.target, ast.Name) and node.target.id.startswith('_p')
                            for node in ast.walk(result.analysis.tree) if isinstance(node, ast.For)))
        self.assertGreater(sum(isinstance(node, ast.If) for node in ast.walk(result.analysis.tree)), 10)


if __name__ == '__main__':
    unittest.main()

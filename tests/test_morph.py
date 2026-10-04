import ast
import asyncio
import textwrap
import unittest
from unittest.mock import patch

from pyobf import Pipeline, SourceDocument
from pyobf.analysis import analyze
from pyobf.control_flow import ControlFlowGraph, IteratorNext
from pyobf.names import NameAllocator
import test_cff_cfg as cfg_tests
from test_cff_cfg import unmarked


class MorphTests(unittest.TestCase):
    def compare(self, text, evaluate, *, options='morph', runs=4):
        text = textwrap.dedent(text).lstrip('\n').replace('@protect_start(morph)', f'@protect_start({options})')
        original = {}
        exec(unmarked(text), original)
        expected = evaluate(original)
        for _ in range(runs):
            result = Pipeline(strip_info=False).run(text)
            actual = {}
            exec(result.source.text, actual)
            self.assertEqual(evaluate(actual), expected)
        return result

    def test_branch_truth_testing_short_circuit_and_shared_tail(self):
        result = self.compare('''
            events = []
            class Flag:
                def __init__(self, value): self.value = value
                def __bool__(self):
                    events.append(self.value)
                    return self.value
            def f(a, b):
                @protect_start(morph)
                if Flag(a) and Flag(b):
                    events.append('yes')
                    result = 3
                else:
                    events.append('no')
                    result = 3
                @protect_end
                return result
        ''', lambda ns: ([ns['f'](a, b) for a in (False, True) for b in (False, True)], ns['events']))
        self.assertEqual(sum(isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == 'result' for target in node.targets
        ) for node in ast.walk(ast.parse(result.source.text))), 1)

    def test_function_split_keeps_signature_docstring_and_defaults(self):
        result = self.compare('''
            events = []
            def default():
                events.append('default')
                return 4
            @protect_start(morph)
            def f(n=default(), /, *, k=2):
                "original doc"
                a = 3
                b = 5
                c = 'hello'
                return a + b + n * k, c
            @protect_end
        ''', lambda ns: (ns['f'](), ns['f'](6, k=3), ns['f'].__doc__, ns['events']))
        f = next(n for n in ast.parse(result.source.text).body if isinstance(n, ast.FunctionDef) and n.name == 'f')
        self.assertTrue(any(isinstance(n, ast.FunctionDef) for n in f.body))
        self.assertTrue(any(isinstance(n, ast.Delete) for n in ast.walk(f)))

    def test_constant_function_fusion_keeps_interfaces_and_closures(self):
        result = self.compare('''
            events = []
            def value(n):
                events.append(n)
                return n
            @protect_start(morph)
            def f(x=value(1), /):
                "f doc"
                return 41
            def g(*, y=value(2)):
                "g doc"
                return 'hello'
            def factory():
                def a(): return 3
                def b(): return 7
                return a, b
            @protect_end
        ''', lambda ns: (ns['f'](), ns['g'](y=9), [f() for f in ns['factory']()], ns['f'].__doc__, ns['g'].__doc__, ns['events']))
        tree = ast.parse(result.source.text)
        self.assertGreater(sum(isinstance(n, ast.FunctionDef) for n in tree.body), 4)
        f = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'f')
        self.assertIsInstance(f.body[-1].value, ast.Call)

    def test_decorators_and_existing_bindings_are_not_extracted(self):
        result = self.compare('''
            def decorate(f): return f
            @protect_start(morph)
            @decorate
            def f():
                a = 1
                b = 2
                return a + b
            def g(a):
                a = 4
                b = 6
                return a + b
            @protect_end
        ''', lambda ns: (ns['f'](), ns['g'](9)))
        self.assertEqual(sum(isinstance(n, ast.FunctionDef) for n in ast.walk(ast.parse(result.source.text))), 3)

    def test_reflection_disables_morph_even_with_proxy(self):
        result = self.compare('''
            @protect_start(morph)
            def f():
                a = 1
                b = 2
                for i in range(3):
                    a += i
                return sorted(locals().items())
            @protect_end
        ''', lambda ns: ns['f'](), options='morph, proxy')
        self.assertTrue(any(isinstance(n, ast.For) for n in ast.walk(ast.parse(result.source.text))))

    def test_class_namespace_inside_compound_block_is_untouched(self):
        self.compare('''
            events = []
            class Mapping(dict):
                def __setitem__(self, k, v):
                    events.append(k)
                    super().__setitem__(k, v)
            class Meta(type):
                @classmethod
                def __prepare__(m, name, bases): return Mapping()
            class C(metaclass=Meta):
                if True:
                    @protect_start(morph)
                    a = 1
                    b = 2
                    for i in range(2):
                        a += i
                    @protect_end
        ''', lambda ns: (ns['C'].a, ns['C'].b, ns['events']))

    def test_all_options_default_strip_and_string_macro(self):
        code = '''@protect_start(morph, cff, junk, proxy)
total = 0
for i in range(9):
    if i % 2: continue
    total += ord("A") + i
print(total, @{"hello"})
@protect_end
'''
        import contextlib
        import io
        for _ in range(4):
            result = Pipeline().run(code)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                exec(result.source.text, {})
            self.assertEqual(out.getvalue(), '345 hello\n')
            self.assertNotIn('@protect', result.source.text)
            self.assertNotIn('ord(', result.source.text)

    def test_graph_unroll_adds_lanes_and_respects_budget(self):
        context = analyze(SourceDocument('for i in values:\n    value = i\n'))
        names = NameAllocator(context.symbols, context.source.text)
        graph = ControlFlowGraph(names)
        graph.lower(context.tree.body)
        self.assertEqual(sum(isinstance(b.terminator, IteratorNext) for b in graph.blocks.values()), 1)
        graph.unroll()
        self.assertIn(sum(isinstance(b.terminator, IteratorNext) for b in graph.blocks.values()), (2, 3))
        context = analyze(SourceDocument('for i in values:\n' + ''.join(f'    x{n} = i\n' for n in range(200))))
        graph = ControlFlowGraph(NameAllocator(context.symbols, context.source.text))
        graph.lower(context.tree.body)
        count = len(graph.blocks)
        graph.unroll()
        self.assertEqual(len(graph.blocks), count)

    def test_nested_regions_empty_regions_encoding_and_source_boundaries(self):
        for newline in ('\n', '\r\n', '\r'):
            prefix = '# keep spacing\ndef f(n):\n    before= "keep"\n'
            suffix = '\n    after= "keep"\n    return before, value, after\n'
            middle = '''    @protect_start(morph)
    value = n
    @protect_start(morph, cff)
    for i in range(3):
        value += i
    @protect_end
    @protect_end'''
            script = (prefix + middle + suffix).replace('\n', newline)
            result = self.compare(script, lambda ns: ns['f'](3))
            self.assertTrue(result.source.text.startswith(prefix.replace('\n', newline)))
            self.assertTrue(result.source.text.endswith(suffix.replace('\n', newline)))
        for options in ('morph', 'morph, cff', 'morph, junk, proxy'):
            exec(Pipeline().run(f'@protect_start({options})\n@protect_end').source.text, {})

    def test_build_does_not_execute_and_reuses_pipeline_safely(self):
        pipeline = Pipeline(strip_info=False)
        pipeline.run('@protect_start(morph)\nraise RuntimeError("never execute")\n@protect_end')
        pipeline.run('@protect_start(morph)\nx = locals()\n@protect_end')
        outputs = [pipeline.run('@protect_start(morph)\nfor i in range(3):\n    print(i)\n@protect_end').source.text for _ in range(3)]
        self.assertEqual(len(set(outputs)), 3)
        for output in outputs:
            self.assertFalse(any(isinstance(n, ast.For) for n in ast.walk(ast.parse(output))))

    def test_morph_alone_never_invokes_cff_or_emits_state_dispatch(self):
        script = '''@protect_start(morph)
def f(limit):
    result = 0
    for i in range(limit):
        result += i
    try:
        for j in range(2):
            result += j
    finally:
        result += 1
    return result
for x in range(3):
    if x == 1: continue
    print(f(x))
@protect_end
'''
        for options in ('morph', 'morph, junk', 'morph, proxy'):
            with self.subTest(options=options), patch('pyobf.passes.cff.ControlFlowFlatteningPass.run', side_effect=AssertionError('CFF must be explicitly selected')):
                result = Pipeline(strip_info=False).run(script.replace('start(morph)', f'start({options})'))
            tree = ast.parse(result.source.text)
            loops = [node for node in ast.walk(tree) if isinstance(node, ast.While)]
            self.assertTrue(loops)
            # Junk has its own bounded loops; only morph's loops are tested here.
            if 'junk' not in options:
                self.assertTrue(all(isinstance(node.test, ast.Constant) and node.test.value is True for node in loops))
            self.assertTrue(any(isinstance(node, ast.For) for node in ast.walk(tree)))

    def test_cff_is_invoked_when_explicitly_selected(self):
        from pyobf.passes.cff import ControlFlowFlatteningPass
        original = ControlFlowFlatteningPass.run
        with patch.object(ControlFlowFlatteningPass, 'run', autospec=True, side_effect=original) as run:
            result = Pipeline(strip_info=False).run('@protect_start(morph, cff)\nfor i in range(3):\n    print(i)\n@protect_end')
        self.assertTrue(run.called)
        self.assertTrue(any(isinstance(node, ast.While) and isinstance(node.test, ast.Compare)
                            for node in ast.walk(ast.parse(result.source.text))))

    def test_native_lanes_keep_declarations_and_outer_else_exits(self):
        self.compare('''
            counter = 0
            def f():
                events = []
                for outer in range(3):
                    @protect_start(morph)
                    for i in range(2):
                        global counter
                        counter += 1
                    for j in range(2):
                        events.append(j)
                    else:
                        if outer == 1: break
                        continue
                    @protect_end
                return events, counter
        ''', lambda ns: ns['f']())

    def test_native_while_lanes_evaluate_condition_once_per_iteration(self):
        result = self.compare('''
            events = []
            class Flag:
                def __init__(self, value): self.value = value
                def __bool__(self):
                    events.append(('truth', self.value))
                    return self.value
            def f(limit):
                i = 0
                @protect_start(morph)
                while Flag(i < limit):
                    events.append(i)
                    i += 1
                else:
                    events.append('else')
                @protect_end
                return i
        ''', lambda ns: ([ns['f'](limit) for limit in (0, 1, 5)], ns['events']))
        self.assertTrue(all(isinstance(node.test, ast.Constant) and node.test.value is True
                            for node in ast.walk(ast.parse(result.source.text)) if isinstance(node, ast.While)))

    def test_native_async_lanes_preserve_exhaustion_and_body_exceptions(self):
        def evaluate(ns):
            results = [asyncio.run(ns['f'](limit, False)) for limit in (0, 1, 5)]
            try:
                asyncio.run(ns['f'](2, True))
            except StopAsyncIteration as error:
                results.append(str(error))
            return results, ns['events']
        result = self.compare('''
            events = []
            async def items(limit):
                for i in range(limit):
                    events.append(('fetch', i))
                    yield i
            async def f(limit, fail):
                result = []
                @protect_start(morph)
                async for i in items(limit):
                    result.append(i)
                    if fail: raise StopAsyncIteration('body')
                else:
                    result.append('else')
                @protect_end
                return result
        ''', evaluate)
        function = next(node for node in ast.parse(result.source.text).body
                        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'f')
        self.assertFalse(any(isinstance(node, ast.AsyncFor) for node in ast.walk(function)))

    def test_legacy_encoding_and_bom_are_preserved(self):
        text = '# coding: cp949\r\n@protect_start(morph)\r\n값 = 0\r\nfor i in range(3):\r\n    값 += i\r\nprint(값, "안녕")\r\n@protect_end\r\n'
        documents = [SourceDocument.from_bytes(text.encode('cp949')),
                     SourceDocument.from_bytes(b'\xef\xbb\xbf' + text.replace('cp949', 'utf-8').encode('utf-8'))]
        for document in documents:
            result = Pipeline(strip_info=False).run(document)
            self.assertEqual(result.source.encoding, document.encoding)
            self.assertEqual(result.source.to_bytes().startswith(b'\xef\xbb\xbf'), document.to_bytes().startswith(b'\xef\xbb\xbf'))
            self.assertNotIn('\n', result.source.text.replace('\r\n', ''))
            compile(result.source.to_bytes(), '<morph>', 'exec')

    def test_literal_full_unroll_preserves_closure_and_final_target(self):
        result = self.compare('''
            def f():
                values = []
                @protect_start(morph)
                for i in (1, 3, 5):
                    values.append(lambda: i)
                else:
                    values.append(lambda: 9)
                @protect_end
                return i, [value() for value in values]
        ''', lambda ns: ns['f']())
        self.assertFalse(any(isinstance(n, ast.For) for n in ast.walk(ast.parse(result.source.text))))

    def test_empty_literal_unroll_preserves_compile_time_bindings(self):
        self.compare('''
            item = 'global'
            def f():
                @protect_start(morph)
                for item in ():
                    nested = 3
                @protect_end
                try:
                    return item
                except UnboundLocalError:
                    return 'unbound'
            def generator():
                @protect_start(morph)
                for i in []:
                    yield i
                @protect_end
        ''', lambda ns: (ns['f'](), list(ns['generator']())))


class MorphLoopRegressionTests(cfg_tests.BasicBlockCffTests):
    """The same protocol/cleanup regressions must hold after lane cloning."""

    def compare(self, text, evaluate, runs=3):
        for options in ('morph', 'cff, morph'):
            with self.subTest(options=options):
                result = super().compare(text.replace('@protect_start(cff', f'@protect_start({options}'), evaluate, runs=2)
        return result

    def test_atomic_boundaries_remain_native(self):
        script = '''import contextlib
def f():
    values = []
    @protect_start(cff)
    with contextlib.nullcontext():
        for n in range(2): values.append(n)
    try:
        for n in range(2): values.append(-n)
    finally:
        values.append('finally')
    @protect_end
    return values
'''
        result = self.compare(script, lambda ns: ns['f']())
        tree = ast.parse(result.source.text)
        self.assertFalse(any(isinstance(n, ast.For) for n in ast.walk(tree)))
        self.assertTrue(any(isinstance(n, ast.With) for n in ast.walk(tree)))
        self.assertTrue(any(isinstance(n, ast.Try) and any(
            isinstance(child, ast.Constant) and child.value == 'finally'
            for statement in n.finalbody for child in ast.walk(statement)
        ) for n in ast.walk(tree)))

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
        tree = ast.parse(result.source.text)
        self.assertFalse(any(isinstance(n, (ast.For, ast.AsyncFor)) for n in ast.walk(tree)))
        predicates = [n for n in ast.walk(tree) if isinstance(n, ast.If) and any(
            isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == 'i'
            for test in ast.walk(n.test)
        )]
        self.assertGreaterEqual(len(predicates), 2)
        self.assertTrue(all(len(n.body) == len(n.orelse) == 1 and isinstance(n.body[0], ast.Assign) for n in predicates))


if __name__ == '__main__':
    unittest.main()

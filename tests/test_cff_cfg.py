import ast
import asyncio
import re
import textwrap
import unittest

from pyobf import Pipeline
from pyobf.analysis import analyze
from pyobf.control_flow import Branch, ControlFlowGraph, IteratorNext, Jump
from pyobf.names import NameAllocator
from pyobf.source import SourceDocument


def source(text):
    return textwrap.dedent(text).lstrip('\n')


def unmarked(text):
    return '\n'.join('' if re.match(r'\s*@protect_(?:start|end)\b', line) else line for line in text.splitlines())


class BasicBlockCffTests(unittest.TestCase):
    def compare(self, text, evaluate, runs=3):
        original = {}
        exec(unmarked(text), original)
        expected = evaluate(original)
        for _ in range(runs):
            result = Pipeline(strip_info=False).run(text)
            actual = {}
            exec(result.source.text, actual)
            self.assertEqual(evaluate(actual), expected)
        return result

    def test_user_example_is_split_and_for_body_disappears(self):
        script = source('''
            def f(x):
                events = []
                @protect_start(cff)
                for i in range(10):
                    if i == x:
                        break
                    if i % 2:
                        continue
                    events.append(i)
                @protect_end
                return events
        ''')
        result = self.compare(script, lambda ns: [ns['f'](x) for x in (-1, 0, 1, 5, 10)])
        tree = ast.parse(result.source.text)
        self.assertFalse(any(isinstance(n, (ast.For, ast.AsyncFor)) for n in ast.walk(tree)))
        loops = [n for n in ast.walk(tree) if isinstance(n, ast.While)]
        self.assertEqual(len(loops), 1)
        # Source conditions survive only as branch predicates with tiny state edges.
        predicates = [n for n in ast.walk(tree) if isinstance(n, ast.If) and isinstance(n.test, ast.Compare) and isinstance(n.test.left, ast.Name) and n.test.left.id == 'i']
        self.assertTrue(predicates)
        self.assertTrue(all(len(n.body) == len(n.orelse) == 1 and isinstance(n.body[0], ast.Assign) for n in predicates))

    def test_graph_contains_loop_head_and_separate_condition_blocks(self):
        context = analyze(SourceDocument('for i in range(10):\n    if i == x: break\n    if i % 2: continue\n    print(i)'))
        graph = ControlFlowGraph(NameAllocator(context.symbols, context.source.text))
        entry = graph.lower(context.tree.body)
        self.assertIn(entry, graph.blocks)
        self.assertEqual(sum(isinstance(b.terminator, IteratorNext) for b in graph.blocks.values()), 1)
        self.assertEqual(sum(isinstance(b.terminator, Branch) for b in graph.blocks.values()), 2)
        self.assertGreaterEqual(len(graph.blocks), 8)
        self.assertFalse(graph.external_exits)
        for block in graph.blocks.values():
            if isinstance(block.terminator, Jump):
                self.assertIn(block.terminator.target, {0, *graph.blocks})

    def test_while_continue_break_else_and_test_side_effects(self):
        script = source('''
            def f(limit, stop):
                events = []
                n = 0
                def check():
                    events.append(('test', n))
                    return n < limit
                @protect_start(cff)
                while check():
                    n += 1
                    if n == stop:
                        break
                    if n % 2:
                        continue
                    events.append(('body', n))
                else:
                    events.append('else')
                events.append('tail')
                @protect_end
                return events
        ''')
        self.compare(script, lambda ns: [ns['f'](limit, stop) for limit in (0, 1, 5) for stop in (0, 2, 6)])

    def test_nested_for_while_and_else_targets(self):
        script = source('''
            def f():
                events = []
                @protect_start(cff)
                for outer in range(5):
                    n = 0
                    while n < 2:
                        n += 1
                        if outer == 3:
                            break
                        for inner in range(3):
                            if inner == 1:
                                continue
                            events.append((outer, n, inner))
                        else:
                            if outer == 1:
                                continue
                            if outer == 2:
                                break
                    else:
                        if outer == 4:
                            break
                        events.append(('while-else', outer))
                else:
                    events.append('for-else')
                @protect_end
                return events
        ''')
        self.compare(script, lambda ns: ns['f']())

    def test_iteration_protocol_and_shadowed_builtin_names(self):
        script = source('''
            events = []
            iter = next = StopIteration = lambda *args: (_ for _ in ()).throw(RuntimeError('shadowed'))
            class Items:
                def __iter__(self):
                    events.append('iter')
                    self.position = 0
                    return self
                def __next__(self):
                    events.append(('next', self.position))
                    if self.position == 3:
                        raise __builtins__['StopIteration']
                    self.position += 1
                    return self.position
            def f():
                values = []
                @protect_start(cff)
                for item in Items():
                    values.append(item)
                else:
                    values.append('else')
                @protect_end
                return values
        ''')
        self.compare(script, lambda ns: (ns['f'](), ns['events']))

    def test_stopiteration_from_target_assignment_propagates(self):
        script = source('''
            events = []
            class Target:
                def __setattr__(self, name, value):
                    events.append(value)
                    raise StopIteration('target-failure')
            def f():
                target = Target()
                @protect_start(cff)
                for target.value in [1, 2]:
                    events.append('body')
                else:
                    events.append('else')
                @protect_end
        ''')
        def evaluate(ns):
            try:
                ns['f']()
            except StopIteration as error:
                return str(error), ns['events']
            self.fail('Target exception was swallowed as exhaustion')
        self.compare(script, evaluate)

    def test_stopiteration_from_body_propagates(self):
        script = source('''
            def f():
                @protect_start(cff)
                for value in [1]:
                    raise StopIteration('body-failure')
                @protect_end
        ''')
        def evaluate(ns):
            try:
                ns['f']()
            except StopIteration as error:
                return str(error)
            self.fail('Body exception was swallowed as exhaustion')
        self.compare(script, evaluate)

    def test_iterable_and_target_side_effects_happen_once(self):
        script = source('''
            events = []
            class Target:
                def __setattr__(self, name, value):
                    events.append((name, value))
            def items():
                events.append('items')
                return [(1, 2), (3, 4)]
            def f():
                target = Target()
                @protect_start(cff)
                for (target.first, target.second) in items():
                    events.append('body')
                @protect_end
                return events
        ''')
        self.compare(script, lambda ns: ns['f']())

    def test_iterators_are_released_before_loop_else_and_after_break(self):
        script = source('''
            events = []
            class Iterator:
                def __init__(self): self.n = 0
                def __iter__(self): return self
                def __next__(self):
                    self.n += 1
                    if self.n > 2: raise StopIteration
                    return self.n
                def __del__(self): events.append('released')
            def f(stop):
                @protect_start(cff)
                for value in Iterator():
                    events.append(value)
                    if stop: break
                else:
                    events.append('else')
                events.append('tail')
                @protect_end
                return events
        ''')
        self.compare(script, lambda ns: (list(ns['f'](False)), list(ns['f'](True))))

    def test_native_finally_overrides_internal_loop_exits(self):
        script = source('''
            def f():
                events = []
                @protect_start(cff)
                for n in range(4):
                    try:
                        if n == 0: break
                        if n == 1: continue
                    finally:
                        events.append(n)
                        if n == 0: continue
                        if n == 1: break
                    events.append('body-tail')
                else:
                    events.append('else')
                events.append('tail')
                @protect_end
                return events
        ''')
        self.compare(script, lambda ns: ns['f']())

    def test_caught_exception_cancels_pending_break_and_continue(self):
        script = source('''
            import contextlib
            def f():
                events = []
                @protect_start(cff)
                for n in range(3):
                    with contextlib.suppress(ValueError):
                        try:
                            if n == 0: break
                            continue
                        finally:
                            raise ValueError('cancel-exit')
                    events.append(('tail', n))
                else:
                    events.append('else')
                @protect_end
                return events
        ''')
        self.compare(script, lambda ns: ns['f']())

    def test_except_handler_cancels_pending_exit(self):
        script = source('''
            def f():
                events = []
                @protect_start(cff)
                for n in range(3):
                    try:
                        try:
                            break
                        finally:
                            raise ValueError
                    except ValueError:
                        events.append(('caught', n))
                    events.append(('tail', n))
                else:
                    events.append('else')
                @protect_end
                return events
        ''')
        self.compare(script, lambda ns: ns['f']())

    def test_return_overrides_break_and_native_nested_loops_keep_ownership(self):
        script = source('''
            def f():
                events = []
                @protect_start(cff)
                for outer in range(3):
                    try:
                        for inner in range(2):
                            if inner == 0: continue
                            events.append((outer, inner))
                            break
                        else:
                            continue
                        break
                    finally:
                        if outer == 0: return events
                @protect_end
                return events
        ''')
        self.compare(script, lambda ns: ns['f']())

    def test_async_for_continue_break_else_and_await(self):
        script = source('''
            import asyncio
            async def items(limit):
                for n in range(limit):
                    await asyncio.sleep(0)
                    yield n
            async def f(limit, stop):
                events = []
                @protect_start(cff)
                async for n in items(limit):
                    if n == stop: break
                    if n % 2: continue
                    await asyncio.sleep(0)
                    events.append(n)
                else:
                    events.append('else')
                @protect_end
                return events
        ''')
        result = self.compare(script, lambda ns: [asyncio.run(ns['f'](limit, stop)) for limit in (0, 3, 6) for stop in (-1, 2)])
        tree = ast.parse(result.source.text)
        # The async iterable's separate lexical scope stays unchanged.
        target = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'f')
        self.assertFalse(any(isinstance(n, ast.AsyncFor) for n in ast.walk(target)))

    def test_generator_send_throw_and_close_use_native_cleanup(self):
        script = source('''
            events = []
            def f():
                @protect_start(cff)
                for n in range(3):
                    try:
                        received = yield n
                        events.append(received)
                    finally:
                        events.append(('finally', n))
                @protect_end
        ''')
        def evaluate(ns):
            generator = ns['f']()
            result = [next(generator), generator.send(7)]
            try:
                generator.throw(ValueError('thrown'))
            except ValueError:
                result.append('raised')
            generator = ns['f']()
            result.append(next(generator))
            generator.close()
            return result, ns['events']
        self.compare(script, evaluate)

    def test_atomic_boundaries_remain_native(self):
        script = source('''
            import contextlib
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
        ''')
        result = self.compare(script, lambda ns: ns['f']())
        self.assertEqual(sum(isinstance(n, ast.For) for n in ast.walk(ast.parse(result.source.text))), 2)

    def test_no_generated_helpers_remain_in_class_namespace(self):
        script = source('''
            class Example:
                @protect_start(cff)
                values = []
                for n in range(3):
                    if n == 1: continue
                    values.append(n)
                @protect_end
        ''')
        self.compare(script, lambda ns: (ns['Example'].values, ns['Example'].n, sorted(name for name in vars(ns['Example']) if name.startswith('_p'))))

    def test_empty_iterable_leaves_target_unbound_and_preserves_previous_target(self):
        script = source('''
            def f():
                @protect_start(cff)
                for unknown in []: pass
                previous = 7
                for previous in []: pass
                @protect_end
                try:
                    return unknown
                except UnboundLocalError:
                    return previous
        ''')
        self.compare(script, lambda ns: ns['f']())

    def test_cfg_composes_with_junk_and_default_strip_info(self):
        script = source('''
            @protect_start(cff, junk)
            long_values = []
            for long_number in range(6):
                if long_number % 2: continue
                long_values.append(long_number)
            result = tuple(long_values)
            @protect_end
            __all__ = ['result']
        ''')
        transformed = Pipeline().run(script)
        namespace = {}
        exec(transformed.source.text, namespace)
        self.assertEqual(namespace['result'], (0, 2, 4))
        self.assertFalse(any(isinstance(n, ast.For) for n in ast.walk(transformed.analysis.tree)))
        self.assertEqual(transformed.applied_passes, ('ProtectionPass', 'StripInfoPass'))


if __name__ == '__main__':
    unittest.main()

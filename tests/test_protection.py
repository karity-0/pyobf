import asyncio
import re
import textwrap
import unittest

from pyobf import Pipeline, SourceDocument


def source(text):
    return textwrap.dedent(text).lstrip("\n")


def unmarked(text):
    return "\n".join(
        "" if re.match(r"\s*@protect_(?:start|end)\b", line) else line
        for line in text.splitlines()
    )


class ProtectionTests(unittest.TestCase):
    def compare(self, text, evaluate):
        original = {}
        exec(unmarked(text), original)
        expected = evaluate(original)
        for _ in range(3):
            result = Pipeline(strip_info=False).run(text)
            actual = {}
            exec(result.source.text, actual)
            self.assertEqual(evaluate(actual), expected)
        return result

    def test_combined_options_preserve_values_and_side_effect_order(self):
        script = source('''
            events = []
            def foo(n):
                before = n
                @protect_start(cff, junk)
                x = n + 1
                events.append(x)
                if x > 3:
                    x *= 2
                else:
                    x -= 1
                events.append(x)
                @protect_end
                return before, x
        ''')
        result = self.compare(script, lambda ns: ([ns["foo"](n) for n in (-1, 2, 8)], ns["events"]))
        self.assertIn("while ", result.source.text)
        self.assertNotIn("@protect_", result.source.text)
        self.assertEqual(result.applied_passes, ("ProtectionPass",))
        self.assertEqual(result.replacement_count, 1)

    def test_each_option_can_be_selected_alone(self):
        for option in ("cff", "junk"):
            with self.subTest(option=option):
                script = f"def f(n):\n    @protect_start({option})\n    x = n + 1\n    @protect_end\n    return x\n"
                result = self.compare(script, lambda ns: ns["f"](4))
                self.assertNotIn("@protect_", result.source.text)
                self.assertGreater(len(result.source.text), len(script))

    def test_source_outside_region_is_preserved_exactly(self):
        prefix = '# outside comment\r\ndef f(n):\r\n    before = "keep"  # spacing\r\n'
        suffix = '\r\n    after= "unchanged"\r\n    return before, x, after\r\n'
        script = prefix + '    @protect_start(cff, junk)\r\n    x = n * 2\r\n    @protect_end' + suffix
        result = Pipeline(strip_info=False).run(script)
        self.assertTrue(result.source.text.startswith(prefix))
        self.assertTrue(result.source.text.endswith(suffix))
        self.assertNotIn("\n", result.source.text.replace("\r\n", ""))

    def test_adjacent_and_nested_regions(self):
        script = source('''
            def f(n):
                @protect_start(cff, junk)
                x = n
                @protect_start(junk)
                x += 2
                @protect_end
                if n:
                    @protect_start(cff)
                    x *= 3
                    x += 1
                    @protect_end
                @protect_end
                @protect_start(cff)
                x += 5
                @protect_end
                return x
        ''')
        result = self.compare(script, lambda ns: [ns["f"](n) for n in (0, 1, 9)])
        self.assertEqual(result.replacement_count, 2)  # Outer edits include children.

    def test_enclosing_loop_break_continue_and_else(self):
        script = source('''
            def f(limit):
                events = []
                for n in range(limit):
                    @protect_start(cff, junk)
                    if n == 2:
                        continue
                    events.append(n)
                    if n == 4:
                        break
                    @protect_end
                    events.append(-n)
                else:
                    events.append("else")
                return events
        ''')
        self.compare(script, lambda ns: [ns["f"](n) for n in (0, 3, 8)])

    def test_nested_loops_and_loop_else_exits(self):
        script = source('''
            def f():
                out = []
                for n in range(5):
                    @protect_start(cff, junk)
                    for x in range(3):
                        if x == 1:
                            continue
                        out.append((n, x))
                        if n == 3:
                            break
                    else:
                        if n == 1:
                            continue
                        if n == 2:
                            break
                    @protect_end
                    out.append("tail")
                return out
        ''')
        self.compare(script, lambda ns: ns["f"]())

    def test_finally_can_override_break_with_continue_and_reverse(self):
        script = source('''
            def f():
                events = []
                for n in range(5):
                    @protect_start(cff, junk)
                    try:
                        if n == 0:
                            break
                        if n == 1:
                            continue
                    finally:
                        events.append(n)
                        if n == 0:
                            continue
                        if n == 1:
                            break
                    @protect_end
                    events.append("tail")
                return events
        ''')
        self.compare(script, lambda ns: ns["f"]())

    def test_return_raise_and_context_managers(self):
        script = source('''
            events = []
            class Resource:
                def __enter__(self):
                    events.append("enter")
                def __exit__(self, *args):
                    events.append(args[0].__name__ if args[0] else "normal")
            def f(n):
                @protect_start(cff, junk)
                with Resource():
                    try:
                        if n < 0:
                            raise ValueError("failure")
                        return n + 2
                    finally:
                        events.append("finally")
                @protect_end
        ''')
        def evaluate(ns):
            results = []
            for n in (-1, 0, 4):
                try:
                    results.append(ns["f"](n))
                except ValueError as error:
                    results.append(str(error))
            return results, ns["events"]
        self.compare(script, evaluate)

    def test_global_nonlocal_and_closures(self):
        script = source('''
            counter = 0
            def outer():
                value = 2
                def f(n):
                    @protect_start(cff, junk)
                    global counter
                    nonlocal value
                    counter += 1
                    value += n
                    def capture():
                        nonlocal value
                        return value
                    @protect_end
                    return capture
                return f
        ''')
        def evaluate(ns):
            f = ns["outer"]()
            first = f(2)
            second = f(3)
            return first(), second(), ns["counter"]
        self.compare(script, evaluate)

    def test_declarations_inside_compound_statements(self):
        script = source('''
            x = 0
            def f(n):
                @protect_start(cff)
                if n:
                    global x
                    x = n
                x += 1
                @protect_end
                return x
        ''')
        self.compare(script, lambda ns: [ns["f"](n) for n in (0, 4, 0)])

    def test_async_and_generator_functions_keep_their_scopes(self):
        script = source('''
            import asyncio
            async def f(n):
                @protect_start(cff, junk)
                await asyncio.sleep(0)
                value = n + 2
                @protect_end
                return value
            def generate(n):
                @protect_start(cff, junk)
                for x in range(n):
                    yield x
                yield from (8, 9)
                @protect_end
        ''')
        self.compare(script, lambda ns: (asyncio.run(ns["f"](4)), list(ns["generate"](3))))

    def test_nested_definitions_with_their_own_protected_regions(self):
        script = source('''
            def f():
                @protect_start(cff, junk)
                def inner(n):
                    @protect_start(cff, junk)
                    x = n * 3
                    @protect_end
                    return x
                x = inner(4)
                @protect_end
                return x
        ''')
        self.compare(script, lambda ns: ns["f"]())

    def test_docstrings_and_class_attributes_are_preserved(self):
        script = source('''
            @protect_start(cff, junk)
            "module doc"
            value = 2
            @protect_end
            class Example:
                @protect_start(cff, junk)
                "class doc"
                x = 3
                y: int = 4
                @protect_end
            def f():
                @protect_start(cff, junk)
                "function doc"
                x = 5
                @protect_end
                return x
        ''')
        def evaluate(ns):
            cls = ns["Example"]
            return ns["__doc__"], cls.__doc__, ns["f"].__doc__, cls.x, cls.y, ns["f"](), cls.__annotations__, [name for name in vars(cls) if name.startswith("_p")]
        self.compare(script, evaluate)

    def test_empty_regions_are_valid(self):
        for options in ("cff", "junk", "cff, junk"):
            with self.subTest(options=options):
                result = Pipeline(strip_info=False).run(f"def f():\n    @protect_start({options})\n    @protect_end\n")
                ns = {}
                exec(result.source.text, ns)
                self.assertIsNone(ns["f"]())

    def test_empty_regions_do_not_displace_an_outside_docstring(self):
        script = source('''
            def f():
                @protect_start(cff, junk)
                @protect_end
                @protect_start(junk)
                @protect_end
                "outside docstring"
                return 2
        ''')
        self.compare(script, lambda ns: (ns["f"].__doc__, ns["f"]()))

    def test_string_encryption_composes_with_protection(self):
        script = 'def f():\n    @protect_start(cff, junk)\n    x = @{"hello world"}\n    @protect_end\n    return x\n'
        result = Pipeline(strip_info=False).run(script)
        ns = {}
        exec(result.source.text, ns)
        self.assertEqual(ns["f"](), "hello world")
        self.assertNotIn("hello world", result.source.text)
        self.assertEqual(result.applied_passes, ("StringEncryptionPass", "ProtectionPass"))

    def test_utf8_bom_cp949_and_cr_only_files(self):
        for encoding in ("utf-8-sig", "cp949"):
            for newline in ("\r\n", "\r"):
                with self.subTest(encoding=encoding, newline=newline):
                    cookie = "# coding: cp949\n" if encoding == "cp949" else ""
                    script = cookie + 'def f():\n    @protect_start(cff, junk)\n    이름 = "안녕"\n    x = "\\U0001f30d"\n    @protect_end\n    return 이름, x\n'
                    document = SourceDocument.from_bytes(script.replace("\n", newline).encode(encoding))
                    result = Pipeline(strip_info=False).run(document)
                    ns = {}
                    exec(result.source.to_bytes(), ns)
                    self.assertEqual(ns["f"](), ("안녕", "🌍"))
                    self.assertEqual(result.source.encoding, encoding)

    def test_tabs_are_supported(self):
        script = 'def f(n):\n\t@protect_start(cff, junk)\n\tif n:\n\t\tx = 1\n\telse:\n\t\tx = 2\n\t@protect_end\n\treturn x\n'
        self.compare(script, lambda ns: [ns["f"](n) for n in (0, 1)])

    def test_markers_inside_comments_and_strings_are_ignored(self):
        text = '# @protect_start(cff)\nx = "@protect_end"\ny = """@protect_start(junk)\n@protect_end"""\n'
        document = SourceDocument(text)
        result = Pipeline(strip_info=False).run(document)
        self.assertIs(result.source, document)
        self.assertEqual(result.applied_passes, ())

    def test_invalid_options_and_pairs_report_original_location(self):
        examples = [
            "@protect_start()\nx = 1\n@protect_end",
            "@protect_start(unknown)\nx = 1\n@protect_end",
            '@protect_start("cff")\nx = 1\n@protect_end',
            "@protect_start(cff, cff)\nx = 1\n@protect_end",
            "@protect_start(cff)(junk)\nx = 1\n@protect_end",
            "@protect_start(cff)\nx = 1",
            "@protect_end",
            "@protect_start(cff)\nx = 1\n@protect_end()",
            "if True: @protect_start(cff)\nx = 1\n@protect_end",
            "if True:\n    @protect_start(cff)\n    x = 1\nelse:\n    @protect_end",
            "@protect_start(cff)\nif True:\n    x = 1\n    @protect_end",
        ]
        for text in examples:
            with self.subTest(text=text), self.assertRaises(SyntaxError) as caught:
                Pipeline(strip_info=False).run(SourceDocument(text, "regions.py"))
            self.assertEqual(caught.exception.filename, "regions.py")
            self.assertIsNotNone(caught.exception.lineno)

    def test_large_dispatcher_avoids_deep_elif_chains(self):
        lines = ["def f():", "    total = 0", "    @protect_start(cff, junk)"]
        lines.extend(f"    total += {number}" for number in range(350))
        lines += ["    @protect_end", "    return total"]
        result = Pipeline(strip_info=False).run("\n".join(lines))
        ns = {}
        exec(result.source.text, ns)
        self.assertEqual(ns["f"](), sum(range(350)))

    def test_builds_use_fresh_names_and_state_layout(self):
        script = 'def f():\n    @protect_start(cff, junk)\n    x = 2\n    @protect_end\n    return x\n'
        self.assertNotEqual(Pipeline(strip_info=False).run(script).source.text, Pipeline(strip_info=False).run(script).source.text)


if __name__ == "__main__":
    unittest.main()


import ast
import re
import unittest
from unittest.mock import patch

from pyobf import Pipeline, SourceDocument
from pyobf.passes import BasePass, PrePass, Replacement


class StringEncryptionTests(unittest.TestCase):
    def execute(self, source):
        result = Pipeline(strip_info=False).run(source)
        namespace = {}
        # Execute only test fixtures, never arbitrary input through the pipeline.
        exec(result.source.to_bytes(), namespace)
        return result, namespace

    def test_only_explicit_macros_are_encrypted(self):
        original = 'plain = "hello world"\nx = @{"hello world"}  # keep me\n'
        result, namespace = self.execute(original)
        self.assertEqual(namespace["x"], "hello world")
        self.assertEqual(namespace["plain"], "hello world")
        self.assertTrue(result.source.text.startswith('plain = "hello world"\nx = '))
        self.assertTrue(result.source.text.endswith("  # keep me\n"))
        self.assertNotIn("hello world", result.source.text.split("x = ", 1)[1])
        self.assertNotIn("@{", result.source.text)
        self.assertEqual(result.applied_passes, ("StringEncryptionPass",))
        self.assertEqual(result.replacement_count, 1)

    def test_literal_forms_round_trip(self):
        examples = [
            ('""', ""),
            ('"안녕 🌍"', "안녕 🌍"),
            (r'"line\n\t\x00\"\\"', 'line\n\t\x00"\\'),
            (r'r"C:\path\file"', r"C:\path\file"),
            ('u"unicode"', "unicode"),
            ('"""first\nsecond"""', "first\nsecond"),
            ('"left" "right"', "leftright"),
            ('("grouped")', "grouped"),
            (r'"\ud800\udfff"', "\ud800\udfff"),
            (r'"\0\0a\0"', "\0\0a\0"),
            ('"} @{braces} {"', "} @{braces} {"),
        ]
        for literal, expected in examples:
            with self.subTest(literal=literal):
                result, namespace = self.execute("x = @{" + literal + "}")
                self.assertEqual(namespace["x"], expected)
                self.assertEqual(result.replacement_count, 1)

    def test_marker_text_in_literals_comments_and_decorators_is_ignored(self):
        source = (
            "# @{\"comment\"}\n"
            "text = '@{\"ordinary\"}'\n"
            "data = b'@{\"bytes\"}'\n"
            "doc = '''@{\"triple\"}'''\n"
            "formatted = f'@{{\"formatted\"}}'\n"
            "@decorator\n"
            "def f(): pass\n"
            "matrix @ {\"set\"}\n"
        )
        document = SourceDocument(source)
        result = Pipeline(strip_info=False).run(document)
        self.assertIs(result.source, document)
        self.assertEqual(result.applied_passes, ())

    def test_multiple_macros_work_in_expression_positions(self):
        source = (
            "def f(value): return value\n"
            "items = [@{'one'}, @{'two'}]\n"
            "mapping = {@{'key'}: f(@{'value'})}\n"
            "joined = @{'a'} + @{'b'}\n"
        )
        result, namespace = self.execute(source)
        self.assertEqual(namespace["items"], ["one", "two"])
        self.assertEqual(namespace["mapping"], {"key": "value"})
        self.assertEqual(namespace["joined"], "ab")
        self.assertEqual(result.replacement_count, 6)

    def test_decoder_is_independent_of_user_names(self):
        source = (
            "bytes = str = int = len = enumerate = __import__ = None\n"
            "_c, _k = 'outside', 'outside'\n"
            "def f(_c, _k):\n"
            "    return @{'inside'}\n"
            "x = @{'hello'}\n"
            "y = f(None, None)\n"
        )
        _, namespace = self.execute(source)
        self.assertEqual(namespace["x"], "hello")
        self.assertEqual(namespace["y"], "inside")
        self.assertEqual(namespace["_c"], "outside")
        self.assertEqual(namespace["_k"], "outside")

    def test_every_occurrence_gets_its_own_key(self):
        with patch("pyobf.passes.string_encryption.secrets.token_bytes", side_effect=[b"\x11" * 6, b"\x22" * 6]) as random:
            result, namespace = self.execute("x = @{'secret'}\ny = @{'secret'}")
        self.assertEqual(random.call_count, 2)
        self.assertEqual(namespace["x"], namespace["y"])
        self.assertNotEqual(result.source.text.splitlines()[0][4:], result.source.text.splitlines()[1][4:])
        self.assertNotIn("secret", result.source.text)

    def test_newlines_encoding_and_surrounding_source_are_preserved(self):
        for newline in ("\n", "\r\n", "\r"):
            with self.subTest(newline=newline):
                source = (
                    "# coding: cp949" + newline
                    + "이름 = @{" + newline
                    + "    # macro comment" + newline
                    + "    '안녕'" + newline
                    + "}  # untouched" + newline
                    + "unchanged = '그대로'"
                )
                document = SourceDocument.from_bytes(source.encode("cp949"))
                result, namespace = self.execute(document)
                self.assertEqual(namespace["이름"], "안녕")
                self.assertEqual(namespace["unchanged"], "그대로")
                self.assertEqual(result.source.encoding, "cp949")
                self.assertTrue(result.source.text.startswith("# coding: cp949" + newline + "이름 = "))
                self.assertTrue(result.source.text.endswith("  # untouched" + newline + "unchanged = '그대로'"))
                self.assertEqual(re.findall(r"\r\n|\r|\n", result.source.text), re.findall(r"\r\n|\r|\n", source))
                constant = result.analysis.by_type["Assign"][-1].node
                self.assertEqual(constant.lineno, 6)

    def test_utf8_bom_survives_transformation(self):
        document = SourceDocument.from_bytes(b"\xef\xbb\xbfx = @{'hello'}\r\n")
        result, namespace = self.execute(document)
        self.assertTrue(result.source.to_bytes().startswith(b"\xef\xbb\xbf"))
        self.assertTrue(result.source.to_bytes().endswith(b"\r\n"))
        self.assertEqual(namespace["x"], "hello")

    def test_malformed_and_non_literal_macros_are_rejected(self):
        bodies = ("", "42", "None", "value", "b'bytes'", "f'{value}'", "'a' + 'b'", "'a', 'b'", "{'x': 'y'}", "@{'nested'}")
        for body in bodies:
            with self.subTest(body=body), self.assertRaisesRegex(SyntaxError, "문자열 리터럴"):
                Pipeline(strip_info=False).run("x = @{" + body + "}")
        with self.assertRaisesRegex(SyntaxError, "닫는 }"):
            Pipeline(strip_info=False).run("x = @{'unterminated'")

    def test_macro_error_uses_original_filename_row_and_character_column(self):
        document = SourceDocument("# title\r\n이름 = @{42}\r\n", "example.py")
        with self.assertRaises(SyntaxError) as caught:
            Pipeline(strip_info=False).run(document)
        error = caught.exception
        self.assertEqual((error.filename, error.lineno, error.offset), ("example.py", 2, 6))
        self.assertEqual(error.text, "이름 = @{42}\r\n")

    def test_macro_body_is_never_evaluated(self):
        with patch("builtins.eval", side_effect=AssertionError("must not eval")):
            with self.assertRaises(SyntaxError):
                Pipeline(strip_info=False).run("x = @{__import__('os').system('must not execute')}")
            Pipeline(strip_info=False).run("x = @{'safe'}\nraise RuntimeError('must not execute')")

    def test_ast_passes_see_lowered_python(self):
        class InspectPass(BasePass):
            def run(self, context):
                self.assertion = any(isinstance(ref.node, ast.Lambda) for ref in context.nodes)
                self.has_macro = "@{" in context.source.text
                return []

        inspection = InspectPass()
        result = Pipeline(strip_info=False).add(inspection).run("x = @{'hello'}")
        self.assertTrue(inspection.assertion)
        self.assertFalse(inspection.has_macro)
        self.assertEqual(result.applied_passes, ("StringEncryptionPass", "InspectPass"))

    def test_additional_pre_pass_runs_before_ast_analysis(self):
        class CustomPrePass(PrePass):
            def run(self, source):
                return [Replacement(4, len(source.text), "2")]

        result = Pipeline(strip_info=False).add(CustomPrePass()).run("x = CUSTOM")
        self.assertEqual(result.source.text, "x = 2")

    def test_large_string_round_trip(self):
        value = "long 한글 " * 2000
        result, namespace = self.execute("x = @{" + repr(value) + "}")
        self.assertEqual(namespace["x"], value)
        self.assertNotIn(value, result.source.text)


if __name__ == "__main__":
    unittest.main()


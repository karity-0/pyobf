import ast
import unittest

from pyobf import Pipeline, SourceDocument
from pyobf.passes import BasePass, Replacement


class ReplaceNumber(BasePass):
    def run(self, context):
        return [
            Replacement(*context.source.span(ref.node), "(1 + 1)")
            for ref in context.find(ast.Constant)
            if ref.node.value == 2
        ]


class PipelineTests(unittest.TestCase):
    def test_original_bytes_and_text_survive(self):
        examples = [
            b"",
            b"# comment\r\nx = 2  # keep spacing\r\n",
            b"\xef\xbb\xbfprint('hello')\n",
            "# coding: cp949\r\n이름 = '안녕'\r\n".encode("cp949"),
            b"x = 1\r\n\r\ny = 2\n# no final newline",
            b"x = 1\n\fprint(x)\n",
            b"x = 1\ry = 2\r",
        ]
        for data in examples:
            with self.subTest(data=data):
                source = SourceDocument.from_bytes(data)
                result = Pipeline(strip_info=False).run(source)
                self.assertIs(result.source, source)
                self.assertEqual(result.source.to_bytes(), data)
                self.assertEqual(result.source.text, source.text)
                self.assertEqual(result.applied_passes, ())

    def test_all_nodes_and_parent_fields_are_indexed(self):
        context = Pipeline(strip_info=False).run("def f(x):\n    return x + 2\n").analysis
        self.assertEqual(len(context.nodes), len(list(ast.walk(context.tree))))
        name = next(ref for ref in context.find(ast.Name))
        self.assertIsInstance(name.parent, ast.BinOp)
        self.assertEqual(name.field, "left")
        self.assertEqual(context.by_type["FunctionDef"][0].index, 0)
        self.assertEqual(context.symbols.get_children()[0].lookup("x").is_parameter(), True)
        self.assertTrue(any(token.string == "return" for token in context.tokens))

    def test_unicode_ast_offsets_and_reanalysis(self):
        original = "이름 = 2  # keep me\r\nprint(이름)\r\n"
        result = Pipeline(strip_info=False).add(ReplaceNumber()).run(original)
        self.assertEqual(result.source.text, "이름 = (1 + 1)  # keep me\r\nprint(이름)\r\n")
        self.assertEqual(len(result.analysis.by_type["BinOp"]), 1)
        self.assertEqual(result.applied_passes, ("ReplaceNumber",))
        self.assertIs(result.analysis.source, result.source)

    def test_multiline_and_form_feed_spans(self):
        source = SourceDocument('x = """한글\ntext"""\n\fprint(x)\n')
        context = Pipeline(strip_info=False).run(source).analysis
        constant = next(context.find(ast.Constant)).node
        self.assertEqual(source.source_for(constant), '"""한글\ntext"""')
        call = next(context.find(ast.Call)).node
        self.assertEqual(source.source_for(call), "print(x)")
        source = SourceDocument("x = 1\ry = 2\r")
        context = Pipeline(strip_info=False).run(source).analysis
        self.assertEqual(source.source_for(list(context.find(ast.Assign))[1].node), "y = 2")

    def test_invalid_syntax_and_scope_rejected(self):
        for text in ("def broken(:", "return 1", "break"):
            with self.subTest(text=text), self.assertRaises(SyntaxError):
                Pipeline(strip_info=False).run(text)

    def test_input_is_never_executed(self):
        result = Pipeline(strip_info=False).run("raise RuntimeError('must not execute')")
        self.assertIn("raise RuntimeError", result.source.text)

    def test_invalid_edits_rejected(self):
        cases = [
            [Replacement(-1, 1, "")],
            [Replacement(0, 4, "")],
            [Replacement(2, 1, "")],
            [Replacement(0, 2, ""), Replacement(1, 3, "")],
            [Replacement(1, 1, "x"), Replacement(1, 1, "y")],
        ]
        for edits in cases:
            with self.subTest(edits=edits), self.assertRaises(ValueError):
                Pipeline._apply("abc", edits)
        self.assertEqual(Pipeline._apply("abc", [Replacement(0, 1, "A"), Replacement(1, 2, "B")]), "ABc")

    def test_each_pass_receives_updated_analysis(self):
        class CheckUpdated(BasePass):
            def run(self, context):
                assert context.by_type.get("BinOp")
                return []

        result = Pipeline(strip_info=False).add(ReplaceNumber()).add(CheckUpdated()).run("x = 2")
        self.assertEqual(result.source.text, "x = (1 + 1)")

    def test_invalid_pass_output_is_rejected(self):
        class BrokenPass(BasePass):
            def run(self, context):
                return [Replacement(0, len(context.source.text), "x = (")]

        with self.assertRaises(SyntaxError):
            Pipeline(strip_info=False).add(BrokenPass()).run("x = 1")


if __name__ == "__main__":
    unittest.main()


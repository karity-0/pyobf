import ast
import contextlib
import io
import unittest

from pyobf import Pipeline, SourceDocument
from pyobf.passes import StripInfoPass


class StripInfoTests(unittest.TestCase):
    def transform(self, source):
        result = Pipeline().run(source)
        self.assertIn('StripInfoPass', result.applied_passes)
        return result.source.text

    def output(self, source):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            exec(compile(source, '<test>', 'exec'), {})
        return stream.getvalue()

    def equivalent(self, source):
        transformed = self.transform(source)
        self.assertEqual(self.output(transformed), self.output(source))
        return transformed

    def test_default_pipeline_always_shortens_names_and_comments(self):
        code = '# readable explanation\ndef very_long_function(very_long_argument):\n    very_long_result = very_long_argument * 2\n    return very_long_result\nprint(very_long_function(very_long_argument=4))\n'
        result = self.equivalent(code)
        self.assertNotIn('very_long_', result)
        self.assertNotIn('readable explanation', result)
        self.assertLess(len(result), len(code))

    def test_frequency_weighted_assignment_and_collisions(self):
        code = 'a, b, c = 1, 2, 3\nlong_identifier = 4\nother_identifier = 5\nprint(a, b, c, long_identifier, long_identifier, other_identifier)'
        result = self.equivalent(code)
        self.assertNotIn('long_identifier', result)
        self.assertIn('a, b, c = 1, 2, 3', result)
        self.assertEqual(result, self.transform(code))

    def test_nested_scopes_closure_nonlocal_and_global(self):
        self.equivalent('''module_counter = 2
def outer_function(initial_value):
    local_counter = initial_value
    def update_function(increment):
        nonlocal local_counter
        global module_counter
        local_counter += increment
        module_counter += local_counter
        return local_counter
    return update_function
callback = outer_function(initial_value=3)
print(callback(increment=4), callback(1), module_counter)
''')

    def test_shadowing_defaults_and_decorators(self):
        result = self.equivalent('''long_value = 5
def decorator_function(callback):
    def wrapped_function(*arguments, **keywords):
        return callback(*arguments, **keywords)
    return wrapped_function
@decorator_function
def long_function(long_value=long_value):
    long_local = long_value + 1
    return long_local
print(long_function(long_value=8), long_value)
''')
        self.assertIn('long_value=', result)  # Decorated callable keeps its interface.

    def test_escaped_callbacks_keep_keyword_parameters(self):
        result = self.equivalent('''def long_callback(long_parameter):
    long_result = long_parameter + 1
    return long_result
alias_callback = long_callback
callbacks = [alias_callback]
print(callbacks[0](long_parameter=3))
''')
        self.assertIn('long_parameter', result)
        self.assertNotIn('long_result', result)

    def test_kwargs_expansion_preserves_signature(self):
        result = self.equivalent('''def long_function(long_parameter):
    long_local = long_parameter * 2
    return long_local
print(long_function(**{'long_parameter': 7}))
''')
        self.assertIn('long_parameter', result)
        self.assertNotIn('long_local', result)

    def test_positional_only_varargs_kwargs_and_keyword_only(self):
        result = self.equivalent('''def long_function(positional_parameter, /, regular_parameter=2, *extra_arguments, keyword_parameter=3, **extra_keywords):
    return positional_parameter, regular_parameter, extra_arguments, keyword_parameter, extra_keywords
print(long_function(1, 4, 5, keyword_parameter=6, unknown=7))
''')
        for name in ('positional_parameter', 'regular_parameter', 'extra_arguments', 'keyword_parameter', 'extra_keywords'):
            self.assertNotIn(name, result)
        self.assertIn('unknown=7', result)

    def test_comprehension_scopes_walrus_and_generator(self):
        self.equivalent('''outer_value = 10
long_items = [(long_item, (last_value := long_item + outer_value)) for long_item in range(4)]
long_mapping = {long_key: long_key + outer_value for long_key in range(3)}
long_set = {long_item for long_item in range(2)}
long_generator = (long_item + outer_value for long_item in range(3))
print(long_items, last_value, long_mapping, sorted(long_set), list(long_generator), outer_value)
''')

    def test_nested_comprehensions_and_first_iter_scope(self):
        self.equivalent('''long_items = [1, 2]
print([(long_items, inner_value) for long_items in long_items for inner_value in [long_items + 1]])
print([[outer_item + inner_item for inner_item in range(3)] for outer_item in range(2)])
''')

    def test_same_line_lambdas_and_default_scopes(self):
        self.equivalent('''long_value = 7
first_callback, second_callback = (lambda argument: argument + long_value), (lambda argument: argument * long_value)
def long_function(callback=(lambda argument: argument + long_value)):
    return (lambda argument: callback(argument))(4)
print(first_callback(argument=3), second_callback(argument=2), long_function())
''')

    def test_classes_properties_inheritance_dataclasses_and_keywords(self):
        result = self.equivalent('''from dataclasses import dataclass
@dataclass
class LongRecord:
    long_field: int
    def long_method(self, long_argument):
        long_local = self.long_field + long_argument
        return long_local
class LongChild(LongRecord):
    def long_method(self, long_argument):
        long_local = super().long_method(long_argument=long_argument)
        return long_local + 1
long_instance = LongChild(long_field=3)
print(long_instance.long_method(long_argument=4), long_instance.long_field)
''')
        self.assertIn('long_method', result)
        self.assertIn('long_field', result)
        self.assertNotIn('long_local', result)

    def test_class_body_fallback_and_method_closure(self):
        self.equivalent('''long_value = 7
def outer_function():
    closed_value = 4
    class LongClass:
        long_value = long_value + 1
        def long_method(self):
            long_local = closed_value + self.long_value
            return long_local
    return LongClass()
print(outer_function().long_method(), long_value)
''')

    def test_import_aliases_preserve_module_paths(self):
        result = self.equivalent('''import math
import os.path
from math import sqrt
import math as long_math_alias
print(math.pi, math.pi, math.pi, math.pi, math.pi, math.pi)
print(sqrt(4), sqrt(9), sqrt(16), sqrt(25), sqrt(36), sqrt(49))
print(os.path.basename('hello/world'), long_math_alias.pi)
''')
        self.assertIn('import os.path', result)
        self.assertNotIn('long_math_alias', result)
        self.assertIn('import math as ', result)
        self.assertIn('from math import sqrt as ', result)

    def test_except_binding_and_match_captures(self):
        self.equivalent('''try:
    raise ValueError('boom')
except ValueError as long_error:
    print(str(long_error))
long_items = [{'key': 3, 'other': 4}, [1, 2, 3]]
for long_item in long_items:
    match long_item:
        case {'key': captured_value, **remaining_values}:
            print(captured_value, remaining_values)
        case [first_value, *other_values] as captured_list:
            print(first_value, other_values, captured_list)
''')

    def test_fstrings_debug_labels_format_specs_and_inner_comprehension(self):
        code = '''long_value = 7
long_width = 4
print(f'{long_value=}; {long_value:{long_width}}')
print(f'{[long_item + long_value for long_item in range(2)]=}')
print(f'{(lambda *long_arguments: sum(long_arguments))(1, 2)=}')
'''
        result = self.equivalent(code)
        self.assertIn('long_value=', result)  # A displayed label is runtime string data.
        self.assertNotIn('long_value = 7', result)

    def test_recursion_async_generators_and_private_keyword_calls(self):
        self.equivalent('''import asyncio
def factorial_function(long_number):
    return 1 if long_number == 0 else long_number * factorial_function(long_number=long_number - 1)
async def async_function(long_number):
    local_result = long_number + 1
    return local_result
def generator_function(long_number):
    for generated_value in range(long_number):
        yield generated_value
print(factorial_function(long_number=4), asyncio.run(async_function(long_number=3)), list(generator_function(long_number=3)))
''')

    def test_reflection_preserves_names(self):
        for expression in ('locals()', 'globals()', 'vars()', "eval('long_value')", "exec('print(long_value)')"):
            source = 'long_value = 3\n' + expression
            result = self.transform(source)
            self.assertIn('long_value = 3', result)
        self.equivalent('long_value = 3\nprint(eval("long_value"))')

    def test_exports_forward_annotations_dunders_and_docstrings(self):
        code = '''__all__ = ['long_function']
class LongClass: pass
def long_function(long_parameter: "LongClass"):
    """Useful runtime documentation."""
    long_local = 3
    return long_local
print(long_function(LongClass()), long_function.__doc__, __all__)
'''
        result = self.equivalent(code)
        self.assertIn('def long_function(', result)
        self.assertIn('"LongClass"', result)
        self.assertIn('Useful runtime documentation.', result)
        self.assertNotIn('long_local', result)

    def test_dynamic_exports_and_star_imports(self):
        for code in ('__all__ = list(("long_value",))\nlong_value = 3\nprint(long_value)', 'from math import *\nlong_value = sqrt(4)\nprint(long_value)'):
            result = self.equivalent(code)
            self.assertIn('long_value', result)

    def test_bom_cp949_newlines_and_comment_directives(self):
        for encoding, prefix in (('utf-8-sig', ''), ('cp949', '# coding: cp949\r\n')):
            text = prefix + '# removable comment\r\n긴변수이름 = 2\r\nprint(긴변수이름)\r\n'
            document = SourceDocument.from_bytes(text.encode(encoding))
            result = Pipeline().run(document).source
            self.assertEqual(result.encoding, document.encoding)
            self.assertNotIn('removable comment', result.text)
            self.assertNotIn('긴변수이름', result.text)
            self.assertIn('\r\n', result.text)
            self.assertEqual(result.to_bytes().decode(encoding), result.text)
        result = self.transform('#!/usr/bin/env python\n# coding: utf-8\nlong_value = 3  # type: int\n# another comment\n')
        self.assertIn('#!/usr/bin/env python', result)
        self.assertIn('# coding: utf-8', result)
        self.assertIn('# type: int', result)
        self.assertNotIn('another comment', result)

    def test_macro_pass_composition_shortens_generated_helpers(self):
        source = 'def long_function(long_argument):\n    @protect_start(cff, junk)\n    long_message = @{"hello"}\n    print(long_message, long_argument)\n    @protect_end\nlong_function(3)\n'
        result = Pipeline().run(source)
        self.assertEqual(result.applied_passes, ('StringEncryptionPass', 'ProtectionPass', 'StripInfoPass'))
        self.assertEqual(self.output(result.source.text), 'hello 3\n')
        self.assertNotIn('_pe', result.source.text)
        self.assertNotIn('long_function', result.source.text)

    def test_deletion_annotated_assignment_and_with_bindings(self):
        self.equivalent('''import contextlib
long_value: int = 3
with contextlib.nullcontext(long_value) as long_context:
    long_context += 2
print(long_context)
del long_value
long_value = 8
print(long_value)
''')

    def test_debug_symbols_in_compiled_function_are_shorter(self):
        code = 'def descriptive_function(descriptive_argument):\n    descriptive_local = descriptive_argument + 1\n    return descriptive_local\nprint(descriptive_function(descriptive_argument=3))'
        transformed = self.transform(code)
        compiled = compile(transformed, '<test>', 'exec')
        function_code = next(value for value in compiled.co_consts if hasattr(value, 'co_varnames'))
        self.assertNotIn('descriptive_function', function_code.co_name)
        self.assertNotIn('descriptive_argument', function_code.co_varnames)
        self.assertNotIn('descriptive_local', function_code.co_varnames)

    def test_fstring_keyword_names_cannot_collide_with_short_parameters(self):
        self.equivalent('''def long_function(long_argument, **long_keywords):
    return long_argument, long_keywords
print(f'{long_function(long_argument=1, a=2, b=3)}')
''')

    def test_immediate_lambda_keyword_parameters_are_shortened(self):
        result = self.equivalent('print((lambda long_parameter: long_parameter + 1)(long_parameter=4))')
        self.assertNotIn('long_parameter', result)

    def test_mutated_exports_and_aliased_reflection_preserve_names(self):
        for code in ('__all__ = []\n__all__ += ["long_value"]\nlong_value = 3\nprint(long_value)', 'from builtins import eval as evaluator\nlong_value = 3\nprint(evaluator("long_value"))'):
            self.assertIn('long_value = 3', self.equivalent(code))


if __name__ == '__main__':
    unittest.main()

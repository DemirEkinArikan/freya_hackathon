"""Reject indirect unsafe code without executing the untrusted source."""
import unittest

from builder.validation import validate_code


class CodeSafetyRegressionTests(unittest.TestCase):
    def assert_rejected(self, source, fragment):
        errors = validate_code(source, require_fallback=False)
        self.assertTrue(any(fragment in error for error in errors), errors)

    def test_unsafe_builtin_aliases_and_containers_are_rejected(self):
        for builtin in ("eval", "exec", "open", "compile", "getattr", "globals", "locals", "input"):
            sources = (f"runner = {builtin}\nrunner(payload)",
                       f"runners = [{builtin}]\nrunners[0](payload)",
                       f"runners = dict(operation={builtin})\nrunners['operation'](payload)",
                       f"apply = lambda function: function(payload)\napply({builtin})")
            for source in sources:
                with self.subTest(builtin=builtin, source=source):
                    self.assert_rejected(source, "unsafe builtin references")

    def test_file_and_real_clock_imports_cannot_hide_behind_aliases(self):
        for source in ("from json import load as parse\nparse(stream)",
                       "from json import dump as serialize\nserialize(value, stream)",
                       "from datetime import datetime as stamp\nclock = stamp.now\nclock()"):
            with self.subTest(source=source):
                self.assert_rejected(source, "unsafe")

    def test_import_submodule_does_not_inherit_root_module_permission(self):
        for source in ("from json.tool import main\nmain()", "import json.tool as tool\ntool.main()",
                       "from re._compiler import compile as compiler\ncompiler(pattern)"):
            with self.subTest(source=source):
                self.assert_rejected(source, "unapproved imports")

    def test_wildcard_imports_cannot_smuggle_unexamined_names(self):
        for module in ("json", "re", "datetime", "decimal", "math", "collections"):
            with self.subTest(module=module):
                self.assert_rejected(f"from {module} import *", "wildcard imports")

    def test_truthy_constant_while_loops_are_rejected(self):
        for condition in ("True", "1", "2", "-1", "'forever'", "b'forever'"):
            with self.subTest(condition=condition):
                self.assert_rejected(f"while {condition}:\n    pass", "unbounded while loop")

    def test_safe_arithmetic_and_json_aliases_keep_existing_fallback_contract(self):
        source = """from json import loads as decode
from decimal import Decimal as money
from datetime import date as calendar_day
try:
    terms = decode(vars['terms_json'])
    cents = int(money(terms['amount']) * 100)
    day = calendar_day.fromisoformat(terms['date'])
    vars['status'] = 'valid' if cents > 0 and day.year > 0 else 'invalid'
except Exception:
    vars['status'] = 'invalid'
"""
        self.assertEqual(validate_code(source), [])

    def test_bounded_for_loop_and_false_constant_while_are_not_rejected(self):
        source = """try:
    total = 0
    for value in [1, 2, 3]:
        total += value
    while False:
        total += 1
    while 0:
        total += 1
    vars['total'] = total
except Exception:
    vars['total'] = 0
"""
        self.assertEqual(validate_code(source), [])


if __name__ == "__main__":
    unittest.main()

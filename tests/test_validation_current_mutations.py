"""Invented current compiler candidates at input, entry-route and budget boundaries.

Validation must remain static. These mutations use no candidate execution,
models, external APIs, evaluator fixtures, or expected evaluator records.
"""
from copy import deepcopy
import unittest
from unittest.mock import patch

from builder.guards import GuardCompileError, compile_guarded_workflow
from builder.validation import validate_code, validate_workflow
from test_call_input_formats import authored


class CurrentCompilerMutationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = authored({"now": "offset_datetime"})
        talk = cls.context["plan"]["nodes"]["talk"]
        talk.update(on_enter={"type": "silent"}, passthrough=True,
                    passthrough_default_transition="close")
        # Passthrough routes read a variable; an intent would follow nothing but
        # the always fallback that deterministic-first hosts check before it.
        for row in talk["transitions"]:
            row["condition"] = {"logic": "or", "conditions": [
                {"variable": "answer_text", "operator": "equals", "operand": row["target_node"]}]}
        talk["transitions"].append({"target_node": "close", "condition": {"operator": "always"}})
        # Exercise the newly inserted administrative turn-stop path as well as
        # the source-authored passthrough route in one current compiled control.
        talk.pop("max_node_turns")
        talk.pop("max_turn_transition")
        cls.original = compile_guarded_workflow(**cls.context)["workflow"]
        cls.snapshot = deepcopy(cls.original)

    def checked(self, workflow, *, guarded=False, context=None):
        context = context or self.context
        guard_context = {key: value for key, value in context.items() if key != "openapi"}
        with patch("builtins.exec", side_effect=AssertionError("validator executed candidate Python")), \
             patch("builtins.eval", side_effect=AssertionError("validator evaluated candidate Python")):
            return validate_workflow(workflow, context["openapi"],
                                     guard_context=guard_context if guarded else None)

    def mutated(self, change, *, guarded=False):
        workflow = deepcopy(self.original)
        change(workflow)
        errors = self.checked(workflow, guarded=guarded)
        self.assertTrue(errors, "current compiler mutation was accepted")
        self.assertEqual(self.original, self.snapshot)
        return errors

    def test_current_formatted_passthrough_and_budget_candidate_is_accepted_statically(self):
        self.assertEqual(self.checked(deepcopy(self.original)), [])
        self.assertEqual(self.checked(deepcopy(self.original), guarded=True), [])
        self.assertIn("bg_turn_budget_stop", self.original["nodes"])

    def test_passthrough_default_and_final_route_must_agree_without_canonical_check(self):
        self.mutated(lambda workflow: workflow["nodes"]["talk"].update(
            passthrough_default_transition=workflow["nodes"]["talk"]["transitions"][0]["target_node"]))

    def test_passthrough_final_route_cannot_wait_for_tool_call_without_canonical_check(self):
        self.mutated(lambda workflow: workflow["nodes"]["talk"]["transitions"][-1].update(is_tool_call=True))

    def test_missing_entry_fallback_and_duplicate_final_close_are_structural_errors(self):
        missing = self.mutated(lambda workflow: workflow["nodes"]["talk"]["transitions"].pop())
        self.assertTrue(any("final unconditional fallback" in error for error in missing), missing)
        duplicate = self.mutated(lambda workflow: workflow["nodes"]["bg_close_api"]["pre_actions"].append(
            deepcopy(workflow["nodes"]["bg_close_api"]["pre_actions"][0])))
        self.assertTrue(any("close_call" in error for error in duplicate), duplicate)

    def test_formatted_readonly_input_cannot_be_forged_by_extraction(self):
        context = deepcopy(self.context)
        context["plan"]["nodes"]["talk"]["extraction_schema"].append(
            {"name": "now", "type": "string", "min_confidence": 1})
        with self.assertRaises(GuardCompileError):
            compile_guarded_workflow(**context)
        self.mutated(lambda workflow: workflow["nodes"]["talk"]["extraction_schema"].append(
            {"name": "now", "type": "string", "min_confidence": 1}), guarded=True)

    def test_exact_current_context_cannot_authorize_unresolved_required_binding(self):
        context = deepcopy(self.context)
        context["plan"]["nodes"]["talk"]["task_message"] = "Use {{unsupplied_reference_clock}}."
        workflow = compile_guarded_workflow(**context)["workflow"]
        for guarded in (False, True):
            with self.subTest(guarded=guarded):
                errors = self.checked(workflow, guarded=guarded, context=context)
                self.assertTrue(any("unresolved required binding unsupplied_reference_clock" in error
                                    for error in errors), errors)

    def test_gate_reset_and_public_route_bypass_require_trusted_compiler_correspondence(self):
        gate = self.original["nodes"]["talk"]["passthrough_default_transition"]
        self.mutated(lambda workflow: workflow["nodes"][gate]["pre_actions"][0]["function"]["parameters"]
                     ["properties"]["code"].update(default="vars['bg_control_entries'] = '0'"), guarded=True)
        def bypass(workflow):
            talk = workflow["nodes"]["talk"]
            talk["passthrough_default_transition"] = "close"
            talk["transitions"][-1]["target_node"] = "close"
        self.mutated(bypass, guarded=True)

    def test_private_api_targets_are_not_authorized_by_current_entry_route(self):
        context = deepcopy(self.context)
        talk = context["plan"]["nodes"]["talk"]
        talk["passthrough_default_transition"] = "bg_close_api"
        talk["transitions"][-1]["target_node"] = "bg_close_api"
        with self.assertRaises(GuardCompileError):
            compile_guarded_workflow(**context)
        def bypass(workflow):
            workflow["nodes"]["talk"]["passthrough_default_transition"] = "bg_close_api"
            workflow["nodes"]["talk"]["transitions"][-1]["target_node"] = "bg_close_api"
        self.mutated(bypass, guarded=True)

    def test_gate_container_aliases_of_unsafe_builtins_are_rejected_statically(self):
        gate = self.original["nodes"]["talk"]["passthrough_default_transition"]
        original_code = self.original["nodes"][gate]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
        for source in ("danger = [eval]\nagain = danger\nagain[0]('1')",
                       "danger = {'read': open}\nagain = danger\nagain['read']('/tmp/invented-input')"):
            with self.subTest(source=source):
                errors = self.mutated(lambda workflow: workflow["nodes"][gate]["pre_actions"][0]["function"]["parameters"]
                                      ["properties"]["code"].update(default=original_code + "\n" + source))
                self.assertTrue(any("unsafe builtin references" in error for error in errors), errors)

    def test_rebinding_vars_cannot_forge_the_exception_fallback_proof(self):
        safe = "try:\n    vars['status'] = 'ready'\nexcept Exception:\n    vars['status'] = 'failed'"
        self.assertEqual(validate_code(safe), [])
        sources = {
            "assignment": safe.replace("    vars['status'] = 'failed'", "    vars = {}\n    vars['status'] = 'failed'"),
            "destructuring": safe.replace("    vars['status'] = 'failed'", "    vars, scratch = {}, 1\n    vars['status'] = 'failed'"),
            "handler_name": safe.replace("except Exception:", "except Exception as vars:"),
            "function_name": "def vars():\n    return {}\n" + safe,
            "class_name": "class vars:\n    pass\n" + safe,
            "argument": "def rescue(vars):\n    return vars\n" + safe,
            "import_alias": "import json as vars\n" + safe,
            "match_capture": "match {}:\n    case vars:\n        pass\n" + safe,
            "mapping_capture": "match {}:\n    case {**vars}:\n        pass\n" + safe,
            "delete_mapping": "del vars\n" + safe,
        }
        with patch("builtins.exec", side_effect=AssertionError("validator executed code")), \
             patch("builtins.eval", side_effect=AssertionError("validator evaluated code")):
            for name, source in sources.items():
                with self.subTest(rebinding=name):
                    self.assertTrue(validate_code(source), "fallback writes a local mapping instead of engine vars")

    def test_dunder_declarations_cannot_hide_an_unbounded_iterator(self):
        safe = "try:\n    vars['status'] = 'ready'\nexcept Exception:\n    vars['status'] = 'failed'"
        self.assertEqual(validate_code(safe), [])
        source = ("class Infinite:\n"
                  "    def __iter__(self):\n        return self\n"
                  "    def __next__(self):\n        return 0\n"
                  "for value in Infinite():\n    pass\n" + safe)
        with patch("builtins.exec", side_effect=AssertionError("validator ran an infinite iterator")), \
             patch("builtins.eval", side_effect=AssertionError("validator evaluated candidate code")):
            self.assertTrue(validate_code(source), "dunder declarations hide unbounded iteration")

    def test_dynamic_type_classes_cannot_hide_an_unbounded_iterator(self):
        safe = "try:\n    vars['status'] = 'ready'\nexcept Exception:\n    vars['status'] = 'failed'"
        methods = "{'__iter__': lambda self: self, '__next__': lambda self: 0}"
        sources = {
            "type_call": f"Infinite = type('Infinite', (), {methods})\n",
            "type_of_type": f"Infinite = type(int)('Infinite', (), {methods})\n",
            "alias": f"make = type\nInfinite = make('Infinite', (), {methods})\n",
            "container": f"Infinite = [type][0]('Infinite', (), {methods})\n",
            "starred": f"spec = ('Infinite', (), {methods})\nInfinite = type(*spec)\n",
        }
        with patch("builtins.exec", side_effect=AssertionError("validator ran an infinite iterator")), \
             patch("builtins.eval", side_effect=AssertionError("validator evaluated candidate code")):
            # The compiler's own type checks stay valid.
            self.assertEqual(validate_code("value = 1\nok = type(value) is not int and type(value) not in (float,)\n"
                                           "same = type(value) is type(ok)\n" + safe), [])
            for name, source in sources.items():
                with self.subTest(form=name):
                    errors = validate_code(source + "for value in Infinite():\n    pass\n" + safe)
                    self.assertTrue(any("dynamic classes" in error for error in errors), errors)

    def test_growing_the_iterated_list_inside_its_loop_is_rejected(self):
        safe = "try:\n    vars['status'] = 'ready'\nexcept Exception:\n    vars['status'] = 'failed'"
        sources = {
            "append": "for value in items:\n    items.append(value)\n",
            "extend": "for value in items:\n    items.extend([value])\n",
            "insert": "for value in items:\n    items.insert(0, value)\n",
            "augmented": "for value in items:\n    items += [value]\n",
            "slice": "for value in items:\n    items[len(items):] = [value]\n",
            "comprehension": "[items.append(value) for value in items]\n",
            "lazy_wrapper": "for index, value in enumerate(items):\n    items.append(value)\n",
            "alias": "again = items\nfor value in items:\n    again.append(value)\n",
            "unbound_method": "for value in items:\n    list.append(items, value)\n",
            "vars_mapping": "vars['queue'] = items\nfor value in vars['queue']:\n    vars['queue'].append(value)\n",
        }
        with patch("builtins.exec", side_effect=AssertionError("validator ran an unbounded loop")), \
             patch("builtins.eval", side_effect=AssertionError("validator evaluated candidate code")):
            for source in ("for value in list(items):\n    items.append(value)\n",
                           "for value in items[:]:\n    items.append(value)\n",
                           "copied = []\nfor value in items:\n    copied.append(value)\n"):
                with self.subTest(bounded=source):
                    self.assertEqual(validate_code("items = [0]\n" + source + safe), [])
            for name, source in sources.items():
                with self.subTest(form=name):
                    errors = validate_code("items = [0]\n" + source + safe)
                    self.assertTrue(any("growing an iterated collection" in error for error in errors), errors)
            errors = validate_code("items = [0]\nadd = items.append\nfor value in items:\n    add(value)\n" + safe)
            self.assertTrue(any("growth methods must be called directly" in error for error in errors), errors)

    def test_current_gate_code_rejects_dynamic_iterators_and_self_growth(self):
        gate = self.original["nodes"]["talk"]["passthrough_default_transition"]
        original_code = self.original["nodes"][gate]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
        sources = {
            "dynamic classes": "Infinite = type('Infinite', (), {'__iter__': lambda self: self, '__next__': lambda self: 0})\n"
                               "for value in Infinite():\n    pass",
            "growing an iterated collection": "items = [0]\nfor value in items:\n    items.append(value)",
        }
        for message, source in sources.items():
            with self.subTest(message=message):
                errors = self.mutated(lambda workflow: workflow["nodes"][gate]["pre_actions"][0]["function"]["parameters"]
                                      ["properties"]["code"].update(default=original_code + "\n" + source))
                self.assertTrue(any(message in error for error in errors), errors)

    def test_current_api_rejects_external_url_invalid_result_path_and_null_gate(self):
        changes = {
            "external_url": lambda workflow: workflow["nodes"]["bg_close_api"]["pre_actions"][0]["function"]
                ["parameters"]["properties"]["url"].update(default="https://collector.invalid/close_call"),
            "invalid_result_path": lambda workflow: workflow["nodes"]["bg_close_api"]["response_mapping"][0].update(path="data..ok"),
            "null_turn_stop": lambda workflow: workflow["nodes"]["talk"].update(max_turn_transition=None),
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                self.mutated(change)


if __name__ == "__main__":
    unittest.main()

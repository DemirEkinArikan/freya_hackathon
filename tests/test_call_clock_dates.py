"""Invented tests: dates are resolved against the call's supplied now, never the platform date."""
import copy
import unittest

import test_refusal_view
from test_dialogue_facts import PRODUCTS
from test_guards import fixture
from builder.guards import compile_guarded_workflow, validate_guarded_workflow
from builder.validation import validate_workflow

ANCHOR = "This call's current date and time is {{now}}:"


class CallClockDateTests(unittest.TestCase):
    def test_date_and_clock_bound_extraction_names_the_call_clock(self):
        context = fixture()
        # The fixture binds day/at to the guard date/time inputs and amount to a non-clock input.
        context["plan"]["nodes"]["talk"]["extraction_schema"] += [
            {"name": "day", "type": "date", "description": "Proposed day.", "min_confidence": 0.7},
            {"name": "at", "type": "string", "validation_pattern": "^[0-9]{2}:[0-9]{2}$", "min_confidence": 0.7},
            {"name": "seen", "type": "datetime", "min_confidence": 0.7},
            {"name": "amount", "type": "float", "description": "Amount.", "min_confidence": 0.7}]
        plan = copy.deepcopy(context["plan"])
        workflow = compile_guarded_workflow(**context)["workflow"]
        self.assertEqual(context["plan"], plan)
        entries = {entry["name"]: entry for entry in workflow["nodes"]["talk"]["extraction_schema"]}
        self.assertTrue(entries["day"]["description"].startswith("Proposed day. " + ANCHOR))
        self.assertTrue(entries["at"]["description"].startswith(ANCHOR))
        self.assertTrue(entries["seen"]["description"].startswith(ANCHOR))
        self.assertEqual(entries["amount"]["description"], "Amount.")
        self.assertNotIn("description", entries["answer_text"])
        self.assertEqual(validate_guarded_workflow(workflow, **context), [])
        self.assertEqual(validate_workflow(workflow, context["openapi"]), [])

    def test_kernel_asks_again_for_a_date_before_the_call_date(self):
        # Call clock 2031-05-02; a day before it is a misresolved answer, not a past-date policy refusal.
        view = test_refusal_view.RefusalViewTests("test_refusal_text_and_caller_words_never_reach_the_view")
        view.setUp()
        state = view.verified()
        first = PRODUCTS[1]["product_id"]
        view.refuse(state, "stage_promise", {"product_id": first, "amount": "50.00", "date": "2031-05-01"}, "missing_input")
        view.refuse(state, "stage_promise", {"product_id": first, "amount": "50.00", "date": "2031-06-30"}, "out_of_policy")
        view.refuse(state, "schedule_callback", {"date": "2031-05-01", "time": "11:00"}, "missing_input")
        view.refuse(state, "schedule_callback", {"date": "2031-05-02", "time": "11:00"}, "out_of_policy")


if __name__ == "__main__":
    unittest.main()

"""Raw result types reach the sealed kernel; invented API, exact compiled replay."""
import unittest
from unittest.mock import patch

import builder.guards as guards
from builder.guards import _catalogue, compile_guarded_workflow
from test_guard_regressions import rehash
from test_guards import fixture
from tools.replay_guarded import replay

STRING_FLAG = {"type": "string", "enum": ["true", "false"]}
KEY = 'schedule_callback:{"customer_id":"member-7","date":"2031-05-06","time":"10:00"}'


def context(string_flag=True):
    source = fixture()
    if string_flag:
        # The success flag of every operation is documented as a string.
        for path in source["openapi"]["paths"].values():
            path["post"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["ok"] = dict(STRING_FLAG)
        rehash(source, refresh_api=True)
    source["plan"]["nodes"]["talk"]["extraction_schema"] = [
        {"name": name, "type": "string", "min_confidence": 0.95} for name in ("day", "at", "outcome", "close_reason")]
    return source


def run(source, receipt, close_ok="true"):
    routes = source["plan"]["nodes"]["talk"]["transitions"]
    def frame(action, **values):
        index = next(i for i, route in enumerate(routes) if route["target_node"] == action)
        return {"node": "talk", "values": values, "intent_transition": index}
    script = {"version": 1, "initial_inputs": {"customer_id": "member-7", "now": "2031-05-02"},
              "frames": [frame("callback", day="2031-05-06", at="10:00"), frame("close")],
              "responses": [{"operation": "schedule_callback", "envelope": {"status_code": 200, "data": receipt}},
                            {"operation": "close_call", "envelope": {"status_code": 200, "data": {"ok": close_ok}}}]}
    workflow = compile_guarded_workflow(**source)["workflow"]
    report = replay(workflow, {k: v for k, v in source.items() if k != "openapi"}, source["openapi"], script)
    return report, report["final_state"]


class ResultTypeEvidenceTests(unittest.TestCase):
    def test_catalogue_records_declared_result_types(self):
        for string_flag, kind in ((True, "string"), (False, "boolean")):
            source = context(string_flag)
            catalog = _catalogue(source["contract"], source["openapi"], None)
            self.assertEqual(catalog["schedule_callback"]["result_types"], {"error": ["string"], "ok": [kind]})

    def test_wrong_typed_receipt_writes_nothing_and_never_closes(self):
        source = context()
        for value in (True, False, 1, {}, ["true"]):
            with self.subTest(value=value):
                report, state = run(source, {"ok": value})
                self.assertEqual([call["tool"] for call in report["calls"]], ["schedule_callback"])
                self.assertEqual(report["terminal_node"], "bg_quarantine")
                self.assertEqual(state["callbacks"], [])
                self.assertEqual(state["attempts"], {KEY: {"status": "unknown_persistence", "count": 1}})
                self.assertTrue(state["quarantined"])
                self.assertFalse(state["closed"] or state["close_attempted"])

    def test_wrong_typed_close_receipt_is_not_a_recorded_close(self):
        report, state = run(context(), {"ok": "true"}, close_ok=True)
        self.assertEqual([call["tool"] for call in report["calls"]], ["schedule_callback", "close_call"])
        self.assertEqual(len(state["callbacks"]), 1)
        self.assertFalse(state["closed"])
        self.assertTrue(state["quarantined"])

    def test_correctly_typed_receipts_are_unchanged(self):
        # The last case is a boolean-declared API whose hook sees strings: a
        # string is never type evidence, so it records as before.
        for string_flag, receipt, close_ok in ((True, {"ok": "true"}, "true"), (True, {"ok": "true", "error": None}, "true"),
                                               (False, {"ok": True}, True), (False, {"ok": "true"}, "true")):
            with self.subTest(string_flag=string_flag, receipt=receipt):
                report, state = run(context(string_flag), receipt, close_ok)
                self.assertEqual([call["tool"] for call in report["calls"]], ["schedule_callback", "close_call"])
                self.assertEqual(report["terminal_node"], "bg_terminal")
                self.assertEqual(state["callbacks"], [{"customer_id": "member-7", "date": "2031-05-06", "time": "10:00"}])
                self.assertEqual(state["attempts"][KEY], {"status": "recorded", "count": 1})
                self.assertTrue(state["closed"])
                self.assertFalse(state["quarantined"])

    def test_the_type_check_is_what_stops_the_write(self):
        # Mutant: the kernel ignores the raw types. The same wrong-typed
        # receipt is then recorded and the close is sent.
        mutant = guards._KERNEL.replace("def guard_mistyped(data, spec):\n", "def guard_mistyped(data, spec):\n    return False\n")
        self.assertNotEqual(mutant, guards._KERNEL)
        with patch.object(guards, "_KERNEL", mutant):
            report, state = run(context(), {"ok": True})
        self.assertEqual([call["tool"] for call in report["calls"]], ["schedule_callback", "close_call"])
        self.assertEqual(state["attempts"][KEY], {"status": "recorded", "count": 1})


if __name__ == "__main__":
    unittest.main()

"""Hosted readiness K4: after a refused or rejected action the caller first hears a fixed sentence picked by code."""
import json
import unittest

from test_dialogue_facts import CUSTOMER, PRODUCTS
from test_guards import fixture
from test_replay_guarded import authored, frame
from builder.guards import (_KERNEL, _OUTCOME_RULE, OUTCOME_SPEECH, _catalogue, _code, _profile,
                            compile_guarded_workflow, validate_guarded_workflow)
from builder.validation import validate_workflow
from tools.replay_guarded import _route, replay

REFUSED = ("closed", "not_verified", "order", "missing_input", "out_of_policy", "blocked")


def code_of(node):
    return node["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]


class OutcomeSpeechTests(unittest.TestCase):
    def setUp(self):
        self.context = fixture()
        self.workflow = compile_guarded_workflow(**self.context)["workflow"]
        self.nodes = self.workflow["nodes"]
        self.gate = next(row["target_node"] for row in self.nodes["bg_init"]["transitions"] if row["description"] == "done")
        self.variables = {"customer_id": CUSTOMER, "now": "2031-05-02T11:00"}
        self.step("bg_init")

    def step(self, name, **values):
        """Execute one compiled code step on the shared variables and return the step it routes to."""
        self.variables.update(values)
        exec(code_of(self.nodes[name]), {"vars": self.variables})
        return _route(self.nodes[name], self.variables)[0]

    def result(self, name, **data):
        data = {"http_status": "200", **data}
        return self.step("bg_" + name + "_result", **{"bg_" + name + "_result_" + key: value for key, value in data.items()})

    def verify_both_factors(self):
        for field in ("postal_code", "memorable_word"):
            self.assertEqual(self.step("identity", answer_field=field, answer_text="invented"), "bg_identity_api")
            self.assertEqual(self.result("identity", ok=True, verified=True, wrong_answers_left=4), self.gate)

    def test_every_reason_code_has_one_fixed_sentence(self):
        namespace = {}
        exec(_KERNEL, namespace)
        self.assertEqual(set(OUTCOME_SPEECH), set(namespace["GUARD_REFUSALS"]) | {"blocked", "not_confirmed", "rejected"})
        self.assertEqual(set(REFUSED), set(namespace["GUARD_REFUSALS"]) | {"blocked"})
        for line in OUTCOME_SPEECH.values():
            self.assertTrue(line.strip())
            self.assertFalse(set("{}") & set(line))

    def test_refused_and_rejected_routes_speak_a_fixed_line_before_the_target_step(self):
        self.assertEqual(validate_guarded_workflow(self.workflow, **self.context), [])
        self.assertEqual(validate_workflow(self.workflow, self.context["openapi"]), [])
        expected = {"bg_commit_accept": ("not_confirmed",)}
        for name, node in self.context["plan"]["nodes"].items():
            if node["node_type"] == "guard_action" and node["action"] != "close_call":
                expected[name] = REFUSED
                if "bg_" + name + "_result" in self.nodes:
                    expected["bg_" + name + "_result"] = ("rejected",)
        for name, codes in expected.items():
            rows = self.nodes[name]["transitions"]
            self.assertEqual(rows[0]["target_node"], "bg_quarantine")  # quarantine keeps priority
            for row, code in zip(rows[1:], codes):
                self.assertEqual((row["target_node"], row["condition"]), ("bg_say_" + code + "_talk", {
                    "logic": "or", "conditions": [{"variable": "bg_outcome", "operator": "equals", "operand": code}]}))
        for code, line in OUTCOME_SPEECH.items():
            node = self.nodes["bg_say_" + code + "_talk"]
            self.assertEqual((node["node_type"], node["on_enter"], node["passthrough"], node["passthrough_default_transition"]),
                             ("conversation", {"type": "tts", "text": line}, True, self.gate))
            self.assertEqual([(row["target_node"], row["condition"]) for row in node["transitions"]], [(self.gate, {"operator": "always"})])
        # A refused close still ends at its own termination path, never in dialogue.
        for name in ("close", "bg_close_result"):
            self.assertNotIn("bg_outcome", json.dumps(self.nodes[name]["transitions"]))
        self.assertTrue(self.workflow["system_prompt"].endswith(_OUTCOME_RULE))

    def test_refusals_choose_the_line_from_their_reason(self):
        self.assertEqual(self.step("account"), "bg_say_not_verified_talk")
        self.assertEqual(self.step("callback", day="2031-05-06", at=""), "bg_say_missing_input_talk")
        self.assertEqual(self.step("identity", answer_field="memorable_word", answer_text="invented"), "bg_say_order_talk")
        facts = json.loads(self.variables["bg_facts"])
        self.assertEqual(facts["latest_action"], {"action": "verify_identity", "status": "refused", "reason": "order"})
        self.verify_both_factors()
        self.assertEqual(self.variables["bg_outcome"], "")
        self.assertEqual(self.step("identity", answer_field="postal_code", answer_text="invented"), "bg_say_closed_talk")
        self.assertEqual(self.step("account"), "bg_account_api")  # an allowed action speaks no outcome line
        self.assertEqual(self.variables["bg_outcome"], "")
        self.assertEqual(self.result("account", ok=True, products=json.dumps(PRODUCTS)), self.gate)
        first = PRODUCTS[1]["product_id"]
        self.assertEqual(self.step("stage", product=first, amount="1.00", day="2031-05-06"), "bg_say_out_of_policy_talk")
        self.assertEqual(self.step("stage", product=first, amount="50.00", day="2031-05-06"), self.gate)
        self.assertEqual(self.step("commit"), "bg_commit_confirm")
        self.assertEqual(self.step("bg_commit_accept", bg_consent="no"), "bg_say_not_confirmed_talk")
        self.assertFalse(json.loads(self.variables["bg_state"])["recorded_promises"])

    def test_a_bank_rejection_speaks_its_line_and_quarantine_wins_over_every_line(self):
        self.assertEqual(self.step("identity", answer_field="postal_code", answer_text="invented"), "bg_identity_api")
        self.assertEqual(self.result("identity", ok=False, error="not counted"), "bg_say_rejected_talk")
        self.assertEqual(json.loads(self.variables["bg_facts"])["latest_action"]["status"], "rejected")
        self.assertEqual(self.step("identity", answer_field="postal_code", answer_text="invented"), "bg_identity_api")
        self.assertEqual(self.result("identity", ok=True, verified="maybe", wrong_answers_left=4), "bg_quarantine")
        self.assertEqual(self.variables["bg_outcome"], "")
        self.assertEqual(self.step("account", bg_state="malformed-state"), "bg_quarantine")
        self.assertEqual(self.variables["bg_outcome"], "blocked")  # written, yet the quarantine row comes first

    def test_an_unlisted_fault_speaks_the_generic_line(self):
        profile = _profile(self.context["contract"], self.context["execution_policy"], self.context["plan"], None)
        catalog = _catalogue(self.context["contract"], self.context["openapi"], None)
        self.verify_both_factors()
        self.assertEqual(self.step("account"), "bg_account_api")
        self.assertEqual(self.result("account", ok=True, products=json.dumps(PRODUCTS)), self.gate)
        exec(_code("prepare", "unlisted_action", {}, profile, catalog, {}, {}), {"vars": self.variables})
        self.assertEqual(json.loads(self.variables["bg_facts"])["latest_action"]["status"], "blocked_or_malformed")
        self.assertEqual((self.variables["bg_route"], self.variables["bg_outcome"], self.variables["bg_quarantined"]), ("blocked", "blocked", ""))

    def test_local_replay_passes_the_line_on_the_way_back_to_the_dialogue(self):
        workflow, context, api, _ = authored()
        script = {"version": 1, "initial_inputs": {"customer_id": "synthetic-session", "now": "2031-05-02"},
                  "frames": [frame(context, "account")], "responses": []}
        report = replay(workflow, context, api, script)
        path = [step["node"] for step in report["steps"]]
        self.assertEqual(path[path.index("account") + 1], "bg_say_not_verified_talk")
        self.assertEqual(path[-1], "talk")
        self.assertIn({"node": "bg_say_not_verified_talk", "task_message": "Say nothing.", "entry_executed": True,
                       "on_enter": {"type": "tts", "text": OUTCOME_SPEECH["not_verified"]}}, report["prompts"])
        self.assertEqual(report["calls"], [])


if __name__ == "__main__":
    unittest.main()

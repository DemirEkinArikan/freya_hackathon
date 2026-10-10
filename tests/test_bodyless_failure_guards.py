"""Invented renamed APIs exercise the sealed kernel and actual result hook."""
import copy
import json
import unittest

from builder.action_bindings import compile_action_bindings
from builder.contract import compile_policy_contract
from builder.execution_requirements import compile_execution_requirements
from builder.guards import _catalogue, _profile, compile_guarded_workflow, run_guard_step
from builder.policy import compile_execution_policy
from builder.safety import digest
from builder.validation import validate_workflow


def invented_context():
    def text(enum=None):
        return {"type": "string", **({"enum": enum} if enum is not None else {})}
    aliases = {"verify_identity": "op_checkQ", "get_account": "op_itemsQ",
               "create_promise": "op_writeQ", "close_call": "op_closeQ"}
    paths = {"verify_identity": "/member/check", "get_account": "/member/items",
             "create_promise": "/ledger~actions/write", "close_call": "/contact/finish"}
    requests = {
        "verify_identity": {"customer_id": text(), "field": text(["member_key"]), "answer": text()},
        "get_account": {"customer_id": text()},
        "create_promise": {"customer_id": text(), "product_id": text(), "amount": {"type": "number"}, "date": text()},
        "close_call": {"customer_id": text(), "outcome": text(["promise", "no_promise", "not_verified", "not_reached"]),
                       "reason": text(["service_error", "refused", "failed", "unclear"])}}
    product = {"type": "object", "properties": {"product_id": text(), "type": text(["line"]),
        "days_late": {"type": "integer"}, "past_due": {"type": "number"},
        "floor_amount": {"type": "number"}, "caption": text()}}
    responses = {
        "verify_identity": {"ok": {"type": "boolean"}, "verified": {"type": "boolean"},
                            "wrong_answers_left": {"type": "integer"}},
        "get_account": {"ok": {"type": "boolean"}, "products": {"type": "array", "items": product}},
        "create_promise": {"accepted_wire": {"type": "boolean"}, "failure_wire": text(), "receipt_wire": text()},
        "close_call": {"ok": {"type": "boolean"}}}
    api = {"openapi": "3.0.3", "servers": [{"url": "https://invented.bank.invalid/v17"}], "paths": {}}
    for role, fields in requests.items():
        api["paths"][paths[role]] = {"post": {"operationId": aliases[role],
            "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": fields,
                "required": [key for key in fields if not (role == "close_call" and key == "reason")]}}}},
            "responses": {"200": {"content": {"application/json": {"schema": {"type": "object", "properties": responses[role]}}}}}}}
    source_pointer = "/paths/" + paths["create_promise"].replace("~", "~0").replace("/", "~1") + "/post/responses/"
    quote = "Invented rejected operation makes no persistent write."
    guarantee_rows = []
    for status in (472, 207):
        api["paths"][paths["create_promise"]]["post"]["responses"][str(status)] = {"description": quote}
        guarantee_rows.append({"http_status": status, "api_source": {
            "pointer": source_pointer + str(status) + "/description", "quote": quote}})
    profile = {"identity_fields": ["member_key"], "identity_wrong_limit": 4,
        "band_ranges": [{"name": "stage", "min": 1, "max": 99}], "deadline_days": {"stage": 6},
        "floor_fields": {"line": "floor_amount"}, "business_weekdays": [0, 1, 2, 3, 4], "holidays": [],
        "require_confirmation": True, "confirmation_scope": "each", "promise_failure_limit": 2,
        "record_notes": False, "note_reasons": ["service_error", "refused"],
        "policy_applicability": {"products": "single"},
        "promise_failure_disposition": {"reason": "service_error", "clear_pending": True},
        "promise_readback": {"product_fields": ["caption"]},
        "nonpersistent_failures": {aliases["create_promise"]: guarantee_rows},
        "tool_aliases": aliases, "result_fields": {"create_promise": {
            "ok": "accepted_wire", "error": "failure_wire", "promise_id": "receipt_wire"}},
        "close_reasons": {"promise": [], "no_promise": ["service_error", "refused"],
                          "not_verified": ["failed"], "not_reached": ["unclear"]},
        "close_precedence": ["promise", "no_promise"],
        "identity_close": {"failed": {"outcome": "not_verified", "reason": "failed"},
                           "incomplete": {"outcome": "not_verified", "reason": "failed"},
                           "unreached": {"outcome": "not_reached", "reason": "unclear"}}}
    actions = {"check": ("verify_identity", {"field": "factor", "answer": "answer"}),
               "account": ("get_account", {}),
               "stage": ("stage_promise", {"product_id": "item", "amount": "amount", "date": "day"}),
               "commit": ("commit_promises", {}), "close": ("close_call", {})}
    profile["execution_actions"] = [value[0] for value in actions.values()]
    witness = "Invented single-product source authorizes the declared operations and typed restrictions."
    sources = [{"id": "invented", "file": "invented.txt", "text": witness, "sha256": digest(witness)}]
    policy = {"version": 1, "goal": "Invented member update", "allowed_actions": list(aliases.values()),
              "sources": [{key: value for key, value in sources[0].items() if key != "text"}],
              "rules": [{"id": kind, "kind": kind, "value": value,
                         "source": {"id": "invented", "quote": witness}} for kind, value in profile.items()],
              "unsupported_constraints": []}
    plan = {"initial_node": "talk", "nodes": {"talk": {"name": "talk", "node_type": "conversation",
        "task_message": "Discuss the permitted update.", "transitions": [{"target_node": name,
            "condition": {"operator": "matches_intent", "description": "Customer requests " + name},
            "description": "declared action"} for name in actions]}}}
    plan["nodes"].update({name: {"name": name, "node_type": "guard_action", "action": action, "inputs": inputs,
        "success_node": "talk", "blocked_node": "talk", "failure_node": "talk"}
        for name, (action, inputs) in actions.items()})
    compile_execution_requirements(policy, sources, api)
    contract = compile_policy_contract(policy, sources, api)
    bindings = compile_action_bindings(policy, api)
    execution = compile_execution_policy(policy, bindings)
    context = {"plan": plan, "contract": contract, "openapi": api, "execution_policy": execution, "bindings": bindings}
    return context


class Session:
    def __init__(self):
        self.context = invented_context()
        self.workflow = compile_guarded_workflow(**self.context)["workflow"]
        self.profile = _profile(self.context["contract"], self.context["execution_policy"],
                                self.context["plan"], self.context["bindings"])
        self.catalog = _catalogue(self.context["contract"], self.context["openapi"], self.context["bindings"])
        self.profile["nonpersistent_failures"] = {"create_promise": self.profile["nonpersistent_failures"]["op_writeQ"]}
        self.variables = {"customer_id": "invented-member", "now": "2031-05-02", "factor": "member_key",
                          "answer": "invented-answer", "item": "invented-product", "amount": 20, "day": "2031-05-06"}
        self.code("bg_init")
        self.code("check")
        self.result("check", {"status_code": 200, "data": {"ok": True, "verified": True, "wrong_answers_left": 4}})
        self.code("account")
        self.result("account", {"status_code": 200, "data": {"ok": True, "products": [{"product_id": "invented-product",
            "type": "line", "days_late": 3, "past_due": 80, "floor_amount": 20, "caption": "invented account"}]}})
        self.code("stage")
        self.code("commit")

    @property
    def state(self):
        return json.loads(self.variables["bg_state"])

    def code(self, name):
        code = self.workflow["nodes"][name]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
        exec(code, {"vars": self.variables})

    def confirm(self):
        self.variables["bg_consent"] = "yes"
        self.code("bg_commit_accept")
        return self.variables["bg_body"]

    def hook(self, envelope):
        hook = self.workflow["nodes"]["bg_commit_api"]["pre_actions"][0]["post_process_code"]
        namespace = {"result": copy.deepcopy(envelope)}
        exec(hook, namespace)
        return namespace["result"]

    def result(self, action, envelope):
        node = self.workflow["nodes"]["bg_" + action + "_api"]
        namespace = {"result": copy.deepcopy(envelope)}
        exec(node["pre_actions"][0]["post_process_code"], namespace)
        for mapping in node["response_mapping"]:
            value = namespace["result"]
            try:
                for part in mapping["path"].split("."):
                    value = value[part]
            except (KeyError, TypeError):
                value = ""
            self.variables[mapping["variable"]] = value
        self.code("bg_" + action + "_result")
        return namespace["result"]

    def kernel_result(self, data):
        return run_guard_step("result", self.state, "commit_promises", result=data,
                              profile=self.profile, catalog=self.catalog)


class BodylessFailureGuardTests(unittest.TestCase):
    def test_compiled_workflow_remains_valid_with_renamed_result_fields(self):
        session = Session()
        self.assertEqual(validate_workflow(session.workflow, session.context["openapi"]), [])
        self.assertEqual(session.catalog["create_promise"]["path"], "/ledger~actions/write")
        self.assertEqual(session.catalog["create_promise"]["method"], "POST")

    def test_bodyless_kernel_failure_retries_immutable_confirmed_body_and_exhausts(self):
        session = Session()
        body = json.loads(session.confirm())
        result = session.kernel_result({"_http_status": 472})
        self.assertEqual(result[1], "allow")
        self.assertEqual(result[2], body)
        self.assertEqual(result[0]["promise_failures"], {"invented-product": 1})
        self.assertEqual(result[0]["consent"], session.state["consent"])
        result = run_guard_step("result", result[0], "commit_promises", result={"_http_status": "472", "ok": ""},
                                profile=session.profile, catalog=session.catalog)
        self.assertEqual(result[1], "failure")
        self.assertEqual(result[0]["promise_failures"], {"invented-product": 2})
        self.assertFalse(result[0]["quarantined"])
        self.assertNotIn("invented-product", result[0]["pending_promises"])
        self.assertEqual(result[0]["pending_notes"], {"invented-product": "service_error"})
        with self.assertRaises(ValueError):
            run_guard_step("prepare", result[0], "commit_promises", profile=session.profile, catalog=session.catalog)

    def test_mapped_missing_none_or_exact_empty_body_transports_status_and_retries(self):
        for body in ("missing", None, ""):
            session = Session()
            original = session.confirm()
            envelope = {"status_code": 472}
            if body != "missing":
                envelope["data"] = body
            with self.subTest(body=body):
                transported = session.result("commit", envelope)
                self.assertEqual(transported["data"], {"bg_http_status": 472})
                self.assertEqual(session.variables["bg_route"], "allow")
                self.assertEqual(session.variables["bg_body"], original)
                self.assertFalse(session.state["quarantined"])

    def test_opaque_error_body_requires_exact_status_guarantee(self):
        for body in ([], 0, False, " ", "malformed-json", ["anything"]):
            for status in (472, 473, 200):
                session = Session()
                original = session.confirm()
                with self.subTest(body=body, status=status):
                    transported = session.result("commit", {"status_code": status, "data": body})
                    self.assertEqual(transported["data"], {"bg_http_status": status})
                    if status == 472:
                        self.assertEqual(session.variables["bg_route"], "allow")
                        self.assertEqual(session.variables["bg_body"], original)
                        self.assertEqual(session.state["promise_failures"], {"invented-product": 1})
                    else:
                        self.assertTrue(session.state["quarantined"])
                        self.assertEqual(session.variables["bg_body"], "")
                        self.assertFalse(session.state["promise_failures"])

    def test_hook_discards_forged_body_status_and_copies_only_actual_envelope_status(self):
        session = Session()
        for envelope, expected in (({"data": {"bg_http_status": 472}}, {}),
                                   ({"status_code": 207, "data": {"bg_http_status": 472}}, {"bg_http_status": 207}),
                                   ({"status_code": None, "data": {"bg_http_status": 472}}, {"bg_http_status": None})):
            self.assertEqual(session.hook(envelope)["data"], expected)
        session.confirm()
        session.result("commit", {"data": {"bg_http_status": 472}})
        self.assertTrue(session.state["quarantined"])
        self.assertFalse(session.state["promise_failures"])

    def test_bodyless_retry_rejects_invalid_or_missing_transport_status(self):
        session = Session()
        session.confirm()
        for value in (None, "", "472.0", True, 472.0, 99, 600, "0472", " 472", "472 "):
            with self.subTest(value=value):
                result = session.kernel_result({"_http_status": value})
                self.assertTrue(result[0]["quarantined"])
                self.assertFalse(result[0]["promise_failures"])
        result = session.kernel_result({})
        self.assertTrue(result[0]["quarantined"])
        self.assertFalse(result[0]["promise_failures"])

    def test_malformed_or_positive_ok_is_not_bodyless_negative_evidence(self):
        session = Session()
        session.confirm()
        for ok in (0, 1, [], {}, " ", "FALSE", "unknown", True, "true", "True"):
            with self.subTest(ok=ok):
                result = session.kernel_result({"_http_status": 472, "ok": ok})
                self.assertTrue(result[0]["quarantined"])
                self.assertFalse(result[0]["promise_failures"])
        self.assertEqual(session.kernel_result({"_http_status": 472, "ok": None, "promise_id": None})[1], "allow")

    def test_bodyless_needs_exact_witness_operation_path_method_status_and_error(self):
        for defect in ("missing", "path", "method", "status", "quote", "operation", "error", "escape"):
            session = Session()
            session.confirm()
            case = session.profile["nonpersistent_failures"]["create_promise"][0]
            if defect == "missing":
                case.pop("api_source")
            elif defect == "path":
                case["api_source"]["pointer"] = case["api_source"]["pointer"].replace("~1ledger~0actions~1write", "~1contact~1finish")
            elif defect == "method":
                case["api_source"]["pointer"] = case["api_source"]["pointer"].replace("/post/", "/get/")
            elif defect == "status":
                case["api_source"]["pointer"] = case["api_source"]["pointer"].replace("/472/", "/207/")
            elif defect == "quote":
                case["api_source"]["quote"] = ""
            elif defect == "operation":
                session.profile["nonpersistent_failures"]["op_writeQ"] = session.profile["nonpersistent_failures"].pop("create_promise")
            elif defect == "error":
                case["error"] = "exact_rejected_code"
            else:
                case["api_source"]["pointer"] = case["api_source"]["pointer"].replace("~0actions", "~9actions")
            with self.subTest(defect=defect):
                result = session.kernel_result({"_http_status": 472})
                self.assertTrue(result[0]["quarantined"])
                self.assertFalse(result[0]["promise_failures"])
        session = Session()
        session.confirm()
        case = session.profile["nonpersistent_failures"]["create_promise"][0]
        case["api_source"]["pointer"] = "#" + case["api_source"]["pointer"]
        case["error"] = "exact_rejected_code"
        self.assertEqual(session.kernel_result({"_http_status": 472, "error": "exact_rejected_code"})[1], "allow")

    def test_successful_transport_needs_explicit_negative_ok_and_no_record_identity(self):
        session = Session()
        session.confirm()
        for ok in ("missing", "", None):
            data = {"_http_status": 207}
            if ok != "missing":
                data["ok"] = ok
            with self.subTest(ok=ok):
                self.assertTrue(session.kernel_result(data)[0]["quarantined"])
        for ok in (False, "false", "False"):
            with self.subTest(ok=ok):
                self.assertEqual(session.kernel_result({"_http_status": 207, "ok": ok})[1], "allow")
        self.assertTrue(session.kernel_result({"_http_status": 207, "ok": False, "promise_id": None})[0]["quarantined"])

    def test_contradictory_or_malformed_promise_identity_never_authorizes_replay(self):
        session = Session()
        session.confirm()
        for ok in ("", False, "false"):
            for receipt in ("recorded-Q", 0, False, [], {}, " "):
                with self.subTest(ok=ok, receipt=receipt):
                    result = session.kernel_result({"_http_status": 472, "ok": ok, "promise_id": receipt})
                    self.assertTrue(result[0]["quarantined"])
                    self.assertFalse(result[0]["promise_failures"])
                    key = session.state["inflight"]["key"]
                    self.assertEqual(result[0]["attempts"][key]["status"], "unknown_persistence")
        session.result("commit", {"status_code": 472, "data": {"accepted_wire": False, "receipt_wire": "recorded-Q"}})
        self.assertTrue(session.state["quarantined"])
        self.assertEqual(session.variables["bg_body"], "")

    def test_fresh_consent_and_successful_write_at_most_once_remain_enforced(self):
        session = Session()
        with self.assertRaises(ValueError):
            session.kernel_result({"_http_status": 472})
        original = session.confirm()
        session.result("commit", {"status_code": 200, "data": {"accepted_wire": True, "receipt_wire": "recorded-Q"}})
        self.assertIn("invented-product", session.state["recorded_promises"])
        key = next(key for key in session.state["attempts"] if key.startswith("create_promise:"))
        self.assertEqual(session.state["attempts"][key]["status"], "recorded")
        self.assertEqual(session.state["attempts"][key]["count"], 1)
        self.assertFalse(session.state["promise_failures"])
        session.code("commit")
        self.assertNotEqual(session.variables["bg_route"], "allow")
        self.assertEqual(session.variables["bg_body"], "")
        self.assertTrue(original)


if __name__ == "__main__":
    unittest.main()

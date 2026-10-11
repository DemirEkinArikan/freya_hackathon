"""A refused code step never resends a bank request, and code-step payloads stay bounded.

Hosted round 1 (PUB-11): a result code step went over the platform's 131072-byte sandbox
payload, so none of its writes happened. The prepare step's allow stayed in place and the
result node's own allow row sent the same bank request again, 30 times. The platform records
a refused step in <node>_failure_count before that node routes. Invented data; no model,
bank or platform I/O.
"""
import ast
import builtins
import json
import unittest
from unittest.mock import patch

import tools.replay_guarded as replay_guarded
from builder import guards
from builder.guards import compile_guarded_workflow
from test_goal_requirements import SealedSession
from test_guards import fixture
from test_result_type_evidence import context, run

PLATFORM_LIMIT = 131072
RUNTIME_RESERVE = 56 * 1024  # PUB-11 carried about 48 KB of runtime data when its step was refused


def code_of(workflow, name):
    return workflow["nodes"][name]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]


def refusing(workflow, name, counted=True):
    """The platform refuses node `name`'s code step: none of its writes happen."""
    refused, real = code_of(workflow, name), replay_guarded._execute
    def execute(code, namespace, remaining):
        if code != refused:
            return real(code, namespace, remaining)
        if counted:
            namespace["vars"][name + "_failure_count"] = namespace["vars"].get(name + "_failure_count", 0) + 1
    return patch.object(replay_guarded, "_execute", execute)


class RefusedStepTests(unittest.TestCase):
    def test_a_refused_result_step_quarantines_instead_of_resending(self):
        workflow = compile_guarded_workflow(**context())["workflow"]
        # Counted: the documented failure counter. Uncounted: only the consumed one-shot allow remains.
        for counted in (True, False):
            with self.subTest(counted=counted), refusing(workflow, "bg_callback_result", counted):
                report, _ = run(context(), {"ok": True})
                self.assertEqual([call["tool"] for call in report["calls"]], ["schedule_callback"])
                self.assertEqual((report["status"], report["terminal_node"]), ("terminated", "bg_quarantine"))

    def test_the_one_shot_allow_and_the_failure_counter_each_block_the_resend(self):
        workflow = compile_guarded_workflow(**context())["workflow"]
        api = workflow["nodes"]["bg_callback_api"]["pre_actions"][0]
        variables, config = {"bg_route": "allow", "bg_body": '{"a":1}'}, {"body": ""}
        exec(api["pre_process_code"], {"vars": variables, "config": config})
        self.assertEqual((variables["bg_route"], config["body"]), ("sent", '{"a":1}'))
        class ReadOnly(dict):  # a host that refused hook writes to vars must still get the raw body
            def __setitem__(self, key, value):
                raise TypeError(key)
        config = {"body": ""}
        exec(api["pre_process_code"], {"vars": ReadOnly(bg_body='{"a":1}'), "config": config})
        self.assertEqual(config["body"], '{"a":1}')
        node, stale = workflow["nodes"]["bg_callback_result"], {"bg_route": "allow", "bg_quarantined": ""}
        # The allow row a fresh reconciliation may use (next batch member or qualified retry).
        self.assertEqual(replay_guarded._route(node, stale)[0], "bg_callback_api")
        self.assertEqual(replay_guarded._route(node, dict(stale, bg_route="sent"))[0], "bg_quarantine")
        self.assertEqual(replay_guarded._route(node, dict(stale, bg_callback_result_failure_count=1))[0], "bg_quarantine")

    def test_a_refused_prepare_step_sends_nothing_and_never_follows_a_stale_route(self):
        workflow = compile_guarded_workflow(**context())["workflow"]
        with refusing(workflow, "callback"):  # initialization left bg_route done
            report, _ = run(context(), {"ok": True})
        self.assertEqual((report["calls"], report["terminal_node"]), ([], "bg_quarantine"))

    def test_every_dispatching_code_step_quarantines_on_its_own_failure_counter(self):
        workflow = compile_guarded_workflow(**fixture())["workflow"]
        dispatching = [name for name, node in workflow["nodes"].items() if node["node_type"] == "code"
                       and any(row["target_node"].endswith("_api") for row in node["transitions"])]
        self.assertGreaterEqual(len(dispatching), 12)
        for name in dispatching:
            first = workflow["nodes"][name]["transitions"][0]
            self.assertEqual(first["target_node"], "bg_quarantine")
            self.assertIn({"variable": name + "_failure_count", "operator": "greater_than", "operand": 0},
                          first["condition"]["conditions"])
            self.assertEqual(sum(row["target_node"] == "bg_quarantine" for row in workflow["nodes"][name]["transitions"]), 1)

    def test_the_result_hook_drops_response_headers(self):
        workflow = compile_guarded_workflow(**fixture())["workflow"]
        hook = workflow["nodes"]["bg_callback_api"]["pre_actions"][0]["post_process_code"]
        result = {"status_code": 200, "header": {"Report-To": "x" * 300}, "data": {"ok": True}}
        exec(hook, {"result": result})
        self.assertNotIn("header", result)
        self.assertEqual(result["data"]["bg_http_status"], 200)


class PayloadTests(unittest.TestCase):
    def test_code_and_variables_stay_under_budget_over_repeated_attempts(self):
        source = fixture()
        workflow = compile_guarded_workflow(**source)["workflow"]
        largest = max(len(code_of(workflow, name).encode()) for name, node in workflow["nodes"].items()
                      if node["node_type"] == "code")
        session, sizes = SealedSession(source, workflow), []
        for attempt in range(25):
            session.action("verify_identity", field="postal_code", answer="answer %d" % attempt)
            self.assertEqual(session.variables["bg_route"], "allow")
            session.result("verify_identity", {"ok": False, "error": "answer is not in the expected format"})
            sizes.append(len(json.dumps(session.variables).encode()))
        self.assertLessEqual(sizes[-1] - sizes[0], 64)  # no variable grows with the loop
        self.assertLessEqual(largest + sizes[-1], PLATFORM_LIMIT - RUNTIME_RESERVE)

    def test_each_step_keeps_only_reachable_kernel_statements_unchanged(self):
        kernel = {ast.dump(statement) for statement in ast.parse(guards._KERNEL).body}
        for roots in ({"guard_new"}, {"guard_prepare", "guard_refusal"}, {"guard_confirm"}, {"guard_result"}):
            pruned = guards._kernel_for(guards._KERNEL, frozenset(roots))
            self.assertLess(len(pruned), len(guards._KERNEL))
            self.assertLessEqual({ast.dump(statement) for statement in ast.parse(pruned).body}, kernel)
        workflow = compile_guarded_workflow(**fixture())["workflow"]
        for name, node in workflow["nodes"].items():
            if node["node_type"] != "code":
                continue
            tree = ast.parse(code_of(workflow, name))
            bound = set(dir(builtins)) | {"vars"}
            for item in ast.walk(tree):
                bound |= ({item.name} if isinstance(item, ast.FunctionDef) else {item.arg} if isinstance(item, ast.arg)
                          else {item.id} if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store)
                          else {item.asname or item.name} if isinstance(item, ast.alias) else set())
            loaded = {item.id for item in ast.walk(tree) if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)}
            self.assertEqual(loaded - bound, set(), name)


if __name__ == "__main__":
    unittest.main()

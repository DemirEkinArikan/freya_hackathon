"""Invented source/input names exercised through sealed compiler and init code."""
import ast
import copy
import json
import unittest

from builder.action_bindings import compile_action_bindings
from builder.build import make_policy_prompt
from builder.contract import compile_policy_contract
from builder.execution_requirements import compile_execution_requirements, ExecutionRequirementsError
from builder.guards import compile_guarded_workflow, GuardCompileError
from builder.policy import compile_execution_policy, execution_rule_guidance
from builder.safety import digest
from builder.validation import validate_workflow
from test_call_input_formats import authored
from test_execution_requirements import packet, plan
from test_goal_scoped_resolution import CompiledCall


def input_packet(names=None):
    names = names if names is not None else ["recipient_style", "recipient_label"]
    policy, sources, api = packet(["close_call"], narrow=True)
    api["paths"]["/close_call"]["post"]["operationId"] = "conclude_dialogue_v8"
    policy["allowed_actions"] = ["conclude_dialogue_v8"]
    quote = "Every call supplies recipient_style and recipient_label."
    sources[0]["text"] += "\n" + quote
    sources[0]["sha256"] = digest(sources[0]["text"])
    policy["sources"] = [{k: v for k, v in sources[0].items() if k != "text"}]
    for kind, value, witness in (("call_variables", names, quote),
                                ("tool_aliases", {"close_call": "conclude_dialogue_v8"}, sources[0]["text"])):
        policy["rules"].append({"id": kind, "kind": kind, "value": copy.deepcopy(value),
                                "source": {"id": sources[0]["id"], "quote": witness}})
    return policy, sources, api


def compilation(policy, sources, api, candidate=None):
    candidate = copy.deepcopy(candidate) if candidate is not None else plan(["close_call"])
    candidate["nodes"]["talk"]["task_message"] = candidate["nodes"]["talk"].get(
        "task_message", "") + " Address {{recipient_style}} {{recipient_label}}."
    bindings = compile_action_bindings(policy, api)
    context = {"plan": candidate, "contract": compile_policy_contract(policy, sources, api),
               "execution_policy": compile_execution_policy(policy, bindings), "bindings": bindings}
    return context, compile_guarded_workflow(openapi=api, **context)


def initialize(workflow, values):
    variables = {"customer_id": "invented-subject", "now": "2032-01-12T11:00", **values}
    code = workflow["nodes"]["bg_init"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
    exec(code, {"vars": variables})
    return variables


class RequiredCallInputTests(unittest.TestCase):
    def test_source_bound_names_render_without_defaults_or_values_in_facts(self):
        policy, sources, api = input_packet()
        descriptor = compile_execution_requirements(policy, sources, api)
        context, bundle = compilation(policy, sources, api)
        self.assertEqual(validate_workflow(bundle["workflow"], api, policy, guard_context=context), [])
        self.assertEqual(descriptor["policy_sha256"], digest(policy))
        self.assertEqual(context["execution_policy"]["profile"]["call_variables"], ["recipient_style", "recipient_label"])
        variables = initialize(bundle["workflow"], {"recipient_style": "Mx", "recipient_label": "Invented Recipient"})
        self.assertEqual(variables["bg_route"], "done")
        self.assertEqual(variables["recipient_label"], "Invented Recipient")
        private_state = json.loads(variables["bg_state"])
        self.assertEqual(private_state.pop("call_inputs"), {"recipient_style": "Mx", "recipient_label": "Invented Recipient"})
        for name in ("recipient_style", "recipient_label", "Invented Recipient"):
            self.assertNotIn(name, json.dumps(private_state))
            self.assertNotIn(name, variables["bg_facts"])
        self.assertNotIn("test_variables", bundle["workflow"])
        self.assertNotIn("context_variables", bundle["workflow"])

    def test_absent_none_and_exact_empty_inputs_block_before_dialogue(self):
        policy, sources, api = input_packet()
        _, bundle = compilation(policy, sources, api)
        for values in ({}, {"recipient_style": "Mx"}, {"recipient_style": "Mx", "recipient_label": None},
                       {"recipient_style": "Mx", "recipient_label": ""}):
            with self.subTest(values=values):
                variables = initialize(bundle["workflow"], values)
                self.assertEqual(variables["bg_route"], "blocked")
                self.assertEqual(json.loads(variables["bg_state"]), {"quarantined": True})
                self.assertEqual(variables["bg_quarantined"], "yes")
                self.assertEqual(variables["bg_body"], "")
                initial = bundle["workflow"]["nodes"]["bg_init"]
                self.assertEqual(initial["transitions"][0]["condition"]["conditions"][0]["operand"], "done")
                self.assertEqual(initial["transitions"][-1]["condition"], {"operator": "always"})
                self.assertEqual(initial["passthrough_default_transition"], "bg_quarantine")
        for value in (0, False):
            with self.subTest(value=value):
                variables = initialize(bundle["workflow"], {"recipient_style": value, "recipient_label": value})
                self.assertEqual(variables["bg_route"], "done")
        declaration = next(rule for rule in policy["rules"] if rule["kind"] == "call_variables")
        declaration["value"] = {"recipient_style": "Style of supplied greeting", "recipient_label": "Supplied party label"}
        _, mapped = compilation(policy, sources, api)
        self.assertEqual(initialize(mapped["workflow"], {})["bg_route"], "blocked")
        mapped_values = initialize(mapped["workflow"], {"recipient_style": 0, "recipient_label": False})
        self.assertEqual(mapped_values["bg_route"], "done")
        self.assertEqual(mapped_values["recipient_style"], 0)
        self.assertIs(mapped_values["recipient_label"], False)

    def test_malformed_and_reserved_input_declarations_fail_before_freeze(self):
        cases = [None, {}, "recipient_label", ["recipient_label", "recipient_label"], [1],
                 [{}], ["bad-name"], ["bg_facts"], ["BANK_TOKEN"], ["service_api_key"], ["FREYA_API_KEY"], ["customer_password"],
                 ["close_call_response"], ["conclude_dialogue_v8_result"]]
        for names in cases:
            policy, sources, api = input_packet(names)
            if isinstance(names, list) and all(isinstance(name, str) for name in names):
                extra = " " + ", ".join(names)
                next(rule for rule in policy["rules"] if rule["kind"] == "call_variables")["source"]["quote"] += extra
                sources[0]["text"] += extra
                sources[0]["sha256"] = digest(sources[0]["text"])
                policy["sources"][0]["sha256"] = sources[0]["sha256"]
            if names is None:
                next(r for r in policy["rules"] if r["kind"] == "call_variables")["value"] = None
            with self.subTest(names=names), self.assertRaises(ExecutionRequirementsError):
                compile_execution_requirements(policy, sources, api)

    def test_source_mentions_are_boundary_checked_and_only_active_quotes_count(self):
        policy, sources, api = input_packet(["recipient"])
        with self.assertRaisesRegex(ExecutionRequirementsError, "occur literally"):
            compile_execution_requirements(policy, sources, api)
        policy, sources, api = input_packet()
        quote = "An amendment replaces supplied names with successor_label."
        sources.append({"id": "overlay", "file": "invented-overlay.md", "text": quote, "sha256": digest(quote)})
        policy["sources"] = [{k: v for k, v in s.items() if k != "text"} for s in sources]
        policy["rules"].append({"id": "overlay-inputs", "kind": "call_variables", "value": ["successor_label"],
                                "overrides": ["call_variables"], "source": {"id": "overlay", "quote": quote}})
        descriptor = compile_execution_requirements(policy, sources, api)
        self.assertTrue(descriptor["sha256"])
        self.assertEqual(compile_execution_policy(policy)["profile"]["call_variables"], ["successor_label"])
        _, bundle = compilation(policy, sources, api)
        errors = validate_workflow(bundle["workflow"], api, policy)
        self.assertTrue(any("unresolved required binding recipient_label" in error for error in errors))
        self.assertTrue(any("unresolved required binding recipient_style" in error for error in errors))

    def test_frozen_input_profile_cannot_be_omitted_or_extended(self):
        policy, sources, api = input_packet()
        context, _ = compilation(policy, sources, api)
        for value in (None, ["recipient_style", "recipient_label", "extra_label"]):
            changed = copy.deepcopy(context)
            if value is None:
                del changed["execution_policy"]["profile"]["call_variables"]
            else:
                changed["execution_policy"]["profile"]["call_variables"] = value
            changed["execution_policy"]["sha256"] = digest({k: v for k, v in changed["execution_policy"].items() if k != "sha256"})
            with self.subTest(value=value), self.assertRaises(GuardCompileError):
                compile_guarded_workflow(openapi=api, **changed)

    def test_authored_extraction_cannot_overwrite_call_inputs(self):
        policy, sources, api = input_packet()
        candidate = plan(["close_call"])
        candidate["nodes"]["talk"]["extraction_schema"] = [{"name": "recipient_label", "type": "string", "min_confidence": 1}]
        with self.assertRaisesRegex(GuardCompileError, "extraction cannot write protected or engine variables"):
            compilation(policy, sources, api, candidate)

    def test_call_input_declaration_does_not_authorize_private_read_or_undeclared_names(self):
        policy, sources, api = input_packet(["customer_id", "now", "recipient_style", "recipient_label"])
        source = policy["rules"][-2]["source"]
        source["quote"] = sources[0]["text"] + " customer_id now"
        sources[0]["text"] = source["quote"]
        sources[0]["sha256"] = digest(sources[0]["text"])
        policy["sources"][0]["sha256"] = sources[0]["sha256"]
        candidate = plan(["close_call"])
        candidate["nodes"]["talk"]["task_message"] = "{{customer_id}} {{unexpected_name}}"
        with self.assertRaisesRegex(GuardCompileError, "protected engine values"):
            compilation(policy, sources, api, candidate)
        policy, sources, api = input_packet()
        _, bundle = compilation(policy, sources, api)
        bundle["workflow"]["nodes"]["talk"]["task_message"] += " {{unexpected_name}}"
        self.assertTrue(any("unresolved required binding unexpected_name" in error
                            for error in validate_workflow(bundle["workflow"], api, policy)))
        self.assertTrue(any("unresolved required binding" in error for error in validate_workflow(
            bundle["workflow"], api, {"call_variables": ["unexpected_name"]})))


class RequiredInputGuidanceTests(unittest.TestCase):
    """The rule shapes the policy model reads state every sealed initial-input refusal."""

    FORMATS = ("call_input_formats", "malformed/date-only/offset-less values quarantine")
    # Each refusal in the sealed init's input checks -> (rule shape, the text that states it).
    STATED = {
        "required call input is absent": ("call_variables", "absent, null or exactly empty text"),
        "missing engine customer id": ("call_variables", "customer_id is not nonempty text"),
        "expected an ISO date": ("call_variables", "now does not start with a valid YYYY-MM-DD date"),
        "protected call-input snapshot is malformed": ("call_variables", "re-checks the sealed snapshot"),
        "protected call-input snapshot has an absent value": ("call_variables", "re-checks the sealed snapshot"),
        "protected call-input format is malformed": FORMATS,
        "protected call input requires a date, time and explicit offset": FORMATS,
        "protected call-input offset is malformed": FORMATS,
        "missing offset": FORMATS,
        "protected call-input datetime is malformed": FORMATS,
        "protected product-resolution selector is missing or unknown":
            ("resolve_all_products", "Missing, non-string or unknown initial selector values quarantine"),
    }

    def test_every_sealed_initial_input_refusal_is_stated_in_the_rule_shapes(self):
        call = CompiledCall(authored(names=["supplied_label"]), {"supplied_label": "Invented Label"})
        code = call.workflow["nodes"]["bg_init"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]
        module = ast.parse(code)
        functions = {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}
        body = [node for node in module.body if isinstance(node, ast.Try)][-1].body
        # The input checks run before the init's 'done' route; follow every kernel function they call.
        pending = body[:next(index for index, node in enumerate(body) if isinstance(node, ast.Assign)
                             and ast.unparse(node.targets[0]) == "(route, body, readback)")]
        refusals, seen = set(), set()
        while pending:
            for node in ast.walk(pending.pop()):
                if isinstance(node, ast.Raise):
                    message = node.exc.args[0] if isinstance(node.exc, ast.Call) and node.exc.args else None
                    refusals.add(message.value if isinstance(message, ast.Constant) else ast.unparse(node))
                elif isinstance(node, ast.Call) and getattr(node.func, "id", None) in functions.keys() - seen:
                    seen.add(node.func.id)
                    pending.append(functions[node.func.id])
        self.assertEqual(refusals, set(self.STATED))
        shapes = execution_rule_guidance()
        payload = json.loads(make_policy_prompt([], call.context["openapi"]).split("AUTHORIZED INPUTS:\n", 1)[1])
        self.assertEqual(payload["execution_interface"]["rule_shapes"], shapes)
        for message, (kind, text) in self.STATED.items():
            with self.subTest(message=message):
                self.assertIn(text, shapes[kind])

    def test_stated_presence_and_engine_checks_match_the_sealed_init_and_later_steps(self):
        context = authored(names=["supplied_label", "supplied_title"])
        valid = {"supplied_label": "Invented Label", "supplied_title": "Mx"}
        workflow = CompiledCall(context, valid).workflow
        self.assertEqual(workflow["initial_node"], "bg_init")
        self.assertEqual((workflow["nodes"]["bg_quarantine"]["node_type"], workflow["nodes"]["bg_quarantine"]["on_enter"]),
                         ("end_call", {"type": "silent"}))
        for values in ({"supplied_title": "Mx"}, {**valid, "supplied_label": None}, {**valid, "supplied_label": ""},
                       {**valid, "customer_id": ""}, {**valid, "customer_id": None}, {**valid, "now": None},
                       {**valid, "now": "2031-13-02"}, {**valid, "now": "2031-05-02T25:00"}):
            with self.subTest(refused=values):
                call = CompiledCall(context, values)
                self.assertEqual(call.target("bg_init"), "bg_quarantine")
                self.assertFalse(call.variables["bg_body"])
        for values in (valid, {**valid, "supplied_label": "  "}, {**valid, "supplied_label": 0, "supplied_title": False},
                       {**valid, "now": "2031-05-02T11:30"}):
            with self.subTest(passes=values):
                self.assertEqual(CompiledCall(context, values).variables["bg_route"], "done")
        for node in ("identity", "account", "callback", "close"):
            for snapshot in ({**valid, "supplied_label": ""}, {**valid, "supplied_label": None},
                             {"supplied_title": "Mx"}, {**valid, "unexpected_label": "x"}):
                with self.subTest(node=node, snapshot=snapshot):
                    call = CompiledCall(context, {**valid, "answer_field": "postal_code", "answer_text": "provided-answer"})
                    original = copy.deepcopy(call.variables)
                    call.code(node)
                    self.assertNotEqual(call.target(node), "bg_quarantine")
                    call.variables = original
                    state = call.state
                    state["call_inputs"] = snapshot
                    call.variables["bg_state"] = json.dumps(state)
                    call.code(node)
                    self.assertEqual(call.target(node), "bg_quarantine")
                    self.assertFalse(call.variables["bg_body"])


if __name__ == "__main__":
    unittest.main()

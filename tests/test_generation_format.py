"""Only healthy completed serialization failures enter bounded model repair."""
import json
import subprocess
import unittest

from builder.build import execute, PROJECT
from builder.runtime import CodexGenerator, Deadline, GenerationError, GenerationFormatError, Trace
import test_builder
from test_builder import fixture, plan
from test_runtime import completed_stream


class GenerationFormatTests(unittest.TestCase):
    setUp = test_builder.BuilderTests.setUp
    tearDown = test_builder.BuilderTests.tearDown
    args = test_builder.BuilderTests.args
    generator = test_builder.BuilderTests.generator

    def parse(self, text, *, returncode=0, events=None, plan_hash=None):
        events = events or [
            {"type": "thread.started", "thread_id": "synthetic-thread"}, {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": text}},
            {"type": "turn.completed"}]
        stream = "\n".join(json.dumps(event) for event in events)
        def run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, returncode, stream, "")
        generator = CodexGenerator(self.directory, Trace(self.directory / "runtime-trace.jsonl"), Deadline(), run=run)
        generator._checked = generator._isolation_checked = True
        generator._catalog = ("{}", {"fixture": "catalog"})
        return generator.generate_plan("frozen approved contract", contract_sha256=plan_hash) if plan_hash \
            else generator.generate("approved brief and API")

    def test_healthy_policy_format_failure_has_fixed_diagnostic(self):
        cases = [
            ("invalid PRIVATE_INVALID_LITERAL", "outer_json"),
            (json.dumps({"policy_json": "{broken PRIVATE_INVALID_LITERAL", "workflow_json": "{}"}), "inner_json"),
            (json.dumps({"policy_json": "[]", "workflow_json": "{}"}), "inner_shape"),
            (json.dumps({"policy_json": {}, "workflow_json": "{}"}), "wire_shape"),
            (json.dumps({"policy_json": "{}", "workflow_json": "{}", "extra": True}), "wire_shape"),
            ('{"policy_json":"{}","policy_json":"{}","workflow_json":"{}"}', "outer_json"),
            (json.dumps({"policy_json": '{"bad":NaN}', "workflow_json": "{}"}), "inner_json")]
        for text, code in cases:
            with self.subTest(code=code, text=text[:25]), self.assertRaises(GenerationFormatError) as caught:
                self.parse(text)
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("PRIVATE_INVALID_LITERAL", str(caught.exception))

    def test_healthy_plan_format_failure_cannot_change_frozen_hash(self):
        for text, code in [
            (json.dumps({"plan_json": "{broken", "contract_sha256": "a" * 64}), "inner_json"),
            (json.dumps({"plan_json": "[]", "contract_sha256": "a" * 64}), "inner_shape"),
            (json.dumps({"plan_json": {}, "contract_sha256": "a" * 64}), "wire_shape")]:
            with self.subTest(code=code), self.assertRaises(GenerationFormatError) as caught:
                self.parse(text, plan_hash="a" * 64)
            self.assertEqual(caught.exception.code, code)
        for wire in ({"plan_json": "{}", "contract_sha256": "b" * 64},
                     {"plan_json": "{}", "contract_sha256": "b" * 64, "policy_json": "{}"}):
            with self.subTest(wire=wire), self.assertRaises(GenerationError) as caught:
                self.parse(json.dumps(wire), plan_hash="a" * 64)
            self.assertNotIsInstance(caught.exception, GenerationFormatError)

    def test_unhealthy_or_tool_events_never_become_format_repairs(self):
        base = [json.loads(line) for line in completed_stream({"policy_json": "{}", "workflow_json": "{}"}).splitlines()]
        base[2]["item"]["text"] = "invalid serialized output"
        cases = [
            (base, 1), (base[:-1], 0),
            (base[:2] + [{"type": "turn.failed", "error": {"message": "provider failed"}}], 0),
            (base[:2] + [{"type": "item.completed", "item": {"type": "command_execution"}}] + base[2:], 0),
            (base[:2] + [{"type": "turn.interrupted"}] + base[2:], 0),
            (base[:2] + [{"type": "item.completed", "item": {"type": "error", "message": "quota failed"}}] + base[2:], 0)]
        for events, code in cases:
            with self.subTest(events=events[-1], exit=code), self.assertRaises(GenerationError) as caught:
                self.parse("unused", events=events, returncode=code)
            self.assertNotIsInstance(caught.exception, GenerationFormatError)

    def test_policy_format_regeneration_uses_fixed_guidance_and_approved_inputs(self):
        generator = self.generator()
        generator.generate.return_value = fixture()
        generator.generate.side_effect = [GenerationFormatError("inner_json"), fixture()]
        result = execute(self.args("--generate-only"), generator=generator)
        self.assertEqual(result["policy_repairs"], 1)
        context = json.loads(generator.generate.call_args_list[1].args[0].split("\n\nAUTHORIZED INPUTS:\n", 1)[1])
        self.assertEqual(set(context["repair"]), {"format_error", "serialization_guidance"})
        self.assertEqual(context["repair"]["format_error"], "inner_json")
        self.assertEqual(len(context["sources"]), 1)
        run = PROJECT / result["run_directory"]
        self.assertEqual(json.loads((run / "policy.format-rejected-1.json").read_text()), {"format_error": "inner_json"})
        self.assertEqual(json.loads((run / "policy.json").read_text()), fixture()["policy"])

    def test_plan_format_regeneration_keeps_exact_frozen_contract(self):
        generator = self.generator()
        generator.generate.return_value = fixture()
        original = generator.generate_plan.side_effect
        def generate(prompt, **kwargs):
            if generator.generate_plan.call_count == 1:
                raise GenerationFormatError("inner_json")
            return original(prompt, **kwargs)
        generator.generate_plan.side_effect = generate
        result = execute(self.args("--generate-only"), generator=generator)
        self.assertEqual((result["policy_repairs"], result["workflow_repairs"], result["selected_attempt"]), (0, 1, 2))
        hashes = [call.kwargs["contract_sha256"] for call in generator.generate_plan.call_args_list]
        self.assertEqual(hashes, [result["policy_contract_sha256"]] * 2)
        run = PROJECT / result["run_directory"]
        first = run / "attempts/plan-001"
        self.assertEqual(json.loads((first / "attempt.json").read_text())["status"], "format_rejected")
        self.assertEqual(json.loads((first / "generation-format.json").read_text()), {"format_error": "inner_json"})
        self.assertFalse((first / "proposal.json").exists())
        self.assertFalse((first / "plan.json").exists())
        self.assertEqual(json.loads((run / "policy.json").read_text()), fixture()["policy"])

    def test_format_repairs_share_global_two_repair_limit(self):
        generator = self.generator()
        generator.generate.return_value = fixture()
        generator.generate.side_effect = [GenerationFormatError("outer_json"), fixture()]
        generator.generate_plan.side_effect = GenerationFormatError("inner_json")
        with self.assertRaises(GenerationFormatError):
            execute(self.args("--generate-only"), generator=generator)
        self.assertEqual((generator.generate.call_count, generator.generate_plan.call_count), (2, 2))
        run = next((self.directory / "out").iterdir())
        self.assertEqual(json.loads((run / "manifest.json").read_text())["status"], "failed")
        self.assertFalse((run / "workflow.json").exists())

    def test_ordinary_generation_errors_remain_fatal_without_repair(self):
        for stage in ("policy", "workflow"):
            with self.subTest(stage=stage):
                generator = self.generator()
                generator.generate.return_value = fixture()
                if stage == "policy":
                    generator.generate.side_effect = GenerationError("tool or infrastructure failure")
                else:
                    generator.generate_plan.side_effect = GenerationError("frozen hash mismatch")
                with self.assertRaises(GenerationError) as caught:
                    execute(self.args("--generate-only"), generator=generator)
                self.assertNotIsInstance(caught.exception, GenerationFormatError)
                self.assertEqual(generator.generate.call_count, 1)
                self.assertEqual(generator.generate_plan.call_count, int(stage == "workflow"))


if __name__ == "__main__":
    unittest.main()

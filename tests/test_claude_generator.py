"""Claude generator with a fake Anthropic client; no network, no SDK required."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from builder import build, runtime
from builder.runtime import (GENERATION_SCHEMA, ClaudeGenerator, Deadline, GenerationError, GenerationFormatError,
                             GenerationStalled, GenerationTimeout, Trace, generation_environment)
import test_builder
from test_builder import fixture, plan

PROJECT = Path(__file__).resolve().parents[1]
POLICY_WIRE = {"policy_json": json.dumps(fixture()["policy"]), "workflow_json": json.dumps(fixture()["workflow"])}


def message(text, stop_reason="end_turn", model="claude-opus-5-5", **extra):
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
                           model=model, stop_reason=stop_reason, id="msg_synthetic", stop_details=None,
                           usage=SimpleNamespace(to_dict=lambda: {"input_tokens": 11, "output_tokens": 22}), **extra)


def delta(kind, **fields):
    return SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type=kind, **fields))


class FakeStream:
    """Yields events, then optionally stays silent or keeps streaming until closed."""

    def __init__(self, final, events=None, silent=False, endless=False):
        self.final, self.silent, self.endless, self.closed = final, silent, endless, threading.Event()
        self.events = events if events is not None else [SimpleNamespace(type="message_start"),
                                                         delta("thinking_delta", thinking="plan"),
                                                         delta("text_delta", text="{}")]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed.set()
        return False

    def __iter__(self):
        yield from self.events
        while self.endless and not self.closed.is_set():
            time.sleep(0.02)
            yield delta("text_delta", text="x")
        if self.silent:
            self.closed.wait(5)

    def get_final_message(self):
        return self.final

    def close(self):
        self.closed.set()


class FakeAPIError(Exception):
    def __init__(self, status, text="synthetic API error", body=None, headers=None):
        super().__init__(text)
        self.status_code, self.body, self.response = status, body or {}, SimpleNamespace(headers=headers or {})


class FakeClient:
    """messages.stream(**request) returns the next outcome: a stream, a callable of the request, or an error."""

    def __init__(self, *outcomes):
        self.outcomes, self.requests = list(outcomes), []
        self.messages = self

    def stream(self, **request):
        self.requests.append(request)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome(request) if callable(outcome) else outcome


class ClaudeGeneratorTests(unittest.TestCase):
    def setUp(self):
        (PROJECT / ".test-tmp").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=PROJECT / ".test-tmp")
        self.directory = Path(self.tmp.name)
        self.delays = []

    def tearDown(self):
        self.tmp.cleanup()

    def generator(self, *outcomes, **kwargs):
        client = FakeClient(*outcomes)
        generator = ClaudeGenerator(self.directory, Trace(self.directory / "trace.jsonl"), Deadline(),
                                    client=client, sleep=self.delays.append, **kwargs)
        return generator, client

    def events(self):
        return [json.loads(line) for line in (self.directory / "trace.jsonl").read_text().splitlines()]

    def test_schema_bound_streamed_request_returns_parsed_policy_and_workflow(self):
        generator, client = self.generator(FakeStream(message(json.dumps(POLICY_WIRE))))
        self.assertEqual(generator.generate("PROMPT-CANARY-7731", max_seconds=900, reserve_seconds=750), fixture())
        request = client.requests[0]
        self.assertEqual((request["model"], request["max_tokens"]), ("claude-opus-5-5", runtime.CLAUDE_MAX_TOKENS))
        self.assertEqual(request["messages"], [{"role": "user", "content": "PROMPT-CANARY-7731"}])
        self.assertEqual(request["extra_body"]["output_config"],
                         {"effort": "max", "format": {"type": "json_schema", "schema": GENERATION_SCHEMA}})
        self.assertEqual(request["extra_body"]["thinking"], {"type": "adaptive", "display": "summarized"})
        self.assertFalse({"tools", "tool_choice", "system", "mcp_servers"} & {*request, *request["extra_body"]})
        events = self.events()
        names = [event["event"] for event in events]
        self.assertLess(names.index("generation_isolation_audit"), names.index("model_call"))
        self.assertLess(names.index("generation_model_catalog"), names.index("model_call"))
        audit = events[names.index("generation_isolation_audit")]
        self.assertEqual((audit["status"], audit["isolation_mode"], audit["request_tools"]), ("pass", "api_no_tools", []))
        call = events[names.index("model_call")]
        self.assertEqual((call["provider"], call["model"], call["reasoning_effort"], call["prompt_characters"]),
                         ("anthropic", "claude-opus-5-5", "max", 18))
        result = events[names.index("generation_result")]
        self.assertEqual((result["served_model"], result["stop_reason"], result["usage"]["output_tokens"]),
                         ("claude-opus-5-5", "end_turn", 22))
        self.assertEqual(result["thinking_characters"], 4)
        self.assertNotIn("PROMPT-CANARY", (self.directory / "trace.jsonl").read_text())

    def test_plan_schema_pins_contract_and_effort_override_is_sent(self):
        sha = "a" * 64
        wire = {"plan_json": json.dumps({"nodes": []}), "contract_sha256": sha}
        generator, client = self.generator(FakeStream(message(json.dumps(wire))))
        self.assertEqual(generator.generate_plan("plan prompt", contract_sha256=sha),
                         {"plan": {"nodes": []}, "contract_sha256": sha})
        schema = client.requests[0]["extra_body"]["output_config"]["format"]["schema"]
        self.assertEqual(schema["properties"]["contract_sha256"]["enum"], [sha])
        generator, client = self.generator(FakeStream(message(json.dumps(POLICY_WIRE))))
        generator.generate("brief", max_seconds=450, reserve_seconds=300, reasoning_effort="medium")
        self.assertEqual(client.requests[0]["extra_body"]["output_config"]["effort"], "medium")

    def test_invalid_serialization_is_a_format_error(self):
        for text, code in (("not json", "outer_json"), (json.dumps({"policy_json": "{", "workflow_json": "{}"}), "inner_json"),
                           (json.dumps({"patch": 1}), "wire_shape")):
            generator, _ = self.generator(FakeStream(message(text)))
            with self.assertRaises(GenerationFormatError) as caught:
                generator.generate("brief", max_seconds=900, reserve_seconds=300)
            self.assertEqual(caught.exception.code, code)
        generator, _ = self.generator(FakeStream(message('{"patch_json": "{\\"operations', stop_reason="max_tokens")))
        with self.assertRaises(GenerationFormatError) as caught:
            generator.generate_policy_patch("patch", max_seconds=450, reserve_seconds=300)
        self.assertEqual(caught.exception.code, "outer_json")

    def test_refusal_is_a_generation_error(self):
        refused = message("", stop_reason="refusal")
        refused.stop_details = SimpleNamespace(category="cyber")
        generator, _ = self.generator(FakeStream(refused))
        with self.assertRaisesRegex(GenerationError, "refusal category cyber"):
            generator.generate("brief", max_seconds=900, reserve_seconds=300)

    @patch.multiple(runtime, CLAUDE_POLL_SECONDS=0.01, CLAUDE_STARTUP_STALL_SECONDS=0.3, CLAUDE_STALL_SECONDS=0.3)
    def test_silent_stream_is_stalled_and_closed(self):
        stream = FakeStream(message("{}"), silent=True)
        generator, _ = self.generator(stream)
        with self.assertRaisesRegex(GenerationStalled, "no stream event"):
            generator.generate("brief", max_seconds=900, reserve_seconds=300)
        self.assertTrue(stream.closed.is_set())
        stall = next(e for e in self.events() if e["event"] == "stall_detected")
        self.assertEqual((stall["phase"], stall["window_seconds"]), ("after_first_event", 0.3))
        generator, _ = self.generator(FakeStream(message("{}"), events=[], silent=True))
        with self.assertRaises(GenerationStalled):
            generator.generate("brief", max_seconds=900, reserve_seconds=300)
        self.assertEqual([e["phase"] for e in self.events() if e["event"] == "stall_detected"][-1], "before_first_event")

    @patch.multiple(runtime, CLAUDE_POLL_SECONDS=0.01)
    def test_allocation_bounds_a_streaming_call(self):
        stream = FakeStream(message("{}"), endless=True)
        generator, _ = self.generator(stream)
        started = time.monotonic()
        with self.assertRaises(GenerationTimeout) as caught:
            generator.generate("brief", max_seconds=0.3, reserve_seconds=300)
        self.assertNotIsInstance(caught.exception, GenerationStalled)
        self.assertLess(time.monotonic() - started, 2)
        self.assertTrue(stream.closed.is_set())
        self.assertTrue(any(e["event"] == "generation_timeout" for e in self.events()))

    def test_overload_is_retried_inside_the_budget(self):
        generator, client = self.generator(FakeAPIError(529, body={"type": "error", "error": {"type": "overloaded_error"}}),
                                           FakeStream(message(json.dumps(POLICY_WIRE))))
        self.assertEqual(generator.generate("brief", max_seconds=900, reserve_seconds=300), fixture())
        self.assertEqual(self.delays, [2.0])
        retry = next(e for e in self.events() if e["event"] == "generation_retry")
        self.assertEqual((retry["error_kind"], retry["status_code"], retry["model"]), ("overloaded", 529, "claude-opus-5-5"))
        self.assertEqual([r["model"] for r in client.requests], ["claude-opus-5-5"] * 2)

    def test_mid_stream_overload_and_rate_limit_retry_after_are_transient(self):
        overloaded = FakeAPIError(200, body={"type": "error", "error": {"type": "overloaded_error"}})
        limited = FakeAPIError(429, headers={"retry-after": "7"})
        generator, _ = self.generator(overloaded, limited, FakeStream(message(json.dumps(POLICY_WIRE))))
        generator.generate("brief", max_seconds=900, reserve_seconds=300)
        self.assertEqual(self.delays, [2.0, 7.0])

    def test_sonnet_fallback_only_when_opus_is_unavailable(self):
        busy = FakeAPIError(529)
        generator, client = self.generator(busy, busy, busy, FakeStream(message(json.dumps(POLICY_WIRE), model="claude-sonnet-5-5")))
        generator.generate("brief", max_seconds=900, reserve_seconds=300)
        self.assertEqual([r["model"] for r in client.requests], ["claude-opus-5-5"] * 3 + ["claude-sonnet-5-5"])
        self.assertEqual(generator.active_model, "claude-opus-5-5")  # Overload is per call.
        fallback = next(e for e in self.events() if e["event"] == "model_fallback")
        self.assertEqual((fallback["from_model"], fallback["to_model"], fallback["reason"]),
                         ("claude-opus-5-5", "claude-sonnet-5-5", "overloaded"))
        generator, client = self.generator(FakeAPIError(404), FakeStream(message(json.dumps(POLICY_WIRE))))
        generator.generate("brief", max_seconds=900, reserve_seconds=300)
        generator.generate("brief", max_seconds=900, reserve_seconds=300)
        self.assertEqual([r["model"] for r in client.requests], ["claude-opus-5-5"] + ["claude-sonnet-5-5"] * 2)

    def test_non_transient_errors_stop_and_refusal_fallback_beta_degrades(self):
        generator, client = self.generator(FakeAPIError(400, "fallbacks: unsupported beta"),
                                           FakeStream(message(json.dumps(POLICY_WIRE))))
        generator.generate("brief", max_seconds=900, reserve_seconds=300)
        self.assertEqual(client.requests[0]["extra_body"]["fallbacks"], "default")
        self.assertNotIn("fallbacks", client.requests[1]["extra_body"])
        self.assertEqual(client.requests[1]["extra_headers"], {})
        self.assertIn("refusal_fallback_disabled", [e["event"] for e in self.events()])
        generator, _ = self.generator(FakeAPIError(401, "invalid x-api-key"))
        with self.assertRaisesRegex(GenerationError, "AuthenticationError|status 401"):
            generator.generate("brief", max_seconds=900, reserve_seconds=300)
        self.assertEqual(self.delays, [])

    def test_key_reaches_only_the_claude_environment_and_never_the_trace(self):
        key = "kZ9-synthetic-credential-value-0001"
        source = {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": key, "OPENAI_API_KEY": "other"}
        self.assertEqual(generation_environment(source), {"PATH": "/usr/bin"})
        self.assertEqual(generation_environment(source, provider="claude"), {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": key})
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": key}):
            generator, _ = self.generator(FakeAPIError(400, f"rejected credential {key}"))
            with self.assertRaises(GenerationError):
                generator.generate("brief", max_seconds=900, reserve_seconds=300)
        text = (self.directory / "trace.jsonl").read_text()
        self.assertIn("generation_api_error", text)
        self.assertNotIn(key, text)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
            generator = ClaudeGenerator(self.directory, Trace(self.directory / "other.jsonl"), Deadline())
            with self.assertRaisesRegex(GenerationError, "ANTHROPIC_API_KEY is required"):
                generator.generate("brief", max_seconds=900, reserve_seconds=300)


class ProviderWiringTests(unittest.TestCase):
    setUp = test_builder.BuilderTests.setUp
    tearDown = test_builder.BuilderTests.tearDown
    args = test_builder.BuilderTests.args

    def test_provider_defaults(self):
        args = self.args()
        self.assertEqual((build._resolve_provider(args), args.model, args.reasoning_effort), ("claude", "claude-opus-5-5", "max"))
        args = self.args("--provider", "codex")
        self.assertEqual((build._resolve_provider(args), args.model, args.reasoning_effort), ("codex", "gpt-6.1-sol", "high"))
        with self.assertRaisesRegex(ValueError, "requires --provider claude"):
            build._resolve_provider(self.args("--provider", "codex", "--reasoning-effort", "max"))

    def test_default_build_runs_the_claude_generator_end_to_end(self):
        proposed = plan()
        proposed["initial_node"] = fixture()["workflow"]["initial_node"]

        def respond(request):
            schema = request["extra_body"]["output_config"]["format"]["schema"]
            if "plan_json" in schema["properties"]:
                wire = {"plan_json": json.dumps(proposed), "contract_sha256": schema["properties"]["contract_sha256"]["enum"][0]}
            else:
                wire = POLICY_WIRE
            return FakeStream(message(json.dumps(wire)))
        client = FakeClient(respond)
        made = []

        def make(*args, **kwargs):
            made.append(kwargs)
            return ClaudeGenerator(*args, client=client, **kwargs)
        with patch("builder.build.ClaudeGenerator", side_effect=make), \
                patch("builder.build.CodexGenerator", side_effect=AssertionError("Codex used")):
            manifest = build.execute(self.args("--generate-only"))
        self.assertEqual(made, [{"model": "claude-opus-5-5", "reasoning_effort": "max"}])
        self.assertEqual((manifest["status"], manifest["provider"], manifest["model"], manifest["reasoning_effort"]),
                         ("generated_validated", "claude", "claude-opus-5-5", "max"))
        run = PROJECT / manifest["run_directory"]
        events = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]
        self.assertEqual((events[0]["provider"], events[0]["model"], events[0]["reasoning_effort"]),
                         ("claude", "claude-opus-5-5", "max"))
        calls = [e for e in events if e["event"] == "model_call"]
        self.assertEqual([(c["provider"], c["model"]) for c in calls], [("anthropic", "claude-opus-5-5")] * 2)
        self.assertEqual(json.loads((run / "builder-source.json").read_text())["resolved_config"]["provider"], "claude")


if __name__ == "__main__":
    unittest.main()

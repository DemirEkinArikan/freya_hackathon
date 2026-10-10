"""Offline runner evidence checks; no platform/bank requests or simulations."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from benchmarks.hosted import HostedBackend, evaluate
from builder.platform import FreyaClient
from builder.runtime import Deadline, DeadlineExceeded


def fixture_rows(count=1):
    seed = json.loads((Path(__file__).resolve().parents[1] / "benchmarks/fixtures/hosted.json").read_text())["cases"][0]
    rows = [deepcopy(seed) for _ in range(count)]
    for index, row in enumerate(rows):
        row["fixture_id"] = f"mock-fixture-{index}"
    return rows


class RunnerBackend:
    def __init__(self, rows):
        self.rows = rows
        self.sessions, self.simulations, self.ledgers = [], [], {}
        self.run_number = 0

    def create_personas(self, personas):
        self.personas = personas
        return [{"id": f"persona-{index}"} for index in range(len(personas))]

    def practice_session(self, seed):
        number = len(self.sessions)
        variables = {"customer_id": f"customer-{number}", "session_id": f"session-{number}",
                     "now": "server-date", "server_data": {"facts": [number, "unchanged"]}}
        self.sessions.append((seed, deepcopy(variables)))
        return variables

    def simulate(self, agent_id, name, variables, concurrency):
        self.simulations.append(deepcopy(variables))
        self.run_number += 1
        for index, row in enumerate(self.rows):
            session = variables[f"persona-{index}"]
            expected = deepcopy(row["case"]["expected"])
            calls = [{"tool": "verify_identity", "args": {"field": field}, "result": {"verified": True}}
                     for field in ("date_of_birth", "phone_last4")]
            calls += [{"tool": "get_account", "args": {}, "result": {"ok": True}}]
            calls += [{"tool": "create_promise", "args": promise, "result": {"ok": True}}
                      for promise in expected["promises"]]
            calls += [{"tool": "close_call", "args": {"outcome": expected["outcome"]}, "result": {"ok": True}}]
            self.ledgers[session["customer_id"]] = {**expected, "calls": calls}
        return {"simulation_id": f"simulation-{self.run_number}", "iteration_id": f"iteration-{self.run_number}", "status": "completed"}

    def iteration(self, simulation):
        return {"runsTotal": len(self.rows), "runs": [{"personaId": f"persona-{index}",
                "outcome": "pass", "callId": f"call-{self.run_number}-{index}"} for index in range(len(self.rows))]}

    def ledger(self, customer):
        return self.ledgers[customer]

    def call(self, call_id):
        return {"id": call_id, "messages": [{"role": "assistant", "message": "Synthetic mock evidence"}]}

    def validate_deployment(self, deployment):
        return {"version_number": deployment["version_number"], "published": True}


class HostedRunnerTests(unittest.TestCase):
    def test_direct_selection_rejects_invalid_ids_and_seeds_before_mutation(self):
        for field, value in (("bank_case_id", "unknown"), ("bank_case_id", []), ("fixture_id", ""), ("fixture_id", None)):
            rows = fixture_rows()
            rows[0][field] = value
            backend = RunnerBackend(rows)
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                evaluate(backend, "agent", rows)
            self.assertFalse(hasattr(backend, "personas"))
        with self.assertRaises(ValueError):
            evaluate(RunnerBackend([]), "agent", [])

    def test_numeric_runtime_arguments_require_integers(self):
        for arguments in ({"reps": True}, {"reps": 1.5}, {"concurrency": True}, {"concurrency": 1.5}):
            rows = fixture_rows()
            backend = RunnerBackend(rows)
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                evaluate(backend, "agent", rows, **arguments)
            self.assertFalse(hasattr(backend, "personas"))

    def test_invalid_persona_id_is_rejected_before_sessions(self):
        rows = fixture_rows()
        for identifier in (None, "", []):
            backend = RunnerBackend(rows)
            backend.create_personas = lambda _: [{"id": identifier}]
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                evaluate(backend, "agent", rows)
            self.assertEqual(backend.sessions, [])

    def test_same_bank_seed_gets_fresh_unchanged_sessions_for_every_variant_and_repetition(self):
        rows = fixture_rows(2)
        backend = RunnerBackend(rows)
        result = evaluate(backend, "agent", rows)
        self.assertEqual((result["passed"], result["status"]), (2, "completed"))
        self.assertEqual(len(backend.sessions), 6)
        self.assertEqual([seed for seed, _ in backend.sessions], [rows[0]["bank_case_id"]] * 6)
        for index, variables in enumerate(backend.simulations):
            self.assertEqual(list(variables.values()), [session for _, session in backend.sessions[index * 2:index * 2 + 2]])
        self.assertEqual([s["simulation_id"] for s in result["simulations"]], ["simulation-1", "simulation-2", "simulation-3"])
        self.assertNotIn("customer-0", json.dumps(result))
        self.assertNotIn("session-0", json.dumps(result))

    def test_reused_customer_or_explicit_session_id_aborts_without_simulation(self):
        for field in ("customer_id", "session_id"):
            rows = fixture_rows(2)
            backend = RunnerBackend(rows)
            original = backend.practice_session
            def reused(seed):
                variables = original(seed)
                variables[field] = "reused-private-id"
                return variables
            backend.practice_session = reused
            result = evaluate(backend, "agent", rows)
            with self.subTest(field=field):
                self.assertEqual(result["status"], "infrastructure_failure")
                self.assertEqual(result["passed"], 0)
                self.assertEqual(backend.simulations, [])
                self.assertTrue(all(row["runs"] < row["required_runs"] for row in result["final"]))

    def test_bad_run_inventory_cannot_be_overwritten_into_a_pass(self):
        rows = fixture_rows(2)
        valid = RunnerBackend(rows).iteration({})
        variants = []
        duplicate = deepcopy(valid)
        duplicate["runs"].append(deepcopy(duplicate["runs"][0]))
        duplicate["runsTotal"] = 3
        variants.append(duplicate)
        unexpected = deepcopy(valid)
        unexpected["runs"][1]["personaId"] = "unrequested-persona"
        variants.append(unexpected)
        reused_call = deepcopy(valid)
        reused_call["runs"][1]["callId"] = reused_call["runs"][0]["callId"]
        variants.append(reused_call)
        incomplete = deepcopy(valid)
        incomplete["runsTotal"] = 3
        variants.append(incomplete)
        for iteration in variants:
            backend = RunnerBackend(rows)
            backend.iteration = lambda _: iteration
            result = evaluate(backend, "agent", rows, reps=1)
            with self.subTest(iteration=iteration):
                self.assertEqual((result["passed"], result["status"]), (0, "infrastructure_failure"))

    def test_version_mismatch_on_judge_or_transcript_invalidates_pass(self):
        rows = fixture_rows()
        deployment = {"agent_id": "agent", "version_number": 3}
        for source in ("judge", "transcript"):
            backend = RunnerBackend(rows)
            if source == "judge":
                original = backend.iteration
                def mismatched(simulation):
                    iteration = original(simulation)
                    iteration["runs"][0]["agentVersionNumber"] = 2
                    return iteration
                backend.iteration = mismatched
            else:
                original = backend.call
                backend.call = lambda call_id: {**original(call_id), "agentVersionNumber": 2}
            result = evaluate(backend, "agent", rows, reps=1, deployment=deployment)
            with self.subTest(source=source):
                self.assertEqual((result["passed"], result["status"]), (0, "infrastructure_failure"))

    def test_one_failed_repetition_prevents_final_pass(self):
        rows = fixture_rows()
        backend = RunnerBackend(rows)
        original = backend.iteration
        def one_failure(simulation):
            iteration = original(simulation)
            if backend.run_number == 2:
                iteration["runs"][0]["outcome"] = "fail"
            return iteration
        backend.iteration = one_failure
        result = evaluate(backend, "agent", rows)
        self.assertEqual(result["final"][0], {"fixture_id": rows[0]["fixture_id"], "passes": 2, "runs": 3, "required_runs": 3, "pass": False})

    def test_platform_polling_uses_shared_lease_and_expires_at_deadline(self):
        now, active, entered = [0.0], [False], []
        deadline = Deadline(15, clock=lambda: now[0])
        class MockLease:
            def __init__(self, path, lease_deadline, reserve):
                entered.append((path, lease_deadline))
            def __enter__(self):
                active[0] = True
            def __exit__(self, *_):
                active[0] = False
        client = FreyaClient(api_key="mock-only", deadline=deadline, lease_path=Path("mock-shared.lock"),
                             sleep=lambda seconds: now.__setitem__(0, now[0] + seconds))
        requests = []
        def request(method, route, body=None, **kwargs):
            self.assertTrue(active[0])
            requests.append((method, route))
            return {"simulation": {"id": "mock-simulation", "status": "running"}}
        client.request = request
        backend = HostedBackend(client, team_key="mock-team-key-only")
        with patch("builder.platform.FileLock", MockLease), self.assertRaises(DeadlineExceeded):
            backend.simulate("agent", "mock", {"persona": {"customer_id": "mock-customer"}})
        self.assertEqual(entered, [(Path("mock-shared.lock"), deadline)])
        self.assertFalse(active[0])
        self.assertEqual(now[0], 15)
        self.assertEqual(sum(method == "POST" for method, _ in requests), 1)

    def test_backend_iteration_pages_platform_runs_and_rejects_incomplete_totals(self):
        client = FreyaClient(api_key="mock-only")
        offsets = []
        def request(method, route, params):
            offsets.append(params)
            return {"runs": [{"id": index} for index in range(params["offset"], min(params["offset"] + 100, 101))], "runsTotal": 101}
        client.request = request
        backend = HostedBackend(client, team_key="mock-team-key-only")
        result = backend.iteration({"simulation_id": "simulation", "iteration_id": "iteration"})
        self.assertEqual(len(result["runs"]), 101)
        self.assertEqual(offsets, [{"limit": 100, "offset": 0}, {"limit": 100, "offset": 100}])
        client.request = lambda *args, **kwargs: {"runs": [], "runsTotal": 1}
        with self.assertRaises(RuntimeError):
            backend.iteration({"simulation_id": "simulation", "iteration_id": "iteration"})


if __name__ == "__main__":
    unittest.main()

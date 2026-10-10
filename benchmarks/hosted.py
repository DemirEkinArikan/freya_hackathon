"""Known-seed hosted evaluator. Inspection is default; --execute is required.

No synthetic policy/schema is sent to the hosted bank. Customer variables come
from fresh official practice sessions, never from the local expected fixture.
"""
import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import urllib.request
import urllib.parse

from .generate import KIT, ROOT
from .scoring import score_ledger

BANK = "https://freya-bank-mock.freyavoice.workers.dev"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def load_deployment(path):
    from builder.safety import read_json, digest
    path = Path(path)
    manifest = read_json(path)
    deployment = deepcopy(manifest.get("deployment", manifest))
    if "workflow" not in deployment:
        export = read_json(path.parent / "submission.workflow.json")
        if manifest.get("export_sha256") and digest(export) != manifest["export_sha256"]:
            raise ValueError("deployment export does not match builder manifest hash")
        deployment["workflow"] = export["workflow"]
    for field in ("model", "reasoning_effort"):
        if field in manifest: deployment[field] = manifest[field]
    required = ("workflow_id", "agent_id", "version_id", "version_number", "workflow")
    if any(field not in deployment for field in required) or deployment.get("published") is not True:
        raise ValueError("deployment manifest lacks a verified published graph/version")
    return deployment


def check_holdout_config(frozen, deployment, runtime=None):
    config = frozen.get("resolved_config", {})
    for key in ("model", "reasoning_effort"):
        if not isinstance(config.get(key), str) or not config[key]:
            raise ValueError("hosted holdout requires freeze --config-file with model and reasoning_effort")
        if config[key] != deployment.get(key):
            raise ValueError("deployment model/reasoning overrides differ from frozen configuration")
    for key, value in (runtime or {}).items():
        if key in config and config[key] != value:
            raise ValueError("evaluation runtime differs from frozen configuration: " + key)


def load_cases(path, split="all", brief=None):
    manifest = json.loads(Path(path).read_text())
    if isinstance(manifest, list):
        rows = [{"fixture_id": c["id"], "bank_case_id": c["id"], "brief": c["brief"], "split": "train",
                 "status": "materialized", "style": "plain", "case": c} for c in manifest]
        manifest = {"kind": "hosted", "cases": rows}
    if manifest.get("kind") != "hosted":
        raise ValueError("hosted evaluator refuses offline/synthetic policy fixtures")
    rows = [r for r in manifest["cases"] if (split == "all" or r["split"] == split) and (not brief or r["brief"] == brief)]
    identifiers = [r["fixture_id"] for r in rows]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("duplicate fixture IDs would overwrite persona/session mappings")
    known = {c["id"] for c in json.loads((KIT / "public-test-set.json").read_text())}
    if any(r["bank_case_id"] not in known for r in rows):
        raise ValueError("only documented public bank seeds are allowed")
    if not rows: raise ValueError("case selection is empty")
    return manifest, rows


def persona_for(row):
    # Importing the kit runner has no I/O; calling its main would mutate/spend.
    specification = importlib.util.spec_from_file_location("benchmark_kit_runner", KIT / "tools/run_cases.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    case = deepcopy(row["case"])
    style = row.get("style", "plain")
    if style not in ("plain", "compound"):
        case["style"] = style
    persona = module.persona_for(case, {"identity": case["identity"]})
    if style == "asks_back":
        persona["prompt"] += "\nOVERRIDE FOR THIS FIXTURE: both questions back happen only after the two security answers have been given and accepted. Never replace a security answer with a question."
    persona["name"] = row["fixture_id"] + " " + case["title"]
    return persona


class HostedBackend:
    def __init__(self, client, deadline=None, opener=None, team_key=None):
        self.client, self.deadline = client, deadline
        self.opener = opener or urllib.request.build_opener(NoRedirect()).open
        self.team_key = team_key or os.environ.get("FREYA_BANK_TEAM_KEY", "")
        if len(self.team_key) < 16:
            raise ValueError("FREYA_BANK_TEAM_KEY must contain at least 16 characters")

    def bank(self, path, body=None):
        if not (path == "/v1/practice/sessions" or path.startswith("/v1/practice/ledger/")):
            raise ValueError("bank route is outside the official evaluator allowlist")
        timeout = self.deadline.timeout(cap=20) if self.deadline else 20
        req = urllib.request.Request(BANK + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"User-Agent": "Mozilla/5.0 (hackathon-evaluator)", "Content-Type": "application/json", "x-team-key": self.team_key},
                                     method="POST" if body is not None else "GET")
        with self.opener(req, timeout=timeout) as response:
            return json.load(response)

    def practice_session(self, seed): return self.bank("/v1/practice/sessions", {"case": seed})["variables"]
    def ledger(self, customer): return self.bank("/v1/practice/ledger/" + urllib.parse.quote(customer, safe=""))
    def create_personas(self, personas): return self.client.create_personas(personas)
    def simulate(self, agent_id, name, variables, concurrency=5):
        return self.client.run_simulation(agent_id, name, variables, concurrency=concurrency)
    def iteration(self, simulation): return self.client.get_iteration(simulation["simulation_id"], simulation["iteration_id"])
    def call(self, call_id): return self.client.get_call(call_id)

    def validate_deployment(self, deployment):
        from builder.safety import digest
        stored = self.client.get_workflow(deployment["workflow_id"])
        if stored.get("agentId") != deployment["agent_id"] or digest(stored.get("workflow")) != digest(deployment["workflow"]):
            raise ValueError("agent or stored graph changed from pinned deployment")
        versions = self.client.request("GET", f"/api/v2/versions/workflow/{deployment['workflow_id']}", params={"workspaceId": self.client.workspace_id()})["versions"]
        selected = next((v for v in versions if v.get("id") == deployment["version_id"]), None)
        if not selected or selected.get("versionNumber") != deployment["version_number"] or selected.get("isPublished") is not True:
            raise ValueError("pinned version is no longer published")
        return {"workflow_id": deployment["workflow_id"], "agent_id": deployment["agent_id"],
                "version_id": deployment["version_id"], "version_number": deployment["version_number"],
                "graph_sha256": digest(stored["workflow"]), "published": True,
                "model": deployment.get("model"), "reasoning_effort": deployment.get("reasoning_effort")}


def evaluate(backend, agent_id, rows, *, reps=3, concurrency=5, deployment=None):
    if not isinstance(reps, int) or reps < 1: raise ValueError("reps must be positive")
    if not 1 <= concurrency <= 5: raise ValueError("concurrency must be between 1 and the kit default 5")
    if len({r["fixture_id"] for r in rows}) != len(rows): raise ValueError("duplicate fixture IDs would overwrite sessions")
    if any("case" not in r for r in rows): raise ValueError("held-out slots are reserved; freeze and generate before execution")
    if len({r["brief"] for r in rows}) > 1:
        raise ValueError("one agent must be evaluated on one brief; select --brief A or B")
    pin_before = None
    if deployment:
        if deployment["agent_id"] != agent_id: raise ValueError("requested agent differs from pinned deployment")
        pin_before = backend.validate_deployment(deployment)
    personas = backend.create_personas([persona_for(r) for r in rows])
    if isinstance(personas, dict): personas = personas["personas"]
    if len(personas) != len(rows) or len({p["id"] for p in personas}) != len(rows):
        raise ValueError("persona IDs are missing or duplicated")
    persona_ids = {r["fixture_id"]: p["id"] for r, p in zip(rows, personas)}
    all_rows, seen_sessions = [], set()
    for repetition in range(reps):
        try:
            variables = {}
            customer_ids = {}
            for row in rows:
                session = backend.practice_session(row["bank_case_id"])
                customer = session["customer_id"]
                if customer in seen_sessions: raise ValueError("bank reused a session; ledger isolation is unproven")
                seen_sessions.add(customer)
                customer_ids[row["fixture_id"]] = customer
                variables[persona_ids[row["fixture_id"]]] = deepcopy(session)
            simulation = backend.simulate(agent_id, f"bank benchmark repetition {repetition + 1}", variables, concurrency)
            if simulation.get("status") != "completed": raise RuntimeError("simulation did not complete")
            runs = {r["personaId"]: r for r in backend.iteration(simulation)["runs"]}
        except Exception as exc:
            all_rows.extend({"fixture_id": row["fixture_id"], "repetition": repetition + 1, "pass": False,
                             "failures": [{"category": "infrastructure", "message": type(exc).__name__ + ": " + str(exc)}]} for row in rows)
            break
        for row in rows:
            try:
                customer = customer_ids[row["fixture_id"]]
                ledger = backend.ledger(customer)
                verdict = score_ledger(row["case"]["expected"], ledger, brief=row["brief"])
                judged = runs.get(persona_ids[row["fixture_id"]])
                if deployment and judged and judged.get("agentVersionNumber") not in (None, deployment["version_number"]):
                    verdict["failures"].append({"category": "infrastructure", "message": "simulation used a different agent version"})
                if judged is None or judged.get("outcome") not in ("pass", "fail"):
                    verdict["failures"].append({"category": "infrastructure", "message": "judge verdict missing"})
                elif judged["outcome"] != "pass":
                    verdict["failures"].append({"category": "conversation", "message": str(judged.get("reason", "conversation failed"))})
                if verdict["missing_evidence"]:
                    verdict["failures"].append({"category": "evidence", "message": "; ".join(verdict["missing_evidence"])})
                transcript = None
                if judged and judged.get("callId"):
                    transcript = backend.call(judged["callId"])
                    if not isinstance(transcript, dict) or not isinstance(transcript.get("messages"), list) or not transcript["messages"]:
                        verdict["failures"].append({"category": "infrastructure", "message": "call transcript messages are absent"})
                else:
                    verdict["failures"].append({"category": "infrastructure", "message": "judge callId is absent"})
                # Pure ledger checks cannot establish spoken honesty. The platform
                # judge covers it; transcript evidence is retained for review.
                from builder.safety import sanitize
                secrets = [customer, os.environ.get("FREYA_API_KEY", ""), os.environ.get("FREYA_BANK_TEAM_KEY", "")]
                all_rows.append(sanitize({"fixture_id": row["fixture_id"], "bank_case_id": row["bank_case_id"], "repetition": repetition + 1,
                                         "pass": not verdict["failures"], "failures": verdict["failures"], "ledger": ledger,
                                         "call_id": judged.get("callId") if judged else None, "judge": judged, "transcript": transcript,
                                         "honesty": "platform_judge; not independently inferred from ledger",
                                         "session_fingerprint": hashlib.sha256(customer.encode()).hexdigest()[:16]}, secrets=secrets))
            except Exception as exc:
                all_rows.append({"fixture_id": row["fixture_id"], "repetition": repetition + 1, "pass": False,
                                 "failures": [{"category": "infrastructure", "message": type(exc).__name__ + ": " + str(exc)}]})
    pin_after = None
    if deployment:
        try:
            pin_after = backend.validate_deployment(deployment)
            if pin_after != pin_before: raise ValueError("deployment changed during evaluation")
        except Exception as exc:
            for row in all_rows:
                row["pass"] = False
                row["failures"].append({"category": "infrastructure", "message": "deployment pin verification failed: " + type(exc).__name__})
    final = []
    for row in rows:
        matches = [r for r in all_rows if r["fixture_id"] == row["fixture_id"]]
        passed = len(matches) == reps and all(r["pass"] for r in matches)
        final.append({"fixture_id": row["fixture_id"], "passes": sum(r["pass"] for r in matches), "runs": len(matches), "required_runs": reps, "pass": passed})
    failures = [f for r in all_rows for f in r["failures"]]
    result = {"passed": sum(r["pass"] for r in final), "total": len(rows), "reps": reps,
            "status": "infrastructure_failure" if any(f["category"] in ("infrastructure", "evidence") for f in failures) else "completed",
            "failures": failures, "final": final, "rows": all_rows,
            "deployment_pin": {"before": pin_before, "after": pin_after, "verified": bool(pin_before and pin_after == pin_before),
                               "per_call_version": "checked when platform supplies agentVersionNumber"},
            "claim": "Known public seeds and official policies; wording robustness, not policy/schema generalization."}
    from builder.safety import sanitize
    return sanitize(result, secrets=list(seen_sessions) + [os.environ.get("FREYA_API_KEY", ""), os.environ.get("FREYA_BANK_TEAM_KEY", "")])


def run_builder_tests(client, deployment, cases_path, output_dir, deadline):
    """Explicit controller test hook; caller owns live authorization and budget."""
    from .paths import output_path
    output = output_path(output_dir)
    destination = output_path(output / "benchmark-results.json")
    manifest, rows = load_cases(cases_path, brief=deployment.get("brief"))
    if any(r["split"] == "holdout" for r in rows):
        from .freeze import verify_freeze
        if not manifest.get("freeze"): raise ValueError("holdout requires a verified candidate freeze")
        verified = verify_freeze(manifest["freeze"]["record"])
        if verified["candidate_sha256"] != manifest["freeze"]["candidate_sha256"]:
            raise ValueError("manifest and candidate freeze do not match")
        check_holdout_config(verified, deployment)
    deadline.require(reserve=300)
    backend = HostedBackend(client, deadline)
    pinned = deployment if all(k in deployment for k in ("workflow_id", "workflow", "version_id", "version_number")) else None
    result = evaluate(backend, deployment["agent_id"], rows, reps=1, deployment=pinned)
    output.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--agent-id")
    ap.add_argument("--cases", default=str(ROOT / "fixtures/hosted.json"))
    ap.add_argument("--split", choices=("train", "validation", "holdout", "all"), default="train")
    ap.add_argument("--brief", choices=("A", "B"))
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--budget-seconds", type=int, default=1800)
    ap.add_argument("--out", default=str(ROOT / "runs/hosted-results.json"))
    ap.add_argument("--deployment-manifest", help="Verified builder manifest/export; required for held-out execution")
    actions = ap.add_mutually_exclusive_group()
    actions.add_argument("--execute", action="store_true")
    actions.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    manifest, rows = load_cases(args.cases, args.split, args.brief)
    if not args.execute:
        print(json.dumps({"mode": "dry_run", "cases": len(rows), "repetitions": args.reps,
                          "planned_calls": len(rows) * args.reps, "reserved": sum("case" not in r for r in rows),
                          "briefs": sorted({r["brief"] for r in rows}), "network": "none"}, indent=2))
        return
    if not args.agent_id: ap.error("--agent-id is required with --execute")
    from .paths import output_path
    target = output_path(args.out)
    if target.suffix != ".json": ap.error("--out must be a JSON result path")
    deployment = load_deployment(args.deployment_manifest) if args.deployment_manifest else None
    if any(r["split"] == "holdout" for r in rows):
        from .freeze import verify_freeze
        if not manifest.get("freeze"): raise ValueError("holdout requires a verified candidate freeze")
        frozen = verify_freeze(manifest["freeze"]["record"])
        if not deployment: raise ValueError("held-out execution requires --deployment-manifest")
        if frozen["candidate_sha256"] != manifest["freeze"]["candidate_sha256"]: raise ValueError("manifest freeze hash mismatch")
        check_holdout_config(frozen, deployment, {"concurrency": args.concurrency, "reps": args.reps, "budget_seconds": args.budget_seconds})
    from builder.platform import FreyaClient
    from builder.runtime import Deadline
    from builder.config import load_local_environment
    load_local_environment(ROOT.parent / ".env")
    deadline = Deadline(args.budget_seconds)
    client = FreyaClient(deadline=deadline, lease_path=ROOT.parent / "artifacts/.simulation.lock")
    result = evaluate(HostedBackend(client, deadline), args.agent_id, rows, reps=args.reps, concurrency=args.concurrency, deployment=deployment)
    from builder.safety import sanitize
    result = sanitize(result, secrets=[os.environ.get("FREYA_API_KEY", ""), os.environ.get("FREYA_BANK_TEAM_KEY", "")])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("passed", "total", "reps", "status")}, indent=2))
    if result["passed"] != result["total"]: raise SystemExit(1)


if __name__ == "__main__": main()

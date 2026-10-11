"""Synthetic checks for the T16 authoring kit; no real holdout content is authored or read."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from benchmarks import freeze
from benchmarks.development_protocol import REQUEST_TYPES, result_mapping
from benchmarks.input_api import validate_input_api
from benchmarks.offline import compile_expected, value_mismatches
from builder.safety import digest
from tools import t16_authoring as kit

ROOT = Path(__file__).resolve().parents[1]
SEEDS = {seed["id"]: seed for seed in json.loads((ROOT / "build-the-builder/public-test-set.json").read_text())}


def synthetic_bank(author):
    """A synthetic bank that differs from every development profile in all twelve dimensions."""
    codes = {category: {meaning: f"Z{author}_{category[0].upper()}{index}" for index, meaning in enumerate(meanings, 1)}
             for category, meanings in kit.CODES.items()}
    reason = codes["reasons"]
    values = {
        "identity_fields": ["member_code", "street_number"], "identity_wrong_limit": 5,
        "holidays": ["2027-06-10", "2027-06-21"], "business_weekdays": [0, 1, 2, 3, 4],
        "band_ranges": [{"name": "early", "min": 1, "max": 12}, {"name": "late", "min": 13, "max": 40}],
        "deadline_days": {"early": 9, "late": 5},
        "floor_fields": {"credit_card": "card_floor_zz", "personal_loan": "loan_floor_zz", "car_loan": "auto_floor_zz"},
        "callback_window": {"start": "09:00", "end": "17:30", "allow_today": False, "next_business_days": 2},
        "note_reasons": list(codes["notes"].values()),
        "close_reasons": {codes["outcomes"]["promise"]: [], codes["outcomes"]["paid"]: [],
                          codes["outcomes"]["transferred"]: [reason["support"], reason["human"]],
                          codes["outcomes"]["no_promise"]: [reason["refused"], reason["hardship"], reason["dispute"]],
                          codes["outcomes"]["not_verified"]: [reason["failed"], reason["declined"]],
                          codes["outcomes"]["not_reached"]: [reason["callback"], reason["wrong_number"], reason["unclear"]]},
        "transfer_reasons": {codes["queues"]["support"]: reason["support"], codes["queues"]["general"]: reason["human"],
                             codes["queues"]["restructuring"]: reason["restructuring"]},
        "require_confirmation": True, "promise_failure_limit": 3, "persuasion_limits": {"early": 0, "late": 3},
        "no_notes_on_transfer": True, "recorded_amendments": "synthetic amendments text",
        "closing_precedence": "synthetic precedence text", "multi_product_order": "synthetic order text",
        "hardship_behavior": "synthetic hardship text", "amount_range": "synthetic amount text",
        "call_goals": {"default": "commitment_resolution", "available": list(kit.CODES["goals"]), "variable": "call_goal",
                       **{goal: f"Synthetic {author} description of {goal}." for goal in kit.CODES["goals"]}},
        "wire_codes": codes,
        "call_variables": {name: "synthetic meaning of " + name for name in sorted(kit.VARIABLES)},
        "action_fields": {tool: {field: f"zz_{field}" for field in fields} for tool, fields in REQUEST_TYPES.items()},
        "result_fields": result_mapping(True),
    }
    lines = {kind: f"[{author}] {kind} := {json.dumps(value, sort_keys=True)}" for kind, value in values.items()}
    old_limit = f"[{author}] old extract: identity_wrong_limit was 6 before the memo"
    clean = {"policy": kit.LABEL + "\n" + "\n".join(lines.values())}
    half = list(values)[:12]
    raw = {"memo": kit.LABEL + "\nEffective memo.\n" + "\n".join(lines[k] for k in half),
           "old-extract": kit.LABEL + "\nOld extract.\n" + old_limit + "\n" + "\n".join(lines[k] for k in values if k not in half)}
    return {"now": "2027-06-07T10:00", "identity": {"member_code": "Q7731", "street_number": "42"}, "effective_values": values,
            "representations": {
                "clean": {"documents": clean, "superseded": [],
                          "citations": {k: {"document": "policy", "quote": lines[k]} for k in values}},
                "raw": {"documents": raw,
                        "citations": {k: {"document": "memo" if k in half else "old-extract", "quote": lines[k]} for k in values},
                        "superseded": [{"kind": "identity_wrong_limit", "value": 6, "document": "old-extract", "quote": old_limit}]}}}


def synthetic_author(assignment, authored_at):
    author = assignment["author"]
    bank = synthetic_bank(author)
    values = bank["effective_values"]
    codes = values["wire_codes"]
    tags = (["calendar"], ["counters"], ["outcome_binding"], ["fault"], ["multi_product"])
    local = []
    for index, item in enumerate(assignment["local"]):
        products = [{"product_id": "p1", "type": "credit_card", "card_last4": "1111", "past_due": 300.0, "days_late": 5, "card_floor_zz": 90.0}]
        if tags[index] == ["multi_product"]:
            products.append({"product_id": "p2", "type": "car_loan", "past_due": 200.0, "days_late": 20, "auto_floor_zz": 50.0})
        expected = {"outcome": codes["outcomes"]["promise"], "reason": None,
                    "promises": [{"product_id": "p1", "amount": 300.0, "date": "2027-06-08"}], "notes": [], "callbacks": [], "transfers": []}
        if tags[index] == ["outcome_binding"]:
            expected = {"outcome": codes["outcomes"]["no_promise"], "reason": codes["reasons"]["refused"], "promises": [],
                        "notes": [{"product_id": "p1", "reason": codes["notes"]["refused"]}], "callbacks": [], "transfers": []}
        ids = [p["product_id"] for p in products]
        local.append({"reservation": item["reservation"], "authored_at": authored_at, "dimensions": tags[index],
                      "description": f"Synthetic {author} case {index}", "faults": [{"tool": "create_promise", "fail_first_n": 1}] if index == 3 else [],
                      "variables": {"customer_id": f"syn-{author}-{index}", "first_name": "Test", "last_name": "Person", "title": "Mx",
                                    "now": "2027-06-07T10:00", "call_goal": codes["goals"]["commitment_resolution"]},
                      "identity": dict(bank["identity"]), "products": products,
                      "customer_turns": [{"when": "opening", "say": f"Synthetic {author} turn {index}"}],
                      "script": f"Synthetic {author} script {index}", "expected": expected,
                      "helper_expected": {"deadlines": {i: "2027-06-18" for i in ids}, "floors": {i: 90.0 for i in ids},
                                          "bands": {i: "early" for i in ids}},
                      "checks": ["Synthetic check."]})
    hosted = [{"reservation": item["reservation"], "authored_at": authored_at, "dimensions": list(kit.WORDING_DIMENSIONS),
               "script": SEEDS[item["bank_case_id"]]["script"] + f"\nSynthetic {author} caller behaviour {index}: speaks slowly and asks twice."}
              for index, item in enumerate(assignment["hosted"])]
    return {"version": 1, "author": author, "bank": bank, "local_cases": local, "hosted_cases": hosted}


class T16AuthoringKit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        (self.dir / "candidate").mkdir()
        (self.dir / "candidate/runtime.py").write_text("print('synthetic candidate')\n")
        self.record = self.dir / "freeze.json"
        self.frozen = freeze.freeze_candidate(self.dir / "candidate", self.record)

    def workspace(self, author, authored_at=None):
        workspace = self.dir / "authors" / author
        assignment = kit.prepare(kit.SPEC, author, self.record, workspace)
        stamp = authored_at or datetime.now(timezone.utc).isoformat()
        (workspace / "author.json").write_text(json.dumps(synthetic_author(assignment, stamp)))
        return workspace

    def edit(self, workspace, change):
        data = json.loads((workspace / "author.json").read_text())
        change(data)
        (workspace / "author.json").write_text(json.dumps(data))
        return kit.build_rows(workspace, self.record)[3]

    def test_spec_assigns_every_reservation_once_without_content(self):
        spec, base = kit.load_spec(), kit._base(kit.BASE)
        cores = [kit.assignment_core(spec, author["id"], base) for author in spec["authors"]]
        self.assertEqual(sorted(i["reservation"] for c in cores for i in c["local"]), sorted(kit._reservations(base, "offline")))
        self.assertEqual(sorted(i["reservation"] for c in cores for i in c["hosted"]), sorted(kit._reservations(base, "hosted")))
        self.assertEqual(set(Counter(i["archetype_id"] for c in cores for i in c["local"]).values()), {2})
        self.assertEqual(set(Counter(d for a in spec["authors"] for d in a["policy_dimensions"]).values()), {4})
        text = (ROOT / "docs/T16-FANOUT-SPEC.json").read_text()
        for leaked in ("PUB-", '"script"', '"expected"', '"seed"'):
            self.assertNotIn(leaked, text)
        for path in spec["approved_inputs"]:
            self.assertTrue((ROOT / path).is_file(), path)

    def test_valid_author_builds_lineage_bound_rows(self):
        workspace = self.workspace("A01")
        offline, profiles, hosted, errors = kit.build_rows(workspace, self.record)
        self.assertEqual(errors, [])
        self.assertEqual((len(offline), len(profiles), len(hosted)), (5, 2, 5))
        for row in offline + hosted:
            self.assertEqual(set(row["lineage"]), {"authored_at", "author", "candidate_sha256", "oracle_sha256", "isolation"})
            self.assertEqual(row["lineage"]["candidate_sha256"], self.frozen["candidate_sha256"])
            self.assertEqual(row["status"], "materialized_after_freeze")
        for row in hosted:
            seed = SEEDS[row["bank_case_id"]]
            self.assertEqual(row["case"]["expected"], seed["expected"])
            self.assertEqual(row["case"]["checks"][:len(seed.get("checks", []))], seed.get("checks", []))
        for profile in profiles:
            validate_input_api(profile["openapi"])
            compiled = compile_expected(profile["contract_expected"], profile)
            self.assertEqual(value_mismatches(compiled, profile), [])
        self.assertEqual({p["representation"] for p in profiles}, {"clean", "raw_packet"})
        self.assertNotEqual(profiles[0]["contract_expected"]["sources"], profiles[1]["contract_expected"]["sources"])

    def test_rejects_cases_dated_at_or_before_freeze_or_preparation(self):
        before = (datetime.fromisoformat(self.frozen["created_at"]) - timedelta(seconds=1)).isoformat()
        errors = kit.build_rows(self.workspace("A02", before), self.record)[3]
        self.assertTrue(any("authored_at" in e for e in errors), errors)
        between = (datetime.fromisoformat(self.frozen["created_at"]) + timedelta(microseconds=1)).isoformat()
        errors = kit.build_rows(self.workspace("A03", between), self.record)[3]
        self.assertTrue(any("authored_at" in e for e in errors), errors)
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        errors = kit.build_rows(self.workspace("A04", future), self.record)[3]
        self.assertTrue(any("authored_at" in e for e in errors), errors)
        workspace = self.workspace("A05")
        errors = self.edit(workspace, lambda d: d["local_cases"][0].update(authored_at="2027-06-07T10:00"))
        self.assertTrue(any("authored_at" in e for e in errors), errors)

    def test_rejects_unsafe_or_stale_authoring(self):
        workspace = self.workspace("A06")
        dev = json.loads((ROOT / "benchmarks/fixtures/offline.json").read_text())
        dev_case = next(r for r in dev["cases"] if r["split"] == "train")
        dev_values = dev["profiles"][0]["effective_values_expected"]
        dev_hosted = next(r for r in json.loads((ROOT / "benchmarks/fixtures/hosted.json").read_text())["cases"] if r.get("style") == "compound")
        dimension = kit.load_spec()["authors"][5]["policy_dimensions"][0]
        cases = {
            "seed script changed": lambda d: d["hosted_cases"][0].update(script="Different facts. " * 5),
            "template wording": lambda d: d["hosted_cases"][1].update(
                script=d["hosted_cases"][1]["script"].split("\n")[0] + "\n" + dev_hosted["case"]["script"].splitlines()[-1]),
            "exposed local script": lambda d: d["local_cases"][0].update(script=dev_case["script"]),
            "dimension unchanged": lambda d: d["bank"]["effective_values"].update(
                {k: dev_values[k] for k in kit.POLICY_DIMENSIONS[dimension] if dimension not in ("outcome_bindings", "wire_codes")}),
            "quote not literal": lambda d: d["bank"]["representations"]["raw"]["citations"]["holidays"].update(quote="paraphrased"),
            "clean equals raw": lambda d: d["bank"]["representations"]["raw"]["documents"].update(
                memo=d["bank"]["representations"]["clean"]["documents"]["policy"]),
            "missing reservation": lambda d: d["local_cases"].pop(),
            "foreign code": lambda d: d["local_cases"][1]["expected"].update(outcome="promise"),
            "malformed": lambda d: d["bank"]["effective_values"].update(note_reasons=7),
        }
        if dimension in ("outcome_bindings", "wire_codes"):
            del cases["dimension unchanged"]
        self.assertEqual(kit.build_rows(workspace, self.record)[3], [])
        original = (workspace / "author.json").read_text()
        for name, change in cases.items():
            with self.subTest(name):
                (workspace / "author.json").write_text(original)
                self.assertTrue(self.edit(workspace, change), name)

    def test_prepare_isolates_workspaces(self):
        with self.assertRaises(ValueError):
            kit.prepare(kit.SPEC, "A07", self.record, ROOT / "artifacts/t16-author-should-not-exist")
        workspace = self.workspace("A07")
        with self.assertRaises(ValueError):
            kit.prepare(kit.SPEC, "A07", self.record, workspace)
        (workspace / "inputs/build-the-builder/public-test-set.json").write_text("[]")
        self.assertTrue(any("changed after preparation" in e for e in kit.build_rows(workspace, self.record)[3]))
        freeze.consume(self.record, "synthetic repair disclosure")
        with self.assertRaises(ValueError):
            kit.prepare(kit.SPEC, "A08", self.record, self.dir / "authors/A08")

    def test_critical_rule_uses_oracle_facts(self):
        codes = {"outcomes": {m: m.upper() for m in kit.CODES["outcomes"]}}
        def local(outcome, **extra):
            return {"expected": {"outcome": outcome, "promises": [], "transfers": []}, "faults": [], "wire_codes": codes, **extra}
        self.assertEqual(kit.critical_reasons(local("PAID"), "offline"), [])
        self.assertEqual(kit.critical_reasons(local("NOT_REACHED"), "offline"), ["security_gate"])
        self.assertEqual(kit.critical_reasons(local("PAID", bucket="adversarial_dialogue"), "offline"), ["adversarial"])
        hosted = {"style": "plain", "case": {"expected": {"outcome": "promise", "promises": [{}], "transfers": [{}]}, "faults": [{}]}}
        self.assertEqual(kit.critical_reasons(hosted, "hosted"), ["irreversible_write", "handoff", "fault_recovery"])

    def test_assemble_and_account_keep_lanes_separate(self):
        workspaces = [self.workspace(author["id"]) for author in kit.load_spec()["authors"]]
        out = self.dir / "pack"
        summary = kit.assemble(self.record, workspaces, out)
        self.assertEqual((summary["offline"]["holdout_rows"], summary["hosted"]["holdout_rows"]), (60, 60))
        with self.assertRaises(ValueError):
            kit.assemble(self.record, workspaces, out)
        packs = {k: json.loads((out / (k + ".json")).read_text()) for k in ("offline", "hosted")}
        for kind, pack in packs.items():
            inventory = freeze._manifest_inventory(pack, kind)
            self.assertEqual((inventory["materialized_holdout_rows"], inventory["reserved_holdout_rows"]), (60, 0))
        hosted_rows = [r for r in packs["hosted"]["cases"] if r["split"] == "holdout"]
        first, second, third = hosted_rows[:3]
        results = {"deployment_pin": {"verified": True, "per_call_version_verified": True}, "rows":
                   [{"fixture_id": first["fixture_id"], "repetition": n, "pass": True, "failures": []} for n in (1, 2, 3)]
                   + [{"fixture_id": second["fixture_id"], "repetition": n, "pass": True, "failures": []} for n in (1, 2)]
                   + [{"fixture_id": third["fixture_id"], "repetition": 1, "pass": False, "failures": [{"category": "behavior"}]}]}
        (self.dir / "hosted.json").write_text(json.dumps(results))
        unpinned = {"deployment_pin": {"verified": False}, "rows": [{"fixture_id": second["fixture_id"], "repetition": 3, "pass": True, "failures": []}]}
        (self.dir / "unpinned.json").write_text(json.dumps(unpinned))
        local_rows = [r for r in packs["offline"]["cases"] if r["split"] == "holdout"]
        profiles = {p["profile_id"]: p for p in packs["offline"]["profiles"]}
        plain = next(r for r in local_rows if not r["authoring"]["critical"])
        local_files = []
        for name, profile_id in (("clean", plain["profile_id"]), ("raw", plain["paired_profile_id"])):
            path = self.dir / f"local-{name}.json"
            path.write_text(json.dumps({"fixture_id": plain["fixture_id"], "profile_id": profile_id, "fixture_sha256": digest(plain),
                                        "profile_sha256": digest(profiles[profile_id]), "ledger_verdict": {"pass": True, "failures": []}}))
            local_files.append(str(path))
        contract = self.dir / "policy.json"
        clean = profiles[plain["profile_id"]]
        contract.write_text(json.dumps({k: v for k, v in clean["contract_expected"].items() if k != "source_documents"}))
        report = kit.account(out, [str(self.dir / "hosted.json"), str(self.dir / "unpinned.json")], local_files,
                             [f"{clean['profile_id']}={contract}"])
        hosted = {r["fixture_id"]: r["status"] for r in report["wording_robustness"]["rows"]}
        self.assertEqual((hosted[first["fixture_id"]], hosted[second["fixture_id"]], hosted[third["fixture_id"]]), ("pass", "incomplete", "fail"))
        self.assertEqual(report["wording_robustness"]["uncovered"], 57)
        local = {r["fixture_id"]: r["status"] for r in report["policy_adaptation"]["rows"]}
        self.assertEqual(local[plain["fixture_id"]], "pass")
        self.assertEqual(report["policy_adaptation"]["compilation"]["matched"], 1)
        self.assertEqual(len(report["policy_adaptation"]["compilation"]["missing"]), 23)
        self.assertTrue(report["policy_adaptation"]["blind"] is True and report["wording_robustness"]["blind"] is True)
        self.assertNotIn("combined", json.dumps({k: v for k, v in report.items() if k != "claim"}))
        tampered = json.loads(Path(local_files[0]).read_text())
        tampered["fixture_sha256"] = "0" * 64
        Path(local_files[0]).write_text(json.dumps(tampered))
        with self.assertRaises(ValueError):
            kit.account(out, local_results=local_files)
        freeze.consume(self.record, "synthetic repair disclosure")
        self.assertIsNot(kit.account(out)["wording_robustness"]["blind"], True)


if __name__ == "__main__":
    unittest.main()

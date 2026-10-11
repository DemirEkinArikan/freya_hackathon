"""T16 fresh-holdout authoring: isolated author workspaces, validation, packs, accounting.

`prepare`, `validate` and `assemble` require a verified accepted-candidate
freeze; `account` reports whether a pack is still blind; `check-spec` only reads
reservation IDs. Nothing here authors cases, chooses seeds or calls a model,
bank or Freya. Author files and packs stay outside generator inputs.
Validation uses evaluator code only; candidate compilers and helpers run only
in `account`, which is an evaluation step. See docs/T16-AUTHORING-PROTOCOL.md.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks import freeze
from benchmarks.development_protocol import REQUEST_TYPES, response_definitions, result_mapping
from benchmarks.seed_audit import LEDGER_FIELDS, seed_outcome_constraints, validate_seed_rows
from builder.safety import canonical_json, digest, read_json

SPEC = ROOT / "docs/T16-FANOUT-SPEC.json"
BASE = ROOT / "benchmarks/fixtures"
LABEL = "SYNTHETIC TEST POLICY: NOT FREYA OFFICIAL POLICY. No real customer data."
RESERVED = "reserved_until_candidate_freeze"
KINDS = ("identity_fields", "identity_wrong_limit", "holidays", "business_weekdays", "band_ranges", "deadline_days",
         "floor_fields", "callback_window", "note_reasons", "close_reasons", "transfer_reasons", "require_confirmation",
         "promise_failure_limit", "persuasion_limits", "no_notes_on_transfer", "recorded_amendments", "closing_precedence",
         "multi_product_order", "hardship_behavior", "amount_range", "call_goals", "wire_codes", "call_variables",
         "action_fields", "result_fields")
CODES = {"outcomes": ("promise", "paid", "transferred", "no_promise", "not_verified", "not_reached"),
         "reasons": ("support", "human", "restructuring", "refused", "hardship", "dispute", "service_error", "failed",
                     "declined", "callback", "no_callback", "wrong_number", "deceased", "unclear"),
         "notes": ("refused", "hardship", "dispute", "paid", "service_error"),
         "queues": ("support", "general", "restructuring"),
         "goals": ("contact_callback", "exception_triage", "commitment_resolution")}
POLICY_DIMENSIONS = {"identity_schema": ("identity_fields",), "identity_counter": ("identity_wrong_limit",),
                     "calendar": ("holidays",), "deadline_bands": ("band_ranges", "deadline_days"),
                     "floor_fields": ("floor_fields",), "retry_counter": ("promise_failure_limit",),
                     "persuasion_counter": ("persuasion_limits",),
                     "outcome_bindings": ("close_reasons", "transfer_reasons", "note_reasons"),
                     "wire_codes": ("wire_codes",), "goals": ("call_goals",), "request_schema": ("action_fields",),
                     "callback_window": ("callback_window",)}
CASE_TAGS = ("calendar", "counters", "outcome_binding", "multi_product", "fault", "adversarial")
REQUIRED_CASE_TAGS = ("calendar", "counters", "outcome_binding")
STYLE_DIMENSION = {"plain": "paraphrase", "talkative": "paraphrase", "in_words": "paraphrase", "pieces": "interruptions",
                   "asks_back": "interruptions", "corrects": "corrections", "compound": "compound"}
WORDING_DIMENSIONS = ("paraphrase", "corrections", "interruptions", "compound")
ISOLATION = {"outside_generator_inputs": True, "candidate_outputs_consulted": False}
VARIABLES = {"customer_id", "first_name", "last_name", "title", "now", "call_goal"}
PRODUCT_TYPES = ("credit_card", "personal_loan", "car_loan")


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _time(value):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.utcoffset() is not None else None


def _norm(text):
    return " ".join(str(text).split()).lower()


def load_spec(path=SPEC):
    """Validate the fan-out plan: 12 isolated authors, 60 + 60 distinct reservations, full dimension coverage."""
    spec = read_json(path)
    authors = spec.get("authors") if isinstance(spec, dict) else None
    if not isinstance(authors, list) or not authors or spec.get("version") != 1:
        raise ValueError("fan-out spec schema is invalid")
    inputs, forbidden = spec.get("approved_inputs"), spec.get("forbidden_input_prefixes")
    if (not isinstance(inputs, list) or not inputs or not isinstance(forbidden, list) or not forbidden
            or any(not isinstance(p, str) or Path(p).is_absolute() or ".." in Path(p).parts
                   or any(p.startswith(f) for f in forbidden) for p in inputs)):
        raise ValueError("fan-out approved inputs must be relative repository files outside every forbidden prefix")
    ids, local, hosted, policy = set(), [], [], []
    for author in authors:
        if (not isinstance(author, dict) or not re.fullmatch(r"A\d\d", str(author.get("id"))) or author["id"] in ids
                or set(author) != {"id", "local_reservations", "hosted_reservations", "policy_dimensions"}):
            raise ValueError("fan-out author entry is invalid")
        ids.add(author["id"])
        if len(author["local_reservations"]) != 5 or len(author["hosted_reservations"]) != 5:
            raise ValueError(f"author {author['id']} needs exactly 5 local and 5 hosted reservations")
        if len(set(author["policy_dimensions"])) != 4 or set(author["policy_dimensions"]) - set(POLICY_DIMENSIONS):
            raise ValueError(f"author {author['id']} needs four distinct supported policy dimensions")
        local += author["local_reservations"]
        hosted += author["hosted_reservations"]
        policy += author["policy_dimensions"]
    if len(local) != 60 or len(set(local)) != 60 or len(hosted) != 60 or len(set(hosted)) != 60:
        raise ValueError("fan-out spec must assign 60 distinct local and 60 distinct hosted reservations")
    if set(policy) != set(POLICY_DIMENSIONS):
        raise ValueError("fan-out spec leaves a supported policy dimension unassigned")
    return spec


def _base(base_dir):
    return {name: read_json(Path(base_dir) / (name + ".json")) for name in ("offline", "hosted")}


def _reservations(base, kind):
    return {row["fixture_id"]: row for row in base[kind]["cases"]
            if row.get("split") == "holdout" and row.get("status") == RESERVED and "case" not in row}


def assignment_core(spec, author_id, base):
    """The deterministic part of an assignment: reservation metadata only, never content or seed facts."""
    author = next((a for a in spec["authors"] if a["id"] == author_id), None)
    if author is None:
        raise ValueError("author is not in the fan-out spec")
    local, hosted = _reservations(base, "offline"), _reservations(base, "hosted")
    if set(author["local_reservations"]) - set(local) or set(author["hosted_reservations"]) - set(hosted):
        raise ValueError("assignment names a row that is not an unmaterialized reservation")
    return {"author": author_id, "spec_sha256": digest(spec), "bank_id": "t16-" + author_id,
            "policy_dimensions": author["policy_dimensions"],
            "local": [{"reservation": rid, **{k: local[rid][k] for k in ("archetype_id", "number", "bucket", "description")}}
                      for rid in author["local_reservations"]],
            "hosted": [{"reservation": rid, "bank_case_id": hosted[rid]["bank_case_id"], "brief": hosted[rid]["brief"],
                        "style": hosted[rid]["style"], "required_dimension": STYLE_DIMENSION[hosted[rid]["style"]]}
                       for rid in author["hosted_reservations"]]}


def prepare(spec_path, author_id, record, workspace, base_dir=BASE):
    """Create one isolated author workspace containing only approved inputs and the assignment."""
    spec = load_spec(spec_path)
    frozen = freeze.verify_freeze(record)
    workspace = freeze._regular_path(workspace, "author workspace")
    for root in (Path(frozen["candidate_root"]), ROOT):
        if workspace == root.resolve() or root.resolve() in workspace.parents:
            raise ValueError("author workspace must be outside the candidate and this repository checkout")
    if workspace.exists():
        raise ValueError("author workspace already exists; use a new workspace")
    core = assignment_core(spec, author_id, _base(base_dir))
    inputs = []
    for relative in spec["approved_inputs"]:
        source = freeze._regular_path(ROOT / relative, "approved input")
        if not source.is_file() or ROOT not in source.parents:
            raise ValueError("approved input must be a regular repository file: " + relative)
        inputs.append({"path": relative, "sha256": _sha(source)})
    for item in inputs:
        target = workspace / "inputs" / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / item["path"], target)
    assignment = {"version": 1, **core, "inputs": inputs,
                  "freeze": {"record": str(Path(record).resolve()), "candidate_sha256": frozen["candidate_sha256"],
                             "created_at": frozen["created_at"]},
                  "prepared_at": datetime.now(timezone.utc).isoformat(), "output": "author.json"}
    with (workspace / "assignment.json").open("x", encoding="utf-8") as out:
        out.write(json.dumps(assignment, indent=2) + "\n")
    return assignment


def critical_reasons(row, kind):
    """Declared critical-case rule; derived from oracle facts, never from an author's own flag."""
    if kind == "hosted":
        expected, faults = row["case"]["expected"], row["case"].get("faults") or []
        outcome = expected["outcome"]
        adversarial = row.get("style") == "compound" or "compound" in row.get("authoring", {}).get("dimensions", [])
    else:
        expected, faults = row["expected"], row.get("faults") or []
        outcome = {code: meaning for meaning, code in row["wire_codes"]["outcomes"].items()}.get(expected["outcome"])
        adversarial = row.get("bucket") == "adversarial_dialogue" or "adversarial" in row.get("authoring", {}).get("dimensions", [])
    reasons = []
    if expected.get("promises"): reasons.append("irreversible_write")
    if outcome in ("not_verified", "not_reached"): reasons.append("security_gate")
    if expected.get("transfers"): reasons.append("handoff")
    if faults: reasons.append("fault_recovery")
    if adversarial: reasons.append("adversarial")
    return reasons


def _semantic(kind, values):
    inverse = {cat: {code: meaning for meaning, code in table.items()} for cat, table in values["wire_codes"].items()}
    value = values[kind]
    if kind == "close_reasons":
        return {inverse["outcomes"].get(o, o): sorted(inverse["reasons"].get(r, r) for r in rs) for o, rs in value.items()}
    if kind == "transfer_reasons":
        return {inverse["queues"].get(q, q): inverse["reasons"].get(r, r) for q, r in value.items()}
    if kind == "note_reasons":
        return sorted(inverse["notes"].get(n, n) for n in value)
    return value


def _openapi(values):
    """Build the bank API from the authored configuration with the documented development-v2 protocol."""
    raw = values["result_fields"] == result_mapping(True)
    codes = values["wire_codes"]
    enums = {("verify_identity", "field"): list(values["identity_fields"]),
             ("transfer_to_agent", "queue"): list(codes["queues"].values()),
             ("add_note", "reason"): list(codes["notes"].values()),
             ("close_call", "outcome"): list(codes["outcomes"].values()),
             ("close_call", "reason"): list(codes["reasons"].values())}
    product = {"product_id": {"type": "string"}, "type": {"type": "string", "enum": list(PRODUCT_TYPES)},
               "past_due": {"type": "number", "description": "Full overdue balance"}, "days_late": {"type": "integer"},
               "card_last4": {"type": "string"}}
    for typ, field in values["floor_fields"].items():
        meaning = "minimum_payment" if typ == "credit_card" else "instalment_amount"
        product.setdefault(field, {"type": "number", "description": "Permitted payment floor; semantic meaning " + meaning})
    paths = {}
    for tool, fields in REQUEST_TYPES.items():
        props = {}
        for name, typ in fields.items():
            props[values["action_fields"][tool][name]] = {"type": typ, "description": "Semantic field: " + name,
                                                           **({"enum": enums[tool, name]} if (tool, name) in enums else {})}
        required = [values["action_fields"][tool][name] for name in fields if not (tool == "close_call" and name == "reason")]
        paths["/" + tool] = {"post": {"operationId": tool, "requestBody": {"content": {"application/json": {
            "schema": {"type": "object", "properties": props, "required": required}}}},
            "responses": response_definitions(tool, product, values["identity_wrong_limit"], raw)}}
    return {"openapi": "3.0.3", "info": {"title": LABEL, "version": "synthetic-2"},
            "servers": [{"url": "https://synthetic-bank.invalid/v1"}], "paths": paths}


def _profile(bank_id, name, bank, representation, openapi):
    values = bank["effective_values"]
    documents = representation["documents"]
    ids = {doc: f"brief-{index}" for index, doc in enumerate(documents, 1)}
    profile_id = f"{bank_id}-{name}"
    rules = []
    for kind in KINDS:
        citation = representation["citations"][kind]
        rule = {"id": f"{profile_id}:{kind}", "kind": kind, "value": deepcopy(values[kind]),
                "source": {"id": ids[citation["document"]], "quote": citation["quote"]}}
        if any(item["kind"] == kind for item in representation["superseded"]):
            rule["overrides"] = [f"{profile_id}:historical:{kind}"]
        rules.append(rule)
    for item in representation["superseded"]:
        rules.append({"id": f"{profile_id}:historical:{item['kind']}", "kind": item["kind"], "value": deepcopy(item["value"]),
                      "source": {"id": ids[item["document"]], "quote": item["quote"]}})
    sources = [{"id": ids[doc], "file": doc + ".md", "sha256": digest(text)} for doc, text in documents.items()]
    return {"profile_id": profile_id, "label": LABEL, "now": bank["now"], "identity": bank["identity"],
            "contract_expected": {"version": 1, "goal": values["call_goals"][values["call_goals"]["default"]],
                                  "allowed_actions": list(REQUEST_TYPES), "sources": sources, "rules": rules,
                                  "source_documents": {ids[doc]: text for doc, text in documents.items()}},
            "policy_documents": dict(documents), "representation": "clean" if name == "clean" else "raw_packet",
            "wire_codes": values["wire_codes"], "effective_values_expected": deepcopy(values), "openapi": openapi,
            "request_field_mapping": {s: w for fields in values["action_fields"].values() for s, w in fields.items() if s != w},
            "result_field_mapping": values["result_fields"]}


def _bank_errors(bank, dimensions, exposed_profiles):
    errors = []
    values = bank.get("effective_values")
    if not isinstance(values, dict) or set(values) != set(KINDS):
        return ["bank effective_values must contain exactly the supported policy kinds"]
    codes = values["wire_codes"]
    if (not isinstance(codes, dict) or set(codes) != set(CODES)
            or any(not isinstance(codes[c], dict) or set(codes[c]) != set(CODES[c]) for c in CODES)
            or any(not isinstance(v, str) or not v.strip() for c in CODES for v in codes[c].values())
            or any(len(set(codes[c].values())) != len(CODES[c]) for c in CODES)):
        return ["wire_codes must map every supported meaning to a distinct nonblank code"]
    identity = bank.get("identity")
    if (not isinstance(identity, dict) or len(identity) < 2 or values["identity_fields"] != list(identity)
            or any(not isinstance(v, str) or not v.strip() for v in identity.values())):
        errors.append("identity must give at least two nonblank answers in identity_fields order")
    if values["result_fields"] not in (result_mapping(False), result_mapping(True)):
        errors.append("result_fields must be the canonical or the raw development-v2 mapping")
    actions = values["action_fields"]
    if (not isinstance(actions, dict) or set(actions) != set(REQUEST_TYPES)
            or any(not isinstance(actions[t], dict) or set(actions[t]) != set(f) for t, f in REQUEST_TYPES.items())
            or any(not isinstance(w, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", w) for f in actions.values() for w in f.values())
            or any(len(set(f.values())) != len(f) for f in actions.values())):
        errors.append("action_fields must name one distinct wire field for every documented request field")
    floors = values["floor_fields"]
    if not isinstance(floors, dict) or set(floors) != set(PRODUCT_TYPES) or any(
            not isinstance(f, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", f) for f in floors.values()):
        errors.append("floor_fields must name a response field for every product type")
    goals = values["call_goals"]
    if (not isinstance(goals, dict) or goals.get("default") not in CODES["goals"]
            or sorted(goals.get("available", [])) != sorted(CODES["goals"])
            or any(not isinstance(goals.get(g), str) or not goals[g].strip() for g in CODES["goals"])):
        errors.append("call_goals needs a supported default, every goal available and a description per goal")
    bands = values["band_ranges"]
    if (values["business_weekdays"] != [0, 1, 2, 3, 4] or not isinstance(bands, list)
            or [b.get("name") for b in bands if isinstance(b, dict)] != ["early", "late"]
            or bands[0]["min"] != 1 or bands[1]["min"] != bands[0]["max"] + 1 or bands[1]["max"] <= bands[1]["min"]
            or set(values["deadline_days"]) != {"early", "late"} or set(values["persuasion_limits"]) != {"early", "late"}):
        errors.append("use the supported Monday-Friday week and two contiguous early/late bands with matching deadlines and limits")
    if (set(values["note_reasons"]) - set(codes["notes"].values()) or set(values["close_reasons"]) != set(codes["outcomes"].values())
            or any(set(r) - set(codes["reasons"].values()) for r in values["close_reasons"].values())
            or set(values["transfer_reasons"]) != set(codes["queues"].values())
            or set(values["transfer_reasons"].values()) - set(codes["reasons"].values())):
        errors.append("note, close and transfer bindings must use this bank's own API codes")
    if errors:
        return errors
    for dimension in dimensions:
        for exposed in exposed_profiles:
            if all(canonical_json(_semantic(k, values)) == canonical_json(_semantic(k, exposed)) for k in POLICY_DIMENSIONS[dimension]):
                errors.append(f"assigned dimension {dimension} does not differ from an exposed development profile")
                break
    representations = bank.get("representations")
    if not isinstance(representations, dict) or set(representations) != {"clean", "raw"}:
        return errors + ["bank needs exactly a clean and a raw representation"]
    texts = {}
    for name, rep in representations.items():
        documents = rep.get("documents") if isinstance(rep, dict) else None
        if (not isinstance(documents, dict) or not documents
                or any(not re.fullmatch(r"[A-Za-z0-9_-]+", d) or not isinstance(t, str) or LABEL not in t for d, t in documents.items())):
            errors.append(f"{name} documents must be named safely and each carry the synthetic label")
            continue
        texts[name] = {_norm(t) for t in documents.values()}
        citations, superseded = rep.get("citations"), rep.get("superseded")
        if not isinstance(citations, dict) or set(citations) != set(KINDS) or not isinstance(superseded, list):
            errors.append(f"{name} needs one citation per policy kind and a superseded list")
            continue
        cited, literal = set(), True
        for item in list(citations.values()) + superseded:
            if isinstance(item, dict) and item.get("document") in documents:
                cited.add(item["document"])
            if (not isinstance(item, dict) or item.get("document") not in documents or not isinstance(item.get("quote"), str)
                    or not item["quote"].strip() or item["quote"] not in documents[item["document"]]):
                literal = False
        if not literal:
            errors.append(f"{name} citations must quote their cited document literally")
        if set(documents) - cited:
            errors.append(f"{name} cites no rule from at least one supplied document")
        kinds = [item.get("kind") for item in superseded if isinstance(item, dict)]
        if (len(kinds) != len(set(kinds)) or set(kinds) - set(KINDS)
                or any(canonical_json(item.get("value")) == canonical_json(values.get(item.get("kind"))) for item in superseded)):
            errors.append(f"{name} superseded rules need one distinct kind each and a value that differs from the effective one")
        if name == "raw" and (len(documents) < 2 or not superseded):
            errors.append("raw representation needs several documents and at least one explicitly superseded rule")
        if name == "clean" and superseded:
            errors.append("clean representation states effective values only")
    if len(texts) == 2 and texts["clean"] & texts["raw"]:
        errors.append("clean and raw documents must be written independently")
    exposed_docs = {_norm(t) for p in exposed_profiles for t in p.get("_documents", ())}
    if any(t in exposed_docs for group in texts.values() for t in group):
        errors.append("a policy document repeats an exposed development document")
    return errors


def _local_case_errors(case, values, label):
    codes = values["wire_codes"]
    errors = []
    variables, products, expected = case.get("variables"), case.get("products"), case.get("expected")
    if (not isinstance(variables, dict) or set(variables) != VARIABLES or variables["call_goal"] not in codes["goals"].values()
            or _time(str(variables["now"]) + "+00:00") is None):
        errors.append(f"{label}: variables need exactly {sorted(VARIABLES)}, an ISO now and one of this bank's goal codes")
    if not isinstance(case.get("identity"), dict) or list(case["identity"]) != values["identity_fields"]:
        errors.append(f"{label}: identity must answer exactly the bank's identity_fields")
    ids = []
    if not isinstance(products, list) or not 1 <= len(products) <= 3:
        errors.append(f"{label}: one to three products are required")
    else:
        for product in products:
            if (not isinstance(product, dict) or product.get("type") not in PRODUCT_TYPES or not isinstance(product.get("product_id"), str)
                    or type(product.get("days_late")) is not int or not isinstance(product.get("past_due"), (int, float))
                    or not isinstance(product.get(values["floor_fields"].get(product.get("type"), "")), (int, float))):
                errors.append(f"{label}: each product needs id, supported type, past_due, days_late and its bank floor field")
                break
            ids.append(product["product_id"])
    if len(ids) != len(set(ids)):
        errors.append(f"{label}: product ids must be distinct")
    if not isinstance(expected, dict) or set(expected) != set(LEDGER_FIELDS):
        return errors + [f"{label}: expected ledger needs exactly {list(LEDGER_FIELDS)}"]
    if (expected["outcome"] not in codes["outcomes"].values()
            or expected["reason"] is not None and expected["reason"] not in codes["reasons"].values()
            or any(not isinstance(p, dict) or p.get("product_id") not in ids or not isinstance(p.get("amount"), (int, float))
                   or _time(str(p.get("date")) + "T00:00+00:00") is None for p in expected["promises"])
            or any(not isinstance(n, dict) or n.get("product_id") not in ids or n.get("reason") not in codes["notes"].values() for n in expected["notes"])
            or any(not isinstance(t, dict) or t.get("queue") not in codes["queues"].values() for t in expected["transfers"])
            or any(not isinstance(c, dict) or set(c) != {"date", "time"} for c in expected["callbacks"])):
        errors.append(f"{label}: expected ledger must use this bank's codes and the case's own products")
    helper = case.get("helper_expected")
    if not isinstance(helper, dict) or set(helper) != {"deadlines", "floors", "bands"} or any(
            not isinstance(helper[k], dict) or sorted(helper[k]) != sorted(ids) for k in helper):
        errors.append(f"{label}: helper_expected needs deadlines, floors and bands for every product")
    turns = case.get("customer_turns")
    if not isinstance(turns, list) or not turns or any(not isinstance(t, dict) or not str(t.get("when", "")).strip() or not str(t.get("say", "")).strip() for t in turns):
        errors.append(f"{label}: customer_turns need nonblank when/say entries")
    faults = case.get("faults")
    if not isinstance(faults, list) or any(not isinstance(f, dict) or f.get("tool") not in REQUEST_TYPES
                                           or type(f.get("fail_first_n")) is not int or f["fail_first_n"] < 1 for f in faults):
        errors.append(f"{label}: faults must be a list of documented tools with positive fail_first_n")
    for key in ("script", "description"):
        if not isinstance(case.get(key), str) or not case[key].strip():
            errors.append(f"{label}: {key} must be nonblank")
    if not isinstance(case.get("checks"), list) or not case["checks"] or any(not isinstance(c, str) or not c.strip() for c in case["checks"]):
        errors.append(f"{label}: checks must be nonblank text")
    tags = case.get("dimensions")
    if not isinstance(tags, list) or not tags or set(tags) - set(CASE_TAGS):
        errors.append(f"{label}: dimensions must be a nonempty subset of {list(CASE_TAGS)}")
    else:
        if "multi_product" in tags and len(ids) < 2: errors.append(f"{label}: multi_product needs several products")
        if "fault" in tags and not faults: errors.append(f"{label}: fault needs a declared fault")
        if "outcome_binding" in tags and isinstance(expected, dict) and expected["outcome"] == codes["outcomes"]["promise"] and not (expected["notes"] or expected["transfers"]):
            errors.append(f"{label}: outcome_binding needs a non-promise outcome, note or transfer")
    return errors


def build_rows(workspace, record, base_dir=BASE, spec_path=SPEC, now=None):
    """Validate one author's file and return (offline rows, profiles, hosted rows, errors)."""
    workspace = Path(workspace)
    spec, base = load_spec(spec_path), _base(base_dir)
    frozen = freeze.verify_freeze(record)
    assignment = read_json(workspace / "assignment.json")
    author_file = read_json(workspace / assignment.get("output", "author.json"))
    errors = []
    core = assignment_core(spec, assignment.get("author"), base)
    if {k: assignment.get(k) for k in core} != core:
        errors.append("assignment differs from the fan-out spec and reservations")
    if (assignment.get("freeze", {}).get("candidate_sha256") != frozen["candidate_sha256"]
            or assignment["freeze"].get("created_at") != frozen["created_at"]):
        errors.append("assignment is bound to a different freeze")
    prepared, frozen_at = _time(assignment.get("prepared_at")), _time(frozen["created_at"])
    if prepared is None or not frozen_at < prepared:
        errors.append("assignment was not prepared after the freeze")
    for item in assignment.get("inputs", []):
        copy = workspace / "inputs" / item["path"]
        if not copy.is_file() or _sha(copy) != item["sha256"] or _sha(ROOT / item["path"]) != item["sha256"]:
            errors.append("an approved input changed after preparation: " + item["path"])
    if [item.get("path") for item in assignment.get("inputs", [])] != spec["approved_inputs"]:
        errors.append("assignment inputs differ from the approved input list")
    if author_file.get("version") != 1 or author_file.get("author") != assignment.get("author"):
        return [], [], [], errors + ["author file must be version 1 and name the assigned author"]
    if errors:
        return [], [], [], errors
    author, now = assignment["author"], now or datetime.now(timezone.utc)
    exposed_values = [{**p["effective_values_expected"], "_documents": tuple(p["policy_documents"].values())}
                      for p in base["offline"]["profiles"]]
    exposed_rows = [r for r in base["offline"]["cases"] if r.get("split") != "holdout"]
    seeds = {seed["id"]: seed for seed in read_json(ROOT / "build-the-builder/public-test-set.json")}
    exposed_additions = set()
    for row in base["hosted"]["cases"]:
        if "case" in row and row["bank_case_id"] in seeds:
            exposed_additions |= {_norm(line) for line in row["case"]["script"][len(seeds[row["bank_case_id"]]["script"]):].splitlines() if line.strip()}
    try:
        from benchmarks.generate import COMPOUNDS
        exposed_additions |= {_norm(text) for text in COMPOUNDS}
    except ImportError:
        pass

    def stamp(case, label, oracle):
        authored = _time(case.get("authored_at"))
        if authored is None or not (frozen_at < authored and prepared <= authored <= now):
            errors.append(f"{label}: authored_at must be timezone-aware, after the freeze and preparation, and not in the future")
        return {"authored_at": case.get("authored_at"), "author": author, "candidate_sha256": frozen["candidate_sha256"],
                "oracle_sha256": digest(oracle), "isolation": dict(ISOLATION)}

    authoring = {"assignment": author, "spec_sha256": assignment["spec_sha256"], "prepared_at": assignment["prepared_at"],
                 "inputs_sha256": digest(assignment["inputs"])}

    def expand():
        # Local lane: one bank in two independently written representations.
        offline_rows, profiles = [], []
        bank = author_file.get("bank")
        bank_errors = _bank_errors(bank, assignment["policy_dimensions"], exposed_values) if isinstance(bank, dict) else ["bank is missing"]
        errors.extend(bank_errors)
        local_cases = author_file.get("local_cases")
        planned = {item["reservation"]: item for item in assignment["local"]}
        if not isinstance(local_cases, list) or any(not isinstance(c, dict) for c in local_cases) or sorted(str(c.get("reservation")) for c in local_cases) != sorted(planned):
            errors.append("local_cases must cover each assigned local reservation exactly once")
            local_cases = []
        if not bank_errors and _time(str(bank.get("now")) + "+00:00") is None:
            errors.append("bank now must be an ISO local date-time")
        if not bank_errors and not errors:
            api = _openapi(bank["effective_values"])
            profiles = [_profile(assignment["bank_id"], name, bank, bank["representations"][name], api) for name in ("clean", "raw")]
            tags = set()
            for case in local_cases:
                meta = planned[case["reservation"]]
                label = f"local {case['reservation']}"
                case_errors = _local_case_errors(case, bank["effective_values"], label)
                errors.extend(case_errors)
                if case_errors:
                    continue
                tags |= set(case["dimensions"])
                if _norm(case["script"]) in {_norm(r["script"]) for r in exposed_rows} or digest(case["customer_turns"]) in {digest(r["customer_turns"]) for r in exposed_rows}:
                    errors.append(f"{label}: script or customer turns repeat an exposed development case")
                row = {"archetype_id": meta["archetype_id"], "number": meta["number"], "bucket": meta["bucket"], "split": "holdout",
                       "description": case["description"], "fixture_id": f"t16-{author}-{meta['archetype_id']}",
                       "profile_id": profiles[0]["profile_id"], "paired_profile_id": profiles[1]["profile_id"],
                       "status": "materialized_after_freeze", "evaluation_kind": "offline_contract_and_ledger_oracle_not_Freya_conversation",
                       "label": LABEL, "variables": case["variables"], "identity": case["identity"], "products": case["products"],
                       "script": case["script"], "customer_turns": case["customer_turns"], "expected": case["expected"],
                       "wire_codes": bank["effective_values"]["wire_codes"], "contract_expected": profiles[0]["contract_expected"],
                       "helper_expected": case["helper_expected"], "checks": case["checks"], "faults": case["faults"],
                       "official_policy": False, "lineage": stamp(case, label, case["expected"]),
                       "authoring": {**authoring, "replaces": case["reservation"], "dimensions": case["dimensions"]}}
                row["authoring"].update(critical_reasons=critical_reasons(row, "offline"))
                row["authoring"]["critical"] = bool(row["authoring"]["critical_reasons"])
                offline_rows.append(row)
            if local_cases and set(REQUIRED_CASE_TAGS) - tags:
                errors.append("local cases must together exercise calendar, counters and outcome_binding")
        # Hosted lane: new caller wording on unchanged official seed facts and ledgers.
        hosted_rows, wording = [], set()
        hosted_cases = author_file.get("hosted_cases")
        planned_hosted = {item["reservation"]: item for item in assignment["hosted"]}
        reserved = _reservations(base, "hosted")
        if not isinstance(hosted_cases, list) or any(not isinstance(c, dict) for c in hosted_cases) or sorted(str(c.get("reservation")) for c in hosted_cases) != sorted(planned_hosted):
            errors.append("hosted_cases must cover each assigned hosted reservation exactly once")
            hosted_cases = []
        for case in hosted_cases:
            meta, label = planned_hosted[case["reservation"]], f"hosted {case['reservation']}"
            seed = seeds[meta["bank_case_id"]]
            script, dims = case.get("script"), case.get("dimensions")
            addition = script[len(seed["script"]):] if isinstance(script, str) and script.startswith(seed["script"]) else ""
            if len(addition.strip()) < 40:
                errors.append(f"{label}: script must start with the official seed script verbatim and add at least 40 characters of new caller behaviour")
                continue
            if not isinstance(dims, list) or set(dims) - set(WORDING_DIMENSIONS) or meta["required_dimension"] not in dims:
                errors.append(f"{label}: dimensions must include {meta['required_dimension']} and use only {list(WORDING_DIMENSIONS)}")
                continue
            if any(_norm(line) in exposed_additions for line in addition.splitlines() if line.strip()):
                errors.append(f"{label}: added caller behaviour repeats exposed development or template wording")
            extra = case.get("extra_checks", [])
            if not isinstance(extra, list) or any(not isinstance(c, str) or not c.strip() for c in extra):
                errors.append(f"{label}: extra_checks must be nonblank text")
                continue
            wording |= set(dims)
            body = deepcopy(seed)
            body.update(id=case["reservation"], script=script, checks=list(seed.get("checks", [])) + extra)
            if isinstance(case.get("title"), str) and case["title"].strip():
                body["title"] = case["title"]
            source = reserved[case["reservation"]]
            row = {**{k: source[k] for k in ("fixture_id", "bank_case_id", "split", "brief", "style", "claim")},
                   "oracle_audit": "official_seed_ledger_unchanged; independently_authored_caller_wording; persona_behavior_not_executed",
                   "status": "materialized_after_freeze", "case": body, "lineage": stamp(case, label, seed["expected"]),
                   "authoring": {**authoring, "replaces": case["reservation"], "dimensions": dims}}
            if row["style"] == "compound":
                row["outcome_constraints"] = seed_outcome_constraints(seed)
            row["authoring"].update(critical_reasons=critical_reasons(row, "hosted"))
            row["authoring"]["critical"] = bool(row["authoring"]["critical_reasons"])
            hosted_rows.append(row)
        if hosted_cases and set(WORDING_DIMENSIONS) - wording:
            errors.append("hosted cases must together exercise paraphrase, corrections, interruptions and compound")
        return offline_rows, profiles, hosted_rows

    try:
        offline_rows, profiles, hosted_rows = expand()
    except (KeyError, TypeError, AttributeError, IndexError, ValueError) as exc:
        return [], [], [], errors + ["author file structure is malformed (" + type(exc).__name__ + "); see the protocol's author file format"]
    if not errors:
        try:
            for kind, rows in (("offline", offline_rows), ("hosted", hosted_rows)):
                freeze.verify_holdout_manifest({"kind": kind, "cases": rows,
                                                "freeze": {"record": str(Path(record).resolve()), "candidate_sha256": frozen["candidate_sha256"]}})
            validate_seed_rows(hosted_rows)
        except ValueError as exc:
            errors.append(str(exc))
    return offline_rows, profiles, hosted_rows, errors


def assemble(record, workspaces, out_dir, base_dir=BASE, spec_path=SPEC):
    """Write offline.json and hosted.json packs in which authored rows replace all 60 + 60 reservations."""
    frozen = freeze.verify_freeze(record)
    out_dir = freeze._outside_candidate(out_dir, frozen["candidate_root"], "holdout pack directory")
    if out_dir.exists():
        raise ValueError("holdout pack directory already exists; never overwrite an authored pack")
    spec, base = load_spec(spec_path), _base(base_dir)
    offline_rows, profiles, hosted_rows, errors, seen = [], [], [], [], set()
    for workspace in workspaces:
        rows, banks, hosted, problems = build_rows(workspace, record, base_dir, spec_path)
        errors += [f"{Path(workspace).name}: {p}" for p in problems]
        offline_rows += rows
        profiles += banks
        hosted_rows += hosted
    for text in ([_norm(t) for p in profiles for t in p["policy_documents"].values()] + [_norm(r["script"]) for r in offline_rows]
                 + [_norm(r["case"]["script"]) for r in hosted_rows]):
        if text in seen:
            errors.append("two authored documents or scripts are identical across authors")
        seen.add(text)
    if (sorted(r["authoring"]["replaces"] for r in offline_rows) != sorted(_reservations(base, "offline"))
            or sorted(r["authoring"]["replaces"] for r in hosted_rows) != sorted(_reservations(base, "hosted"))
            or sorted({r["authoring"]["assignment"] for r in offline_rows + hosted_rows}) != sorted(a["id"] for a in spec["authors"])):
        errors.append("authored rows must replace every reservation exactly once, from every assigned author")
    if errors:
        raise ValueError("\n".join(errors))
    binding = {"record": str(Path(record).resolve()), "candidate_sha256": frozen["candidate_sha256"],
               "frozen_at": frozen["created_at"], "status": "unevaluated",
               "claim": "Independently authored after the recorded freeze; not evaluated."}
    packs = {}
    for kind, rows in (("offline", offline_rows), ("hosted", hosted_rows)):
        pack = deepcopy(base[kind])
        pack["cases"] = [r for r in pack["cases"] if r.get("split") != "holdout"] + rows
        if kind == "offline":
            pack["profiles"] = pack["profiles"] + profiles
        pack["count"] = len(pack["cases"])
        pack["split_counts"] = {s: sum(r["split"] == s for r in pack["cases"]) for s in ("train", "validation", "holdout")}
        pack["freeze"] = dict(binding)
        inventory = freeze._manifest_inventory(pack, kind)
        if inventory["materialized_holdout_rows"] != 60 or inventory["reserved_holdout_rows"]:
            raise ValueError(f"{kind} pack does not contain 60 authored holdouts")
        freeze.verify_holdout_manifest(pack)
        packs[kind] = json.dumps(pack, indent=2, allow_nan=False) + "\n"
    validate_seed_rows(hosted_rows)
    out_dir.mkdir(parents=True)
    summary = {}
    for kind, text in packs.items():
        with (out_dir / (kind + ".json")).open("x", encoding="utf-8") as out:
            out.write(text)
        summary[kind] = {"holdout_rows": 60, "manifest_sha256": hashlib.sha256(text.encode()).hexdigest()}
    return {**summary, "candidate_sha256": frozen["candidate_sha256"], "acceptance": False,
            "claim": "Authored packs only; nothing evaluated."}


def _repetition(passed, categories):
    if passed: return "pass"
    return "incomplete" if set(categories) <= {"infrastructure", "evidence"} else "fail"


def account(pack_dir, hosted_results=(), local_results=(), contracts=()):
    """Coverage and scores; policy adaptation (local) and wording robustness (hosted) are never combined."""
    from benchmarks.offline import compare_contract
    packs = {kind: read_json(Path(pack_dir) / (kind + ".json")) for kind in ("offline", "hosted")}
    blind = {}
    for kind, pack in packs.items():
        try:
            freeze.verify_holdout_manifest(pack)
            blind[kind] = True
        except ValueError as exc:
            blind[kind] = "not blind: " + str(exc)
    rows = {kind: {r["fixture_id"]: r for r in pack["cases"] if r.get("split") == "holdout"} for kind, pack in packs.items()}
    if any(str(r.get("status")).startswith("reserved") for table in rows.values() for r in table.values()):
        raise ValueError("pack still contains reservations; account only an assembled authored pack")
    authored = sorted({r["profile_id"] for r in rows["offline"].values()} | {r["paired_profile_id"] for r in rows["offline"].values()})
    profiles = {p["profile_id"]: p for p in packs["offline"]["profiles"]}
    reps = {kind: {fid: [] for fid in table} for kind, table in rows.items()}
    for path in hosted_results:
        data = read_json(path)
        pin = data.get("deployment_pin", {})
        pinned = pin.get("verified") is True and pin.get("per_call_version_verified") is True
        for rec in data.get("rows", []):
            row = rows["hosted"].get(rec.get("fixture_id"))
            if row is None or rec.get("bank_case_id", row["bank_case_id"]) != row["bank_case_id"]:
                raise ValueError("hosted result does not belong to this pack: " + str(rec.get("fixture_id")))
            categories = [f.get("category") for f in rec.get("failures", [])] + ([] if pinned else ["evidence"])
            reps["hosted"][row["fixture_id"]].append(("hosted", _repetition(rec.get("pass") is True and pinned, categories)))
    for path in local_results:
        data = read_json(path)
        row = rows["offline"].get(data.get("fixture_id"))
        if row is None or data.get("profile_id") not in (row["profile_id"], row.get("paired_profile_id")):
            raise ValueError("local result does not belong to this pack: " + str(data.get("fixture_id")))
        if data.get("fixture_sha256") != digest(row) or data.get("profile_sha256") != digest(profiles[data["profile_id"]]):
            raise ValueError("local result is bound to different case or profile bytes: " + row["fixture_id"])
        verdict = data.get("ledger_verdict", {})
        representation = "clean" if data["profile_id"] == row["profile_id"] else "raw"
        reps["offline"][row["fixture_id"]].append((representation, _repetition(verdict.get("pass") is True, [f.get("category") for f in verdict.get("failures", [])])))
    result = {}
    for kind, lane in (("offline", "policy_adaptation"), ("hosted", "wording_robustness")):
        table = []
        for fid, row in sorted(rows[kind].items()):
            reasons = critical_reasons(row, kind)
            needed = 3 if kind == "hosted" or reasons else 1
            groups = ("hosted",) if kind == "hosted" else ("clean", "raw")
            outcomes = [o for _, o in reps[kind][fid]]
            passes = {g: sum(1 for k, o in reps[kind][fid] if k == g and o == "pass") for g in groups}
            status = ("fail" if "fail" in outcomes else "pass" if all(n >= needed for n in passes.values())
                      else "uncovered" if not outcomes else "incomplete")
            table.append({"fixture_id": fid, "critical": bool(reasons), "critical_reasons": reasons,
                          "required_repetitions": {g: needed for g in groups}, "passing_repetitions": passes,
                          "incomplete_repetitions": outcomes.count("incomplete"), "failing_repetitions": outcomes.count("fail"),
                          "status": status, **({"brief": row["brief"]} if kind == "hosted" else {})})
        summary = {s: sum(r["status"] == s for r in table) for s in ("pass", "fail", "incomplete", "uncovered")}
        critical = [r for r in table if r["critical"]]
        result[lane] = {"lane": "local" if kind == "offline" else "hosted", "cases": len(table), **summary,
                        "critical_cases": len(critical), "critical_passing_all_repetitions": sum(r["status"] == "pass" for r in critical),
                        "blind": blind[kind], "rows": table}
    compilation = {}
    for item in contracts:
        profile_id, _, path = item.partition("=")
        if profile_id not in authored:
            raise ValueError("candidate contract names a profile outside the authored holdouts: " + profile_id)
        compilation[profile_id] = compare_contract(Path(path).read_text(encoding="utf-8"), profiles[profile_id])
    result["policy_adaptation"]["compilation"] = {
        "profiles": len(authored), "matched": sum(not compilation.get(p, [None]) for p in authored),
        "mismatched": sorted(p for p in authored if compilation.get(p)), "missing": sorted(p for p in authored if p not in compilation)}
    result["claim"] = ("Local policy adaptation and hosted wording robustness are separate claims; no combined score. "
                       "Unrun or incomplete cases are never passes; failures stay in the denominator.")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("check-spec", help="Validate the fan-out spec against the current reservations (no freeze needed)")
    plan.add_argument("--spec", default=str(SPEC))
    plan.add_argument("--fixtures-dir", default=str(BASE))
    prep = sub.add_parser("prepare", help="Create one isolated author workspace after the freeze")
    prep.add_argument("--author", required=True)
    check = sub.add_parser("validate", help="Validate one author's file in its workspace")
    pack = sub.add_parser("assemble", help="Write the authored holdout packs")
    pack.add_argument("--out-dir", required=True)
    for command in (prep, check, pack):
        command.add_argument("--record", required=True)
        command.add_argument("--workspace", required=True, action="append" if command is pack else "store")
        command.add_argument("--spec", default=str(SPEC))
        command.add_argument("--fixtures-dir", default=str(BASE))
    tally = sub.add_parser("account", help="Coverage accounting for an evaluated pack")
    tally.add_argument("--pack-dir", required=True)
    tally.add_argument("--hosted-results", action="append", default=[])
    tally.add_argument("--local-result", action="append", default=[])
    tally.add_argument("--candidate-contract", action="append", default=[], metavar="PROFILE_ID=policy.json")
    args = parser.parse_args(argv)
    if args.command == "check-spec":
        spec, base = load_spec(args.spec), _base(args.fixtures_dir)
        cores = [assignment_core(spec, a["id"], base) for a in spec["authors"]]
        print(json.dumps({"authors": len(cores), "local_reservations": sum(len(c["local"]) for c in cores),
                          "hosted_reservations": sum(len(c["hosted"]) for c in cores), "spec_sha256": digest(spec)}))
    elif args.command == "prepare":
        print(json.dumps(prepare(args.spec, args.author, args.record, args.workspace, args.fixtures_dir)["inputs"]))
    elif args.command == "validate":
        offline_rows, _, hosted_rows, errors = build_rows(args.workspace, args.record, args.fixtures_dir, args.spec)
        print(json.dumps({"local_rows": len(offline_rows), "hosted_rows": len(hosted_rows), "errors": errors}, indent=2))
        return 1 if errors else 0
    elif args.command == "assemble":
        print(json.dumps(assemble(args.record, args.workspace, args.out_dir, args.fixtures_dir, args.spec), indent=2))
    else:
        print(json.dumps(account(args.pack_dir, args.hosted_results, args.local_result, args.candidate_contract), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

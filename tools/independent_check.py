#!/usr/bin/env python3
"""Independent check of a phone agent on the final case: our own reading of the bank's documents, not the builder's.

    python3 tools/case_folder.py <case folder or .zip> <bundle>       # the documents as text, once
    export ANTHROPIC_API_KEY=...                                       # never print it; needs the anthropic SDK 1.x
    python3 tools/independent_check.py rules <bundle> --out <dir>      # 1. rules.json + rules.md
    python3 tools/independent_check.py cases <bundle> --out <dir>      # 2. cases.json + cases.md (reads <dir>/rules.json)
    FREYA_BANK_HOST=<mock url> python3 build-the-builder/tools/run_cases.py <agent_id> --cases <dir>/cases.json --out <dir>/run.json
    python3 tools/independent_check.py grade <bundle> --out <dir> --run <dir>/run.json --mock-url <mock url> \
        [--cases <dir>/cases.json] [--env-file <main checkout>/.env]  # 3 + 4. report.md + report.json

1. rules: a numbered checklist of every rule in the documents, each with an exact quote; code marks every quote it
   cannot find verbatim. 2. cases: for every test customer, six scenarios (cooperative, wrong_answer, third_party,
   pressure, injection, correction) in the public-test-set.json format: the expected bank records and the judge's
   checks, each check citing its rule. A case id is <customer id>-<scenario> and `customer` names the test customer,
   so the mock can map a case to its customer. 3. grade: every call of a run_cases.py result file, from its
   transcript (GET /api/v2/call/{callId}) and the mock's records for the customer (GET <mock>/v1/practice/ledger/<id>,
   as build-the-builder/tools/score.py reads them): outcome (the kit's score_ledger against our expected records, and
   Claude), policy per rule, honesty, and flow similarity to the example conversations. Without --cases, calls are
   graded on the rules alone. 4. report.md and report.json: per check, per rule and per case, worst violations first.
   A call passes only when outcome, policy and honesty all pass. rules.md and cases.md are our reading, not answers:
   read them by hand before trusting a grade.

Independence: it reads only the case documents (a tools/case_folder.py bundle, or a folder of text files) and, when
grading, the transcripts without workflow internals (no code steps, routing or node names) and the mock's records.
It never reads the builder's workflow, prompts or self-test cases, and it ignores the pass/fail fields of the run
file. Claude: claude-opus-5-5 at effort max with server-side refusal fallback; prompts in independent_check_prompts.md.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "build-the-builder"
sys.path.insert(0, str(ROOT))
sys.path += [str(KIT), str(KIT / "tools")]   # the kit's freya.py and score.py
MODEL = "claude-opus-5-5"
SCENARIOS = ("cooperative", "wrong_answer", "third_party", "pressure", "injection", "correction")
TEXT = {".md", ".markdown", ".txt", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml", ".svg", ".html", ".htm", ".mmd", ".drawio"}
IMAGE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
CACHE = {"type": "ephemeral", "ttl": "1h"}   # the documents and the checklist are the same prefix for every call
WORKERS = 6
DATE, TIME = re.compile(r"\d{4}-\d{2}-\d{2}"), re.compile(r"\d{2}:\d{2}")
QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "–": "-", "—": "-"})
SHAPE = {"id": str, "customer": str, "scenario": str, "brief": str, "family": str, "level": str, "title": str,
         "script": str, "variables": dict, "identity": dict, "products": list, "expected": dict, "checks": list}
PROMPTS = {part.split("\n", 1)[0].strip(): part.split("\n", 1)[1].strip()
           for part in Path(__file__).with_name("independent_check_prompts.md").read_text(encoding="utf-8").split("\n## ")[1:]}


def norm(text):
    text = unicodedata.normalize("NFKC", text).translate(QUOTES).casefold()
    return " ".join(re.sub(r"[*_`#>|\\]", " ", text).split())


def found(quote, docs_norm):
    parts = [norm(p) for p in re.split(r"\.\.\.|…", quote or "")]
    return any(parts) and all(p in docs_norm for p in parts if p)


def load_bundle(path):
    """The case documents in reading order -> (content blocks for Claude, normalised text, documents not read)."""
    path = Path(path)
    if (path / "manifest.json").is_file():   # a tools/case_folder.py bundle: text versions, images, original PDFs
        docs = [(e["source"], e.get("readme") or "", e["text"] + e["images"] + [f for f in e["files"] if f.lower().endswith(".pdf")])
                for e in json.loads((path / "manifest.json").read_text(encoding="utf-8"))]
    else:
        names = sorted((p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file() and not p.name.startswith(".")),
                       key=lambda n: (n.lower() != "brief.md", n.lower() != "readme.md", n))
        docs = [(n, "", [n]) for n in names]
    blocks, texts, skipped = [], [], []
    for source, readme, rels in docs:
        used = False
        for rel in rels:
            ext, file = Path(rel).suffix.lower(), path / rel
            if ext in TEXT:
                texts.append(f"=== DOCUMENT: {source} ({rel}){' - README: ' + readme if readme else ''} ===\n"
                             f"{file.read_text(encoding='utf-8', errors='replace').strip()}\n")
            elif (ext in IMAGE or ext == ".pdf") and file.stat().st_size <= 5_000_000:
                data = base64.b64encode(file.read_bytes()).decode()
                kind, media = ("document", "application/pdf") if ext == ".pdf" else ("image", IMAGE[ext])
                blocks += [{"type": "text", "text": f"Attached: {source} ({rel})"},
                           {"type": kind, "source": {"type": "base64", "media_type": media, "data": data}}]
            else:
                continue
            used = True
        if not used:
            skipped.append(source)
    if not texts:
        raise SystemExit(f"no readable documents in {path}")
    text = "\n".join(texts)
    return blocks + [{"type": "text", "text": "THE CASE DOCUMENTS\n\n" + text, "cache_control": CACHE}], norm(text), skipped


def ask(client, docs, task, extra=(), data="", check=lambda out: []):
    """One Claude task -> (JSON object, problems left). A reply that is not JSON or fails `check` goes back once."""
    content = [*docs, *({"type": "text", "text": t, "cache_control": CACHE} for t in extra),
               {"type": "text", "text": PROMPTS[task] + data}]
    messages, out, problems = [{"role": "user", "content": content}], None, []
    for _ in range(2):
        with client.beta.messages.stream(model=MODEL, max_tokens=128000, system=PROMPTS["system"], messages=messages,
                                         output_config={"effort": "max"}, betas=["server-side-fallback-2026-07-01"],
                                         fallbacks="default") as stream:
            reply = stream.get_final_message()
        if reply.stop_reason == "refusal":
            raise RuntimeError(f"{task}: Claude declined the request")
        text = "".join(b.text for b in reply.content if b.type == "text")
        try:
            out = json.loads(text[text.index("{"):text.rindex("}") + 1])
            problems = check(out)
        except (ValueError, LookupError, TypeError, AttributeError) as e:
            out, problems = None, [f"the reply is not one JSON object in the asked shape ({type(e).__name__}: {e})"]
        if not problems:
            return out, []
        messages += [{"role": "assistant", "content": reply.content},
                     {"role": "user", "content": "Fix these problems and reply with the whole JSON object again:\n- " + "\n- ".join(problems[:40])}]
    if out is None:
        raise RuntimeError(f"{task}: {problems[0]}")
    return out, problems


def cell(value):
    return " ".join(str("" if value is None else value).split()).replace("|", "\\|")


def row(*values):
    return "| " + " | ".join(cell(v) for v in values) + " |"


def write(folder, name, body):
    path = Path(folder) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body if isinstance(body, str) else json.dumps(body, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def checklist(rules):
    return "RULE CHECKLIST (our independent reading of the documents):\n" + json.dumps(rules["rules"], indent=1, ensure_ascii=False)


def rule_problems(out, docs_norm):
    ids = [r["id"] for r in out["rules"]]
    problems = [] if ids and len(set(ids)) == len(ids) else ["rule ids must be present and unique"]
    for r in out["rules"]:
        missing = [k for k in ("rule", "check", "source", "quote") if not r.get(k)]
        if missing:
            problems.append(f"{r['id']}: missing {', '.join(missing)}")
        elif not found(r["quote"], docs_norm):
            problems.append(f"{r['id']}: the quote is not verbatim in the documents: {r['quote'][:150]!r}")
    return problems


def cmd_rules(a, client):
    docs, docs_norm, skipped = load_bundle(a.bundle)
    out, problems = ask(client, docs, "rules", check=lambda o: rule_problems(o, docs_norm))
    for r in out["rules"]:
        r["quote_found"] = found(r.get("quote"), docs_norm)
    out.update(bundle=str(a.bundle), model=MODEL, documents_not_read=skipped, problems_left=problems)
    write(a.out, "rules.json", out)
    md = ["# Rule checklist (independent reading)", "", f"From `{a.bundle}` by {MODEL} (effort max). Check every rule "
          "against its quote. Found = NO: code could not find the quote verbatim, so check that rule by hand.", "",
          "| Rule | Category | Rule | Applies when | Source | Quote | Replaces | Found |", "|---|---|---|---|---|---|---|---|"]
    md += [row(r["id"], r.get("category"), r.get("rule"), r.get("applies_when"), r.get("source"), r.get("quote"), r.get("replaces"),
               "yes" if r["quote_found"] else "NO") for r in out["rules"]]
    md += ["", "## Open questions", "", *(f"- {q}" for q in out.get("open_questions") or ["(none)"])]
    md += ["", "## Documents not read", "", *(f"- {s}" for s in skipped or ["(none)"])]
    write(a.out, "rules.md", "\n".join(md) + "\n")
    print(f"{len(out['rules'])} rules, {sum(not r['quote_found'] for r in out['rules'])} quotes not found -> {Path(a.out) / 'rules.md'}")


def case_problems(c, rule_ids):
    problems = [f"{k} is not a {t.__name__}" for k, t in SHAPE.items() if not isinstance(c.get(k), t)]
    if problems:
        return problems
    e, v = c["expected"], c["variables"]
    problems += [f"variables.{k} is missing" for k in ("first_name", "last_name") if not v.get(k)]
    problems += [f"expected.{k} is not a list" for k in ("promises", "notes", "callbacks", "transfers") if not isinstance(e.get(k), list)]
    if problems:
        return problems
    if not e.get("outcome") or not isinstance(e["outcome"], str):
        problems.append("expected.outcome is missing")
    problems += [f"promise {p} needs product_id, a numeric amount and a YYYY-MM-DD date" for p in e["promises"]
                 if not (isinstance(p, dict) and p.get("product_id") and isinstance(p.get("amount"), (int, float))
                         and DATE.fullmatch(str(p.get("date"))))]
    problems += [f"note {n} needs a reason" for n in e["notes"] if not (isinstance(n, dict) and n.get("reason"))]
    problems += [f"callback {x} needs a YYYY-MM-DD date and an HH:MM time" for x in e["callbacks"]
                 if not (isinstance(x, dict) and DATE.fullmatch(str(x.get("date"))) and TIME.fullmatch(str(x.get("time"))))]
    problems += [f"transfer {t} needs a queue" for t in e["transfers"] if not (isinstance(t, dict) and t.get("queue"))]
    if not c["checks"] or not all(isinstance(x, str) and x.strip() for x in c["checks"]):
        problems.append("checks must be non-empty strings")
    problems += [f"a check cites unknown rule {r}" for r in sorted(set(re.findall(r"\[(R\d+)\]", " ".join(map(str, c["checks"])))) - rule_ids)]
    if c["scenario"] not in SCENARIOS:
        problems.append(f"unknown scenario {c['scenario']}")
    if (c["scenario"] == "third_party") != (c.get("third_party") is True or c["family"] == "Third party"):
        problems.append("third_party must be true exactly when the scenario is third_party (run_cases.py builds the persona from it)")
    return problems


def cases_problems(out, customer, rule_ids):
    problems = [f"{c.get('id')}: {p}" for c in out["cases"] for p in case_problems(c, rule_ids)]
    problems += [f"{c.get('id')}: customer must be {customer}" for c in out["cases"] if c.get("customer") != customer]
    missing = set(SCENARIOS) - {c.get("scenario") for c in out["cases"]}
    return problems + ([f"no case for scenarios {sorted(missing)}"] if missing else [])


def cmd_cases(a, client):
    docs, _, _ = load_bundle(a.bundle)
    rules = json.loads(Path(a.rules or Path(a.out) / "rules.json").read_text(encoding="utf-8"))
    rule_ids = {r["id"] for r in rules["rules"]}
    found_customers, _ = ask(client, docs, "customers",
                             check=lambda o: [] if o["customers"] and all(c.get("id") for c in o["customers"]) else ["every customer needs an id"])

    def one(customer):
        data = f"\n\nCALL DATE FROM THE DOCUMENTS: {found_customers.get('call_date')}\n\nTEST CUSTOMER:\n" + json.dumps(customer, indent=1, ensure_ascii=False)
        out, problems = ask(client, docs, "cases", [checklist(rules)], data, lambda o: cases_problems(o, str(customer["id"]), rule_ids))
        return [c for c in out["cases"] if isinstance(c, dict) and not case_problems(c, rule_ids) and c["customer"] == str(customer["id"])], problems

    with ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(one, found_customers["customers"]))
    cases, seen = [], set()
    for (good, problems), customer in zip(results, found_customers["customers"]):
        for p in problems:
            print(f"warning: {customer['id']}: {p} (invalid cases are left out)", file=sys.stderr)
        cases += [c for c in good if c["id"] not in seen and not seen.add(c["id"])]
    write(a.out, "customers.json", found_customers)
    write(a.out, "cases.json", cases)
    md = ["# Scenario cases (independent reading)", "", f"{len(cases)} cases for {len(found_customers['customers'])} test customers "
          f"from `{a.bundle}` by {MODEL} (effort max). The expected records and checks are our reading of the rules, not "
          "answers: correct them by hand before trusting a grade.", ""]
    for c in cases:
        e = c["expected"]
        md += [f"## {c['id']}: {c['title']}", "", f"- scenario: {c['scenario']}; now: {c['variables'].get('now')}; rules: {', '.join(c.get('rule_ids') or [])}",
               f"- script: {c['script']}", f"- expected outcome: {e['outcome']} (reason: {e.get('reason')})",
               *(f"- expected {k}: {json.dumps(x, ensure_ascii=False)}" for k, x in e.items() if k not in ("outcome", "reason") and x),
               "- checks:", *(f"  - {x}" for x in c["checks"]), *(f"- assumption: {x}" for x in c.get("assumptions") or []), ""]
    write(a.out, "cases.md", "\n".join(md))
    print(f"{len(cases)} cases -> {Path(a.out) / 'cases.json'}; review {Path(a.out) / 'cases.md'}")


def transcript(call):
    """What the agent and the customer said and every bank API call; code steps, routing and node names are left out."""
    lines = []
    for m in call.get("messages") or []:
        t, role = m.get("secondsFromStart") or 0, m.get("role")
        if role in ("assistant", "user"):
            lines.append(f"[{t:5.1f}s] {'AGENT' if role == 'assistant' else 'CUSTOMER'}: {m.get('message') or ''}")
            continue
        try:
            e = json.loads(m.get("message") or "")
        except ValueError:
            continue
        if not isinstance(e, dict) or e.get("function") in (None, "code_exec"):
            continue
        if e.get("event") == "function_call_start":
            args = e.get("args") if isinstance(e.get("args"), dict) else {}
            lines.append(f"[{t:5.1f}s] TOOL CALL {e['function']} {urlparse(args.get('url') or '').path} "
                         f"{json.dumps(args.get('body', args), ensure_ascii=False)[:1500]}")
        elif e.get("event") == "function_call_complete":
            r = e.get("result") if isinstance(e.get("result"), dict) else {}
            lines.append(f"[{t:5.1f}s] TOOL RESULT {e['function']} {r.get('status_code', '')} "
                         f"{json.dumps(r.get('data', r.get('message')), ensure_ascii=False)[:2000]}")
    return "\n".join(lines + [f"(call ended: {call.get('endedReason')})"])


def fetch_call(call_id):
    import freya
    return freya.get_call(call_id)


def fetch_records(mock_url, customer_id):
    import score
    score.HOST = mock_url.rstrip("/")
    return score.http("/v1/practice/ledger/" + customer_id)


def records_check(case, records):
    """The kit's own ledger comparison against our expected records; pass is None when there is nothing to compare."""
    if isinstance(records, dict) and "error" in records:
        return {"pass": None, "failed": [f"records not read: {records['error']}"]}
    if case is None:
        return {"pass": None, "failed": []}
    import score
    try:
        return score.score_ledger(case, records)
    except (LookupError, TypeError, ValueError, AttributeError) as e:
        return {"pass": None, "failed": [f"records not comparable by code ({type(e).__name__}: {e})"]}


def grade_problems(g):
    problems = [f"{k}.pass must be true or false" for k in ("outcome", "policy", "honesty") if not isinstance(g[k].get("pass"), bool)]
    return problems + ([] if isinstance(g.get("violations"), list) else ["violations must be a list"])


def load_env(path):
    from builder.config import read_local_environment
    for key, value in read_local_environment(Path(path)).items():   # one workspace, one key: never FREYA_API_KEY_2
        if key in ("FREYA_API_KEY", "FREYA_API_URL", "FREYA_BANK_TEAM_KEY"):
            os.environ.setdefault(key, value)


def grade_call(client, docs, rules, case, rep, r, call, records):
    det = records_check(case, records)
    res = {"case": r["id"], "rep": rep, "call_id": r.get("call_id"), "customer_id": r.get("customer_id"),
           "scenario": (case or {}).get("scenario"), "records_check": det, "records": records,
           "transcript": transcript(call) if call.get("messages") else ""}
    if not res["transcript"]:
        res["grade"] = {"error": call.get("error") or "no transcript: the call did not start"}
    else:
        data = ("\n\nTEST CASE:\n" + (json.dumps(case, indent=1, ensure_ascii=False) if case else "(none: grade on the rules alone)")
                + "\n\nTRANSCRIPT:\n" + res["transcript"] + "\n\nBANK MOCK RECORDS AFTER THE CALL:\n"
                + json.dumps(records, indent=1, ensure_ascii=False) + "\n\nRECORDS COMPARED WITH THE EXPECTED RECORDS BY CODE:\n" + json.dumps(det))
        try:
            res["grade"] = ask(client, docs, "grade", [checklist(rules)], data, grade_problems)[0]
        except Exception as e:   # one failed grading must not lose the others
            res["grade"] = {"error": f"{type(e).__name__}: {e}"}
    g = res["grade"]
    claude_outcome = (g.get("outcome") or {}).get("pass") is True
    res.update(outcome=claude_outcome and det["pass"] is not False, policy=(g.get("policy") or {}).get("pass") is True,
               honesty=(g.get("honesty") or {}).get("pass") is True)
    res["pass"] = res["outcome"] and res["policy"] and res["honesty"]
    res["review"] = [x for x in (g.get("error"), g.get("expected_not_applicable"),
                                 det["pass"] is not None and det["pass"] != claude_outcome and "the code's records check and Claude disagree on the outcome") if x]
    return res


def cmd_grade(a, client):
    docs, _, _ = load_bundle(a.bundle)
    rules = json.loads(Path(a.rules or Path(a.out) / "rules.json").read_text(encoding="utf-8"))
    cases = {c["id"]: c for c in json.loads(Path(a.cases).read_text(encoding="utf-8"))} if a.cases else {}
    if any("scenario" not in c or "customer" not in c for c in cases.values()):
        raise SystemExit("--cases must be a file written by `independent_check.py cases`, never the builder's own test cases")
    run = json.loads(Path(a.run).read_text(encoding="utf-8"))
    rows = [(rep, r) for rep, reps in enumerate(run.get("all_rows") or [run["rows"]], 1) for r in reps]
    load_env(a.env_file)
    missing = [k for k in ("FREYA_API_KEY", "FREYA_BANK_TEAM_KEY") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"{', '.join(missing)} not set: export them or pass --env-file")
    fetched = []
    for rep, r in rows:   # one plain GET at a time per call and per customer: reading results, never a load
        try:
            call = fetch_call(r["call_id"]) if r.get("call_id") else {}
        except Exception as e:
            call = {"error": f"transcript not read: {e}"}
        try:
            records = fetch_records(a.mock_url, r["customer_id"])
        except Exception as e:
            records = {"error": f"{type(e).__name__}: {e}"}
        fetched.append((cases.get(r["id"]), rep, r, call, records))
    with ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(lambda f: grade_call(client, docs, rules, *f), fetched))
    for res in results:
        write(a.out, f"transcripts/{res['case']}-r{res['rep']}.txt", res["transcript"] + "\n")
    out = report(results, rules, run, a)
    write(a.out, "report.json", out)
    write(a.out, "report.md", markdown(out))
    print(f"{out['summary']['calls_passed']} / {len(results)} calls pass; worst first in {Path(a.out) / 'report.md'}")


def severity(v):
    return v.get("severity") if isinstance(v.get("severity"), (int, float)) else 0


def report(results, rules, run, a):
    text = {r["id"]: r["rule"] for r in rules["rules"]}
    per_rule, by_case = {}, {}
    for res in results:
        by_case.setdefault(res["case"], []).append(res["pass"])
        for v in (res["grade"].get("policy") or {}).get("rules") or []:
            s = per_rule.setdefault(v.get("rule"), {"rule": v.get("rule"), "text": text.get(v.get("rule"), ""), "applied": 0, "failed": 0, "failing_calls": []})
            s["applied"] += 1
            if v.get("verdict") == "fail":
                s["failed"] += 1
                s["failing_calls"].append(f"{res['case']} r{res['rep']}")
    violations = [{"case": res["case"], "rep": res["rep"], **v} for res in results for v in res["grade"].get("violations") or []]
    violations += [{"case": res["case"], "rep": res["rep"], "severity": 5, "check": "outcome", "rule": None, "what": f,
                    "evidence": "bank mock records, compared by code"} for res in results if res["records_check"]["pass"] is False
                   for f in res["records_check"]["failed"]]
    flows = [(res["grade"].get("flow") or {}).get("similarity") for res in results]
    flows = [f for f in flows if isinstance(f, (int, float))]
    return {"summary": {"run": str(a.run), "agent_id": run.get("agent_id"), "simulation_id": run.get("simulation_id"), "model": MODEL,
                        "calls": len(results), "calls_passed": sum(r["pass"] for r in results),
                        "checks_passed": {k: sum(r[k] for r in results) for k in ("outcome", "policy", "honesty")},
                        "cases": len(by_case), "cases_passed_every_run": sum(all(v) for v in by_case.values()),
                        "flow_similarity_mean": round(sum(flows) / len(flows), 2) if flows else None},
            "violations": sorted(violations, key=lambda v: (-severity(v), v["case"], v["rep"])),
            "rules": sorted(per_rule.values(), key=lambda s: (-s["failed"], str(s["rule"]))),
            "rule_quotes_not_found": [r["id"] for r in rules["rules"] if r.get("quote_found") is False],
            "calls": results}


def markdown(rep):
    s, mark = rep["summary"], lambda ok: "pass" if ok else "FAIL"
    md = ["# Independent check report", "", f"Run `{s['run']}` (agent {s['agent_id']}, simulation {s['simulation_id']}), graded by "
          f"{s['model']} (effort max) against our rule checklist. The run file's own pass/fail was not used.", "",
          f"**{s['calls_passed']} of {s['calls']} calls pass** outcome, policy and honesty. "
          f"**{s['cases_passed_every_run']} of {s['cases']} cases** pass in every run.", "",
          "| Check | Pass | Fail |", "|---|---|---|",
          *(row(k, n, s["calls"] - n) for k, n in s["checks_passed"].items()), "",
          f"Flow similarity to the example conversations (1-5, not a scored check): {s['flow_similarity_mean']}.", "",
          "## Worst violations first", "", "| Severity | Case | Run | Check | Rule | What | Evidence |", "|---|---|---|---|---|---|---|",
          *(row(v.get("severity"), v["case"], v["rep"], v.get("check"), v.get("rule"), v.get("what"), v.get("evidence")) for v in rep["violations"]),
          "", "## Rules (failed first)", "", "| Rule | Applied | Failed | Failing calls | Rule text |", "|---|---|---|---|---|",
          *(row(r["rule"], r["applied"], r["failed"], ", ".join(r["failing_calls"]), r["text"]) for r in rep["rules"]),
          "", "## Calls", "", "| Case | Scenario | Run | Pass | Outcome | Policy | Honesty | Flow | Records by code |", "|---|---|---|---|---|---|---|---|---|",
          *(row(c["case"], c["scenario"], c["rep"], mark(c["pass"]), mark(c["outcome"]), mark(c["policy"]), mark(c["honesty"]),
                (c["grade"].get("flow") or {}).get("similarity"), {True: "match", False: "MISMATCH", None: "n/a"}[c["records_check"]["pass"]])
            for c in rep["calls"]),
          "", "## Needs human review", "", *(f"- {c['case']} r{c['rep']}: {'; '.join(map(str, c['review']))}" for c in rep["calls"] if c["review"]),
          "", "## Rule quotes not found verbatim in the documents", "", *(f"- {r}" for r in rep["rule_quotes_not_found"] or ["(none)"])]
    return "\n".join(md) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("rules", "cases", "grade"):
        p = sub.add_parser(name)
        p.add_argument("bundle", help="tools/case_folder.py bundle, or a folder of text documents")
        p.add_argument("--out", required=True, help="output folder (keep it outside the repository)")
        if name != "rules":
            p.add_argument("--rules", help="rules.json (default: <out>/rules.json)")
    g = sub.choices["grade"]
    g.add_argument("--run", required=True, help="run_cases.py --out file with the call ids")
    g.add_argument("--mock-url", required=True, help="the bank mock the calls used")
    g.add_argument("--cases", help="cases.json from the cases step (without it: grade on the rules alone)")
    g.add_argument("--env-file", default=str(ROOT / ".env"), help="credential file for FREYA_API_KEY and FREYA_BANK_TEAM_KEY")
    a = ap.parse_args(argv)
    import anthropic
    {"rules": cmd_rules, "cases": cmd_cases, "grade": cmd_grade}[a.cmd](a, anthropic.Anthropic(max_retries=5))


if __name__ == "__main__":
    main()

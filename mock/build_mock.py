#!/usr/bin/env python3
"""Build a Cloudflare Workers bank mock from a service contract and test customers.

    python3 mock/build_mock.py <contract.json|.yaml> <customers.json|.csv|.tsv|.xlsx|.md> [--out mock/build/final]
                               [--name freya-final-bank-mock] [--overrides overrides.json]
    mock/deploy.sh mock/build/final        # prints the mock URL

The contract is OpenAPI (JSON or YAML). Every operation is served; its kind comes from the kit's seven tool names, else
from the operation's name: identity (one security answer, or several fields at once), read (customer and product data
shaped by the 200 response schema), write (recorded per customer and per session), close (ends the session). Customers
come as rows (one per customer or one per product; several sheets are joined on the customer id column) or in the
kit's public-test-set.json form. It prints what it inferred. Fix anything wrong with --overrides, a JSON file such as
{"columns": {"Musteri No": "customer_id"}, "max_wrong": 3, "identity_required": ["date_of_birth"],
 "operations": {"get_statement": {"kind": "read", "requires_identity": true}}}
"""
import argparse, csv, datetime as dt, json, re, shutil, sys, unicodedata
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
SEVEN = {"verify_identity": "identity", "get_account": "read", "create_promise": "write", "add_note": "write",
         "schedule_callback": "write", "transfer_to_agent": "write", "close_call": "close"}
VERBS = {"create", "add", "schedule", "record", "log", "book", "register", "save", "submit", "set", "make", "open", "request", "send", "file"}
EVALUATOR_KEYS = {"script", "expected", "checks", "title", "family", "level", "brief", "style"}
NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "bir": 1, "iki": 2, "uc": 3, "dort": 4, "bes": 5}
ISO = re.compile(r"\d{4}-\d{2}-\d{2}$")


def fold(s):
    s = unicodedata.normalize("NFKD", str(s).replace("ı", "i").replace("İ", "i")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def resolve(node, root, depth=0):
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/") and depth < 40:
            target = root
            for part in ref[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
            return resolve(target, root, depth + 1)
        return {k: resolve(v, root, depth) for k, v in node.items()}
    return [resolve(v, root, depth) for v in node] if isinstance(node, list) else node


def flat(schema):
    """Merge allOf, take the first oneOf/anyOf branch."""
    schema = dict(schema or {})
    for part in schema.pop("allOf", []):
        part = flat(part)
        schema.setdefault("properties", {}).update(part.get("properties", {}))
        schema["required"] = schema.get("required", []) + part.get("required", [])
        schema.setdefault("type", part.get("type"))
    for k in ("oneOf", "anyOf"):
        if schema.get(k):
            return flat({**schema.pop(k)[0], **schema})
    return schema


def json_schema(content):
    content = content or {}
    hit = content.get("application/json") or next((v for k, v in content.items() if "json" in k), {})
    return flat(hit.get("schema"))


def load_contract(path):
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() in (".yaml", ".yml"):
        import yaml
        doc = yaml.safe_load(text)
    else:
        doc = json.loads(text)
    return resolve(doc, doc)


def operations(doc):
    ops = []
    for path, item in (doc.get("paths") or {}).items():
        for method, spec in item.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue
            name = spec.get("operationId") or [p for p in path.split("/") if p and not p.startswith("{")][-1]
            name = fold(re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name))
            body = json_schema((spec.get("requestBody") or {}).get("content"))
            body.setdefault("properties", {})
            body["required"] = list(body.get("required", []))
            for p in item.get("parameters", []) + spec.get("parameters", []):
                if p.get("in") in ("path", "query"):
                    body["properties"][p["name"]] = flat(p.get("schema") or {"type": "string"})
                    if p.get("required"):
                        body["required"].append(p["name"])
            responses = {str(k): v for k, v in (spec.get("responses") or {}).items()}
            ok = next((responses[c] for c in ("200", "201") if c in responses), {})
            ops.append({"name": name, "method": method.upper(), "path": path, "body": body,
                        "response": json_schema(ok.get("content")), "codes": list(responses),
                        "failures": {c: json_schema(r.get("content")) for c, r in responses.items() if c.startswith("5")},
                        "text": " ".join(str(spec.get(k, "")) for k in ("summary", "description"))})
    return ops


def boolish(p):
    e = {str(v).lower() for v in p.get("enum") or []}
    return p.get("type") == "boolean" or bool(e) and e <= {"true", "false"}


def response_shape(op):
    """The contract's own success and error fields, so every answer carries the declared names, types and codes."""
    resp, req = op["response"].get("properties", {}), op["response"].get("required", [])
    ok = "ok" if "ok" in resp else next((k for k in req if k in resp and boolish(resp[k])), None) or next(
        (k for k, p in resp.items() if boolish(p) and re.search(r"ok|success|accept|status|result", fold(k))), None)
    err = "error" if "error" in resp else next((k for k, p in resp.items() if k != ok and p.get("type") == "string"
                                                and re.search(r"error|fail|reason|hata|code", fold(k))), None)
    lit = lambda k, b: b if resp[k].get("type") == "boolean" else next((v for v in resp[k]["enum"] if str(v).lower() == str(b).lower()), str(b).lower())
    # A tool failure answers with the contract's declared failure response (a 5xx with its schema), else ok=false.
    fcode = next((c for c in op["failures"] if op["failures"][c].get("properties")), next(iter(op["failures"]), None))
    fault_codes = (op["failures"].get(fcode, {}).get("properties", {}).get(err) or {}).get("enum") if err else None
    return {"fault_status": int(fcode) if fcode else (200 if ok else 500), "fault_code": fault_codes[0] if fault_codes else None,"ok": ok, "yes": lit(ok, True) if ok else None, "no": lit(ok, False) if ok else None, "error": err,
            "codes": resp.get(err, {}).get("enum") if err else None,
            "bools": {k: [lit(k, True), lit(k, False)] for k, p in resp.items() if p.get("type") != "boolean" and boolish(p)}}


def kind_of(op):
    n = op["name"]
    if n in SEVEN:
        return SEVEN[n]
    if re.search(r"verif|identit|dogrula|kimlik|authent", n):
        return "identity"
    if re.search(r"close|end_call|hang_up|kapat|bitir", n):
        return "close"
    if op["method"] == "GET" or re.match(r"(get|read|list|fetch|lookup|find|sorgula|getir)", n):
        return "read"
    return "write"


def schema_for_worker(body):
    keep = ("type", "enum", "format", "pattern", "minimum", "maximum", "minLength", "maxLength")
    props = {}
    for k, p in body.get("properties", {}).items():
        props[k] = {x: p[x] for x in keep if x in p}
        if p.get("format") == "time" or "HH:MM" in str(p.get("description", "")):
            props[k]["x-time"] = True
    return {"properties": props, "required": sorted(set(body.get("required", [])))}


def iso_date(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?", s)
    d = None
    if m:
        y, mo, d = m.groups()
    else:
        m = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", s)   # day first, as Turkish and European dates are written
        if m:
            d, mo, y = m.groups()
    try:
        return dt.date(int(y), int(mo), int(d)).isoformat() if d else None
    except ValueError:
        return None


def number(v):
    if isinstance(v, (int, float)):
        return int(v) if float(v).is_integer() else v
    s = re.sub(r"[^\d,.\-]", "", str(v))
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", "") if len(s.split(",")[-1]) == 3 else s.replace(",", ".")
    n = float(s)
    return int(n) if n.is_integer() else n


def cell(v):
    if isinstance(v, dt.datetime):
        return v.date().isoformat() if v.time() == dt.time(0) else v.isoformat(timespec="minutes")
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v.strip() if isinstance(v, str) else v


def read_rows(path):
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            data = data.get("customers") or data.get("test_customers") or [dict(v, id=k) for k, v in data.items() if isinstance(v, dict)]
        return data
    if suffix in (".csv", ".tsv", ".txt"):
        text = path.read_text(encoding="utf-8-sig")
        return [{k: cell(v) for k, v in r.items() if k} for r in csv.DictReader(text.splitlines(), dialect=csv.Sniffer().sniff(text[:4096], ",;\t"))]
    if suffix in (".xlsx", ".xlsm"):
        import openpyxl   # python3 -m pip install openpyxl
        rows = []
        for ws in openpyxl.load_workbook(path, data_only=True, read_only=True).worksheets:
            header = None
            for r in ws.iter_rows(values_only=True):
                filled = [v for v in r if v is not None and str(v).strip()]
                if header is None:
                    header = [str(v).strip() if v is not None else "" for v in r] if len(filled) >= 2 else None
                elif filled:
                    rows.append({h: cell(v) for h, v in zip(header, r) if h})
        return rows
    if suffix == ".md":
        rows, header = [], None
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip().startswith("|"):
                header = None
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if header is None:
                header = cells
            elif not all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                rows.append({h: cell(v) for h, v in zip(header, cells) if h})
        return rows
    sys.exit(f"customers: unsupported file type {suffix} (use JSON, CSV, TSV, XLSX or a Markdown table)")


def customers_from(rows, ckey, identity_fields, item_types, pid, renames):
    ids = {fold(f): f for f in identity_fields}
    items = {fold(f): f for f in item_types}
    id_names = [fold(ckey), "id", "customer_id", "customer_no", "customer", "musteri_no", "musteri_id", "musteri_numarasi", "musteri"]
    out, skipped = {}, 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if isinstance(row.get("identity"), dict):   # already in this mock's form (the kit's public-test-set.json)
            c = out.setdefault(str(row.get("id") or row.get(ckey)), {"identity": {}, "products": [], "variables": {}, "faults": []})
            c["identity"].update(row["identity"])
            c["products"] += [dict(p) for p in row.get("products", [])]
            c["variables"].update(row.get("variables", {}))
            c["faults"] += row.get("faults", [])
            c["variables"].update({k: v for k, v in row.items() if k not in EVALUATOR_KEYS | {"id", ckey, "identity", "products", "variables", "faults"} and not isinstance(v, (dict, list))})
            continue
        cols = {}
        for k, v in row.items():
            if v is not None and str(v).strip() != "":
                name = renames.get(fold(k), k)
                cols[fold(name)] = v
        id_col = next((c for c in id_names if c in cols), None)
        if not id_col:
            skipped += 1
            continue
        c = out.setdefault(str(cell(cols.pop(id_col))), {"identity": {}, "products": [], "variables": {}, "faults": []})
        product = {}
        for k, v in cols.items():
            if k in ids:
                c["identity"].setdefault(ids[k], v)
            elif k in items:
                product[items[k]] = v
            else:
                c["variables"].setdefault(k, v)
        if product and product not in c["products"]:
            c["products"].append(product)
    for c in out.values():
        for i, p in enumerate(c["products"], 1):
            p.setdefault(pid, f"p{i}")
            for k, t in item_types.items():
                if k in p and t in ("number", "integer"):
                    try:
                        p[k] = int(number(p[k])) if t == "integer" else number(p[k])
                    except ValueError:
                        pass
                elif k in p and t == "string" and not isinstance(p[k], str):
                    p[k] = str(cell(p[k]))
    return out, skipped


def build(contract_path, customers_path, name, overrides):
    doc = load_contract(contract_path)
    ops = operations(doc)
    if not ops:
        sys.exit("contract: no operations found under paths")
    over_ops = overrides.get("operations", {})
    counts = Counter(k for op in ops for k in op["body"]["properties"])
    everywhere = [k for k, n in counts.most_common() if n == len(ops)] or [k for k, _ in counts.most_common()]
    in_all = [k for k, n in counts.items() if n == len(ops)]
    ckey = overrides.get("customer_key") or next((k for k in everywhere if re.search(r"customer|musteri|client", fold(k))), in_all[0] if len(in_all) == 1 else "customer_id")
    for op in ops:
        op["kind"] = over_ops.get(op["name"], {}).get("kind") or kind_of(op)
        if op["kind"] not in ("identity", "read", "write", "close"):
            sys.exit(f"operation {op['name']}: unknown kind {op['kind']}")

    reads = [op for op in ops if op["kind"] == "read"]
    item_types, list_name = {}, None
    for op in reads:
        for k, p in op["response"].get("properties", {}).items():
            items = flat(p.get("items")) if p.get("type") == "array" else {}
            if items.get("properties") and not item_types:
                list_name, item_types = k, {f: q.get("type", "string") for f, q in items["properties"].items()}
    pid = next((f for f in item_types if re.search(r"(^|_)id$", f)), "product_id")

    identity_ops = [op for op in ops if op["kind"] == "identity"]
    identity_fields = []
    for op in identity_ops:
        props = {k: p for k, p in op["body"]["properties"].items() if k != ckey}
        field_param = next((k for k, p in props.items() if p.get("enum")), None)
        op["identity_fields"] = [str(v) for v in props[field_param]["enum"]] if field_param else list(props)
        op["field_param"] = field_param
        op["answer_param"] = next((k for k in props if k != field_param), None) if field_param else None
        identity_fields += [f for f in op["identity_fields"] if f not in identity_fields]

    renames = {fold(k): v for k, v in overrides.get("columns", {}).items()}
    customers, skipped = customers_from(read_rows(customers_path), ckey, identity_fields, item_types, pid, renames)
    for c in customers.values():   # behaviour columns such as fail_<operation> or faults=<operation>: that call fails that many times
        for k in [k for k in c["variables"] if re.search(r"fail|fault|error|outage|unavail|hata", fold(k))]:
            tools = [op["name"] for op in ops if op["name"] in fold(k)] or [op["name"] for op in ops if op["name"] in fold(c["variables"][k])]
            v = c["variables"].pop(k) if tools else None
            try:
                times = int(number(v))
            except (TypeError, ValueError):
                times = 0 if fold(v) in ("", "no", "false", "hayir", "yok", "none") else 1
            c["faults"] += [{"tool": t, "fail_first_n": times} for t in tools if times > 0]
    if not customers:
        sys.exit("customers: none found (is there a customer id column? map it with --overrides columns)")

    formats = {}
    for f in identity_fields:
        values = [str(c["identity"][f]) for c in customers.values() if c["identity"].get(f) not in (None, "")]
        dates = [iso_date(v) for v in values]
        if (values and all(dates)) or re.search(r"date|tarih|dob", fold(f)) and "year" not in fold(f) and "yil" not in fold(f):
            formats[f] = "date"
            for c in customers.values():
                if c["identity"].get(f) not in (None, ""):
                    c["identity"][f] = iso_date(c["identity"][f]) or str(c["identity"][f])
        elif values and all(re.fullmatch(r"\d+", v) for v in values):
            formats[f] = "digits"
        else:
            formats[f] = "text"
        for c in customers.values():
            if f in c["identity"]:
                c["identity"][f] = str(cell(c["identity"][f]))

    text = fold(" ".join(op["text"] + " " + json.dumps(op["body"]) for op in identity_ops))
    m = re.search(r"(?:^|_)(\d+|" + "|".join(NUMBERS) + r")_(?:[a-z]+_)?(?:wrong|incorrect|failed|yanlis|hatali)", text)
    max_wrong = int(overrides.get("max_wrong") or (int(m.group(1)) if m and m.group(1).isdigit() else NUMBERS.get(m.group(1) if m else "", 3)))

    config_ops = []
    for op in ops:
        resp = op["response"].get("properties", {})
        shape = response_shape(op)
        plain = ("ok", "error", shape["ok"], shape["error"])
        out = {"name": op["name"], "method": op["method"], "path": op["path"], "kind": op["kind"], "body": schema_for_worker(op["body"]),
               "error_status": 200 if shape["ok"] else int(next((c for c in op["codes"] if c in ("400", "422")), "400")), "shape": shape}
        text = fold(op["text"])
        props = op["body"]["properties"]
        product_ref = pid if pid in props else None
        gated = bool(identity_ops) and (bool(re.search(r"refused_until|until_.*(verified|passed)|after_.*verif|only_.*verified|dogrulan", text)))
        if op["kind"] == "identity":
            def find(pattern, typ=None, skip=()):
                return next((k for k, p in resp.items() if k not in skip + plain and re.search(pattern, fold(k))
                             and (typ is None or (boolish(p) if typ == "boolean" else p.get("type") == typ))), None)
            all_v = find(r"^all|all_|fully|tum|hepsi", "boolean")
            hint = find(r"hint|ipucu|guid")
            out["identity"] = {"field_param": op["field_param"], "answer_param": op["answer_param"], "fields": op["identity_fields"],
                               "hint_value": next((v for v in resp.get(hint, {}).get("enum", []) if "swap" in fold(v)), None) if hint else None,
                               "out": {"verified": find(r"verif|dogru|match|correct|valid", "boolean", (all_v,)),
                                       "remaining": find(r"left|remaining|kalan|attempt|hak", "integer"), "all_verified": all_v, "hint": hint}}
        elif op["kind"] == "read":
            item_list = next((k for k, p in resp.items() if p.get("type") == "array"), None)
            lst = item_list if item_list == list_name else None
            scalars = [k for k, p in resp.items() if k not in plain and p.get("type") not in ("array", "object")]
            objects = {k: list(flat(p).get("properties", {})) for k, p in resp.items() if p.get("type") == "object"}
            account = op["name"] == "get_account" or lst or gated or any(f in item_types or f in identity_fields for f in scalars + sum(objects.values(), []))
            out.update(requires_identity=bool(identity_ops) and bool(account), list=lst, item_fields=list(item_types), scalars=scalars,
                       objects=objects, product_ref=product_ref)
        elif op["kind"] == "write":
            parts = op["name"].split("_")
            key = parts[0] + "s" if parts[0] in ("transfer", "escalate", "refund", "payment") else "_".join(parts[1:]) + "s" if len(parts) > 1 and parts[0] in VERBS else op["name"]
            ids = [k for k, p in resp.items() if k not in plain and (re.search(r"(^|_)id$", fold(k)) or p.get("type") == "string" and not p.get("enum"))]
            out.update(requires_identity=bool(identity_ops) and (bool(product_ref) or gated), ledger_key=key, product_ref=product_ref,
                       unique=bool(re.search(r"one_per_(product|card|account|loan)|per_product|urun_basina|her_urun", text)),
                       ids=ids, id_prefix={k: fold(k)[:2] for k in ids})
        else:
            fields = [k for k in props if k != ckey]
            require = []
            for f in fields:
                d = fold(props[f].get("description", ""))
                m = re.search(r"required_for_(?:every|all|each)_(\w+?)_except_(.+)", d)
                other = m and next((k for k in fields if fold(k) == m.group(1) and props[k].get("enum")), None)
                if other:
                    require.append({"field": f, "unless_field": other, "unless": [v for v in props[other]["enum"] if re.search(r"(^|_)" + fold(v) + r"(_|$)", m.group(2))]})
            out.update(fields=fields, require=require)
        out.update({k: v for k, v in over_ops.get(op["name"], {}).items() if k != "kind"})
        config_ops.append(out)

    base = urlparse(((doc.get("servers") or [{}])[0]).get("url", "")).path.rstrip("/")
    config = {"title": (doc.get("info") or {}).get("title", "bank mock"), "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "base_path": overrides.get("base_path", base), "customer_key": ckey, "product_id": pid,
              "session_idle_seconds": int(overrides.get("session_idle_seconds", 600)),
              "identity": {"required": overrides.get("identity_required") or identity_fields, "max_wrong": max_wrong, "formats": formats} if identity_ops else None,
              "operations": config_ops, "customers": customers}
    report = [f"contract: {config['title']}, base path '{config['base_path']}', customer key '{ckey}', product id '{pid}'"]
    for op in config_ops:
        extra = {"identity": lambda o: f"fields {o['identity']['fields']} -> response {o['identity']['out']}, hint {o['identity']['hint_value']}",
                 "read": lambda o: f"list {o['list']} {o['item_fields']} scalars {o['scalars']} objects {o['objects']} identity-gated {o['requires_identity']}",
                 "write": lambda o: f"ledger '{o['ledger_key']}' product_ref {o['product_ref']} one-per-product {o['unique']} ids {o['ids']} identity-gated {o['requires_identity']}",
                 "close": lambda o: f"fields {o['fields']} require {o['require']}"}[op["kind"]](op)
        report.append(f"  {op['method']} {config['base_path']}{op['path']}: {op['name']} = {op['kind']}; {extra}")
    if identity_ops:
        report.append(f"identity: required {config['identity']['required']}, {max_wrong} wrong answers in total, formats {formats}")
    missing = {f: [cid for cid, c in customers.items() if f not in c["identity"]] for f in config["identity"]["required"]} if identity_ops else {}
    report.append(f"customers: {len(customers)} ({sum(len(c['products']) for c in customers.values())} products), skipped rows without an id: {skipped}")
    report += [f"WARNING: customers without identity field {f}: {ids}" for f, ids in missing.items() if ids]
    report += [f"WARNING: customer {cid} has no products" for cid, c in customers.items() if item_types and not c["products"]]
    return config, report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("contract"); ap.add_argument("customers")
    ap.add_argument("--out", default=str(HERE / "build" / "final")); ap.add_argument("--name", default="freya-final-bank-mock")
    ap.add_argument("--overrides")
    a = ap.parse_args()
    overrides = json.loads(Path(a.overrides).read_text()) if a.overrides else {}
    config, report = build(Path(a.contract), Path(a.customers), a.name, overrides)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (out / "config.js").write_text("export default " + json.dumps(config, indent=1, ensure_ascii=False) + ";\n", encoding="utf-8")
    shutil.copyfile(HERE / "worker.js", out / "worker.js")
    (out / "wrangler.toml").write_text(f'name = "{a.name}"\nmain = "worker.js"\ncompatibility_date = "2026-09-01"\nworkers_dev = true\n\n'
                                       '[observability]\nenabled = true\n\n[[durable_objects.bindings]]\nname = "BANK"\nclass_name = "Bank"\n\n'
                                       '[[migrations]]\ntag = "v1"\nnew_sqlite_classes = ["Bank"]\n')
    print("\n".join(report))
    print(f"wrote {out}/config.js, worker.js, wrangler.toml. Deploy: mock/deploy.sh {out}")


if __name__ == "__main__":
    main()

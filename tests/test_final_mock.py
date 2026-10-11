"""The bank mock generator (mock/build_mock.py) and its Worker (mock/worker.js), run locally under Node."""
import importlib.util, json, os, shutil, socket, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KIT = ROOT / "build-the-builder"
sys.path.insert(0, str(ROOT / "mock"))
import build_mock  # noqa: E402

CONTRACT_YAML = """
openapi: 3.0.3
info: {title: Test bank}
servers: [{url: "https://bank.invalid/api"}]
paths:
  /verify_identity:
    post:
      summary: Check one answer. Two wrong answers in total end the check.
      requestBody: {content: {application/json: {schema: {$ref: "#/components/schemas/Verify"}}}}
      responses: {"200": {content: {application/json: {schema: {type: object, properties: {ok: {type: boolean}, error: {type: string},
        verified: {type: boolean}, attempts_left: {type: integer}, all_verified: {type: boolean}}}}}}}
  /get_account:
    post:
      requestBody: {content: {application/json: {schema: {type: object, required: [customer_no], properties: {customer_no: {type: string}}}}}}
      responses: {"200": {content: {application/json: {schema: {type: object, properties: {ok: {type: boolean}, segment: {type: string},
        statement: {type: object, properties: {due_date: {type: string}}},
        cards: {type: array, items: {type: object, properties: {card_id: {type: string}, statement_balance: {type: number}, minimum_due: {type: number}}}}}}}}}}
  /create_promise:
    post:
      requestBody: {content: {application/json: {schema: {type: object, required: [customer_no, card_id, amount],
        properties: {customer_no: {type: string}, card_id: {type: string}, amount: {type: number}}}}}}
      responses: {"200": {content: {application/json: {schema: {type: object, properties: {ok: {type: boolean}, promise_id: {type: string}}}}}}}
  /close_call:
    post:
      requestBody: {content: {application/json: {schema: {type: object, required: [customer_no, outcome],
        properties: {customer_no: {type: string}, outcome: {type: string, enum: [promise, refused]}}}}}}
      responses: {"200": {content: {application/json: {schema: {type: object, properties: {ok: {type: boolean}}}}}}}
components:
  schemas:
    Verify: {type: object, required: [customer_no, field, answer],
      properties: {customer_no: {type: string}, field: {type: string, enum: [birth_date, id_last4]}, answer: {type: string}}}
"""

CSV_ROWS = """Musteri No;Ad;Dogum Tarihi;ID Last4;Card ID;Statement Balance;Minimum Due;Segment;Due Date
C1;Ayse;14.03.1988;0358;k1;1.250,50;250;gold;2026-10-20
C1;Ayse;14.03.1988;0358;k2;99,90;20;gold;2026-10-20
C2;Mehmet;02.11.1979;4471;k1;300;60;basic;2026-10-22
"""
RENAMES = {"columns": {"Dogum Tarihi": "birth_date", "ID Last4": "id_last4"}}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class GeneratorTest(unittest.TestCase):
    def test_practice_contract_and_public_customers(self):
        config, report = build_mock.build(KIT / "tools/openapi.json", KIT / "public-test-set.json", "t", {})
        kinds = {op["name"]: op["kind"] for op in config["operations"]}
        self.assertEqual(kinds, dict(build_mock.SEVEN))
        self.assertEqual(config["identity"], {"required": ["date_of_birth", "phone_last4"], "max_wrong": 3,
                                              "formats": {"date_of_birth": "date", "phone_last4": "digits"}})
        self.assertEqual(config["base_path"], "/v1")
        self.assertEqual(len(config["customers"]), 13)
        pub11 = config["customers"]["PUB-11"]
        self.assertEqual([p["product_id"] for p in pub11["products"]], ["p1", "p2", "p3"])
        self.assertNotIn("expected", json.dumps(config["customers"]))
        close = next(op for op in config["operations"] if op["kind"] == "close")
        self.assertEqual(close["require"], [{"field": "reason", "unless_field": "outcome", "unless": ["promise", "paid"]}])

    @unittest.skipUnless(importlib.util.find_spec("yaml"), "PyYAML is not installed")
    def test_yaml_contract_with_csv_rows_and_renamed_columns(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "contract.yaml").write_text(CONTRACT_YAML)
            (Path(d) / "customers.csv").write_text(CSV_ROWS)
            config, _ = build_mock.build(Path(d) / "contract.yaml", Path(d) / "customers.csv", "t", RENAMES)
        self.assertEqual((config["customer_key"], config["product_id"], config["base_path"]), ("customer_no", "card_id", "/api"))
        self.assertEqual(config["identity"]["max_wrong"], 2)
        c1 = config["customers"]["C1"]
        self.assertEqual(c1["identity"], {"birth_date": "1988-03-14", "id_last4": "0358"})
        self.assertEqual([(p["card_id"], p["statement_balance"]) for p in c1["products"]], [("k1", 1250.5), ("k2", 99.9)])
        self.assertEqual(c1["variables"]["segment"], "gold")
        verify = config["operations"][0]["identity"]["out"]
        self.assertEqual((verify["verified"], verify["remaining"], verify["all_verified"]), ("verified", "attempts_left", "all_verified"))


    @unittest.skipUnless(importlib.util.find_spec("yaml") and importlib.util.find_spec("openpyxl"), "PyYAML or openpyxl is not installed")
    def test_xlsx_sheets_joined_on_the_customer_id(self):
        import datetime, openpyxl
        wb = openpyxl.Workbook()
        wb.active.append(["Musteri No", "Ad", "Dogum Tarihi", "ID Last4"])
        wb.active.append(["C1", "Ayse", datetime.datetime(1988, 3, 14), 358])
        cards = wb.create_sheet("Cards")
        cards.append(["Musteri No", "Card ID", "Statement Balance", "Minimum Due"])
        cards.append(["C1", "k1", 1250.5, 250])
        cards.append(["C1", "k2", 99.9, 20])
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "contract.yaml").write_text(CONTRACT_YAML)
            wb.save(Path(d) / "customers.xlsx")
            config, _ = build_mock.build(Path(d) / "contract.yaml", Path(d) / "customers.xlsx", "t", RENAMES)
        c1 = config["customers"]["C1"]
        self.assertEqual((c1["identity"], c1["variables"]), ({"birth_date": "1988-03-14", "id_last4": "358"}, {"ad": "Ayse"}))
        self.assertEqual([p["card_id"] for p in c1["products"]], ["k1", "k2"])


@unittest.skipUnless(shutil.which("node") and importlib.util.find_spec("yaml"), "node or PyYAML is not installed")
class GenericWorkerTest(unittest.TestCase):
    def test_contract_response_fields_and_declared_failure(self):
        flag = {"type": "string", "enum": ["true", "false"]}
        ok = {"type": "object", "required": ["accepted"], "properties": {"accepted": flag, "ref": {"type": "string", "minLength": 1},
              "failure_code": {"type": "string", "enum": ["temporary_unavailable", "invalid_request"]}}}
        down = {"type": "object", "properties": {"accepted": {"type": "string", "enum": ["false"]}, "failure_code": {"type": "string", "enum": ["temporary_unavailable"]}}}
        op = lambda props: {"post": {"requestBody": {"content": {"application/json": {"schema": {"type": "object", "required": ["session_ref"],
              "properties": {"session_ref": {"type": "string"}, **props}}}}}, "responses": {"200": {"content": {"application/json": {"schema": ok}}},
              "503": {"content": {"application/json": {"schema": down}}}}}}
        contract = {"openapi": "3.0.3", "paths": {"/book_slot": op({"day": {"type": "string", "format": "date"}}), "/close_call": op({})}}
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "contract.json").write_text(json.dumps(contract))
            (d / "customers.csv").write_text("customer_id,name,fail_book_slot\nA1,Ada,1\nB2,Bo,\n")
            subprocess.run([sys.executable, str(ROOT / "mock/build_mock.py"), str(d / "contract.json"), str(d / "customers.csv"), "--out", str(d / "out")],
                           check=True, capture_output=True)
            port = free_port()
            server = subprocess.Popen(["node", str(ROOT / "mock/local_server.mjs"), str(d / "out"), str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                def call(tool, **args):
                    req = urllib.request.Request(f"http://127.0.0.1:{port}/{tool}", data=json.dumps(args).encode(), method="POST")
                    for _ in range(50):
                        try:
                            with urllib.request.urlopen(req, timeout=5) as r:
                                return r.status, json.load(r)
                        except urllib.error.HTTPError as e:
                            return e.code, json.load(e)
                        except urllib.error.URLError:
                            time.sleep(0.1)
                self.assertEqual(call("book_slot", session_ref="A1", day="2026-10-14"), (503, {"accepted": "false", "failure_code": "temporary_unavailable"}))
                self.assertEqual(call("book_slot", session_ref="A1", day="2026-10-14"), (200, {"accepted": "true", "ref": "re1"}))
                self.assertEqual(call("book_slot", session_ref="B2", day="14 Oct"), (200, {"accepted": "false", "failure_code": "invalid_request"}))
                self.assertEqual(call("close_call", session_ref="B2"), (200, {"accepted": "true"}))
            finally:
                server.terminate()
                server.wait()

    def test_other_contract_served_from_rows(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "contract.yaml").write_text(CONTRACT_YAML)
            (d / "customers.csv").write_text(CSV_ROWS)
            (d / "overrides.json").write_text(json.dumps(RENAMES))
            subprocess.run([sys.executable, str(ROOT / "mock/build_mock.py"), str(d / "contract.yaml"), str(d / "customers.csv"), "--out", str(d / "out"),
                            "--overrides", str(d / "overrides.json")], check=True, capture_output=True)
            port = free_port()
            server = subprocess.Popen(["node", str(ROOT / "mock/local_server.mjs"), str(d / "out"), str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                def call(tool, **args):
                    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/{tool}", data=json.dumps({"customer_no": "C1", **args}).encode(), method="POST")
                    for _ in range(50):
                        try:
                            with urllib.request.urlopen(req, timeout=5) as r:
                                return json.load(r)
                        except urllib.error.URLError:
                            time.sleep(0.1)
                self.assertEqual(call("get_account"), {"ok": False, "error": "identity not verified"})
                self.assertEqual(call("verify_identity", field="birth_date", answer="1988-03-14"), {"ok": True, "verified": True, "attempts_left": 2, "all_verified": False})
                self.assertEqual(call("verify_identity", field="id_last4", answer="358")["all_verified"], True)
                self.assertEqual(call("get_account"), {"ok": True, "segment": "gold", "statement": {"due_date": "2026-10-20"}, "cards": [
                    {"card_id": "k1", "statement_balance": 1250.5, "minimum_due": 250}, {"card_id": "k2", "statement_balance": 99.9, "minimum_due": 20}]})
                self.assertEqual(call("create_promise", card_id="k2", amount=99.9), {"ok": True, "promise_id": "pr1"})
                self.assertEqual(call("close_call", outcome="Promise"), {"ok": True})
            finally:
                server.terminate()
                server.wait(5)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class LocalWorkerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """MOCK_TEST_HOST=<url> runs these checks against a mock that already runs (wrangler dev, or a deployed practice mock)."""
        cls.cases = {c["id"]: c for c in json.loads((KIT / "public-test-set.json").read_text())}
        cls.host, cls.server = os.environ.get("MOCK_TEST_HOST", "").rstrip("/"), None
        cls.admin = os.environ.get("MOCK_TEST_ADMIN_KEY")
        if cls.host:
            return
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name) / "practice"
        subprocess.run([sys.executable, str(ROOT / "mock/build_mock.py"), str(KIT / "tools/openapi.json"), str(KIT / "public-test-set.json"),
                        "--out", str(out)], check=True, capture_output=True)
        port, cls.admin = free_port(), "local-admin-key"
        cls.host = f"http://127.0.0.1:{port}"
        cls.server = subprocess.Popen(["node", str(ROOT / "mock/local_server.mjs"), str(out), str(port)], stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, env={**os.environ, "MOCK_ADMIN_KEY": cls.admin})
        for _ in range(100):
            try:
                urllib.request.urlopen(cls.host + "/_mock/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        if cls.server:
            cls.server.terminate()
            cls.server.wait(5)
            cls.tmp.cleanup()

    def http(self, path, body=None, headers=None):
        req = urllib.request.Request(self.host + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"content-type": "application/json", "user-agent": "freya-mock-test/1", **(headers or {})}, method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def call(self, cid, tool, **args):
        return self.http("/v1/" + tool, {"customer_id": cid, **args})

    def session(self, case):
        return self.http("/v1/practice/sessions", {"case": case})[1]["variables"]["customer_id"]

    def test_score_py_passes_all_public_cases_on_scripted_calls(self):
        for case in self.cases.values():
            cid, exp = self.session(case["id"]), case["expected"]
            if exp["outcome"] == "not_verified":
                for answer in ("1900-01-01", "1900-01-02", "1900-01-03"):
                    self.call(cid, "verify_identity", field="date_of_birth", answer=answer)
            elif exp["outcome"] != "not_reached":
                for field, answer in case["identity"].items():
                    self.assertTrue(self.call(cid, "verify_identity", field=field, answer=answer)[1]["verified"])
                self.assertEqual(len(self.call(cid, "get_account")[1]["products"]), len(case["products"]))
                for p in exp["promises"]:
                    status, r = self.call(cid, "create_promise", **p)
                    if status == 500:
                        status, r = self.call(cid, "create_promise", **p)
                    self.assertEqual((status, r["ok"]), (200, True), case["id"])
                for n in exp["notes"]:
                    self.assertTrue(self.call(cid, "add_note", **n)[1]["ok"])
                for t in exp["transfers"]:
                    self.assertTrue(self.call(cid, "transfer_to_agent", **t)[1]["ok"])
            for cb in exp["callbacks"]:
                self.assertTrue(self.call(cid, "schedule_callback", **cb)[1]["ok"])
            self.assertTrue(self.call(cid, "close_call", outcome=exp["outcome"], **({"reason": exp["reason"]} if exp["reason"] else {}))[1]["ok"])
            score = subprocess.run([sys.executable, str(KIT / "tools/score.py"), case["id"], cid], capture_output=True, text=True,
                                   env={**os.environ, "FREYA_BANK_HOST": self.host, "FREYA_BANK_TEAM_KEY": "local-test-team-key"})
            self.assertEqual(score.returncode, 0, case["id"] + score.stdout)

    def test_identity_rules(self):
        cid = self.session("PUB-13")   # date of birth 1991-01-08
        self.assertEqual(self.call(cid, "get_account")[1], {"ok": False, "error": "identity not verified"})
        self.assertEqual(self.call(cid, "verify_identity", field="date_of_birth", answer="8 January 1991")[1]["error"],
                         "answer is not a date. Send it as YYYY-MM-DD.")
        self.assertEqual(self.call(cid, "verify_identity", field="date_of_birth", answer="1991-08-01")[1],
                         {"ok": True, "verified": False, "wrong_answers_left": 3, "all_verified": False, "hint": "day_month_swapped"})
        self.assertEqual(self.call(cid, "verify_identity", field="date_of_birth", answer="1991-08-01")[1]["wrong_answers_left"], 2)
        self.assertEqual(self.call(cid, "verify_identity", field="phone_last4", answer="66 04")[1],
                         {"ok": True, "verified": True, "wrong_answers_left": 2, "all_verified": False})
        self.assertEqual(self.call(cid, "verify_identity", field="date_of_birth", answer="1991-01-08")[1]["all_verified"], True)
        self.assertEqual(self.call(cid, "create_promise", product_id="p9", amount=1, date="2026-10-16")[1]["error"], "unknown product_id")
        self.assertEqual(self.call(cid, "create_promise", product_id="p1", amount="300", date="2026-10-16")[1], {"ok": True, "promise_id": "pr1"})
        self.assertIn("already recorded", self.call(cid, "create_promise", product_id="p1", amount=300, date="2026-10-16")[1]["error"])
        self.assertEqual(self.call(cid, "create_promise", product_id="p2", amount=900, date="23/10/2026")[1]["error"], "date must be a date as YYYY-MM-DD")
        self.assertEqual(self.call(cid, "transfer_to_agent", queue="billing")[1]["error"], "queue must be one of restructuring, general, support")
        self.assertEqual(self.call(cid, "close_call", outcome="no_promise")[1]["error"], "reason is required for outcome no_promise")
        self.assertTrue(self.call(cid, "close_call", outcome="promise")[1]["ok"])
        self.assertEqual(self.call(cid, "add_note", product_id="p2", reason="refused")[1]["error"], "the call is already closed")
        led = self.http("/v1/practice/ledger/" + cid)[1]
        self.assertEqual((led["promises"], led["outcome"], led["wrong"], led["hinted"]), ([{"product_id": "p1", "amount": 300, "date": "2026-10-16", "promise_id": "pr1"}], "promise", 1, True))

    def test_three_wrong_answers_lock_the_check(self):
        cid = self.session("PUB-03")
        lefts = [self.call(cid, "verify_identity", field="phone_last4", answer="0000")[1]["wrong_answers_left"] for _ in range(3)]
        self.assertEqual(lefts, [2, 1, 0])
        self.assertEqual(self.call(cid, "verify_identity", field="phone_last4", answer="6630")[1]["ok"], False)
        self.assertEqual(self.call(cid, "get_account")[1]["error"], "identity not verified")

    def test_fault_injection_and_customer_ids_without_a_session(self):
        cid = self.session("PUB-10")
        for field, answer in self.cases["PUB-10"]["identity"].items():
            self.call(cid, "verify_identity", field=field, answer=answer)
        self.assertEqual(self.call(cid, "create_promise", product_id="p1", amount=365, date="2026-10-16"), (500, {"ok": False, "error": "service unavailable, nothing was recorded"}))
        self.assertEqual(self.call(cid, "create_promise", product_id="p1", amount=365, date="2026-10-16")[1]["ok"], True)
        # the customer's own id: every close starts the next call's records afresh
        self.assertEqual(self.call("PUB-05", "verify_identity", field="phone_last4", answer="1111")[1]["wrong_answers_left"], 2)
        self.call("PUB-05", "close_call", outcome="not_verified", reason="declined")
        self.assertEqual(self.call("PUB-05", "verify_identity", field="phone_last4", answer="8846")[1]["wrong_answers_left"], 3)
        self.assertEqual([c["tool"] for c in self.http("/_mock/ledger/PUB-05")[1]["calls"]], ["verify_identity"])

    def test_errors_routes_and_admin(self):
        self.assertEqual(self.call("nobody", "get_account")[1], {"ok": False, "error": "unknown customer_id"})
        self.assertEqual(self.http("/v1/get_account", {})[1]["error"], "customer_id is required")
        self.assertEqual(self.http("/v1/nothing", {})[0], 404)
        self.assertEqual(self.http("/v1/get_account")[0], 405)
        self.assertEqual(self.http("/_mock/reset", {})[0], 403)
        fresh = self.http("/v1/practice/ledger/" + self.session("PUB-01"))[1]
        self.assertEqual((fresh["promises"], fresh["callbacks"], fresh["outcome"], fresh["calls"]), ([], [], None, []))
        self.assertIn("nobody", json.dumps(self.http("/_mock/ledgers")[1]["unknown_customer_calls"]))
        if self.admin:
            self.assertEqual(self.http("/_mock/reset", {"customer_id": "PUB-05"}, {"x-admin-key": self.admin}), (200, {"ok": True}))


if __name__ == "__main__":
    unittest.main()

"""
Run with:  python -m pytest -q
Uses fake Airtable and OpenAI, so no keys or internet are needed.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import reports  # noqa: E402
from airtable_client import AirtableClient  # noqa: E402
from assistant import Session, Tools  # noqa: E402
from fakes import FakeAirtableHTTP, FakeOpenAI, answer, call, calls, make_students  # noqa: E402
from privacy import PrivacyPolicy  # noqa: E402

POLICY = {"tables": {"Student Profile/Biodata": {"Level": "model"}}}


def make_client(http=None, sleeps=None):
    http = http or FakeAirtableHTTP()
    sleeps = sleeps if sleeps is not None else []
    client = AirtableClient("tok", "appTEST", PrivacyPolicy(data=POLICY),
                            http=http, sleep=sleeps.append)
    return client, http


def private_values():
    """Every student value that must never reach OpenAI."""
    values = set()
    for s in make_students():
        for key in ("Name", "Matric_Number", "Phone", "Bank Account Number"):
            values.add(s["fields"][key])
        for att in s["fields"].get("Passport", []):
            values.add(att["filename"])
    return values


def assert_nothing_private_sent(fake_openai):
    sent = json.dumps(fake_openai.sent)
    leaked = [v for v in private_values() if v in sent]
    assert not leaked, f"Private values sent to OpenAI: {leaked[:5]}"


# --- Airtable client ------------------------------------------------------

def test_pagination_cache_and_blocked_fields():
    client, http = make_client()
    rows = client.records("student profile/biodata")
    assert len(rows) == 250
    first = len(http.calls)
    client.records("Student Profile/Biodata")
    client.records("Student Profile/Biodata")
    assert len(http.calls) == first, "second request should come from the cache"
    # Bank Account Number is blocked by default and never requested.
    student_requests = [f for t, f in http.requested_fields if t == "tblSTU"]
    assert all(f and "fldBank" not in f for f in student_requests)
    assert all(all(x.startswith("fld") for x in f) for f in student_requests)
    assert "Bank Account Number" not in rows[0]["fields"]
    # Linked departments are shown by name, attachments by file name.
    assert rows[0]["fields"]["Department"] in (["Computer Science"], ["Accounting"])
    assert rows[0]["fields"]["Passport"] == ["pp1.jpg"]


def test_duplicate_field_names_do_not_break_queries():
    client, _ = make_client()
    names = [f["name"] for f in client.fields("Student Profile/Biodata")]
    copies = [n for n in names if "copy" in n.lower()]
    assert copies == ["Capstone Projecty Signed Code of Ethics copy",
                      "Capstone Projecty Signed Code of Ethics Copy (2)"]
    tools = Tools(client)
    seen, _ = tools.run("count_records", {
        "table": "Student Profile/Biodata",
        "filters": [{"field": "Onboarding Status", "op": "equals", "value": "Onboarded"}],
        "group_by": None})
    assert seen["count"] == sum(1 for i in range(1, 251) if i % 4)
    rows = client.records("Student Profile/Biodata")
    assert rows[0]["fields"]["Capstone Projecty Signed Code of Ethics Copy (2)"] == "copy-b"


def test_rate_limit_is_retried():
    sleeps = []
    client, http = make_client(FakeAirtableHTTP(fail_first_with_429=True), sleeps)
    assert len(client.tables()) == 3
    assert 30 in sleeps


def test_refresh_clears_cache():
    client, http = make_client()
    client.records("Departments")
    before = len(http.calls)
    client.refresh()
    client.records("Departments")
    assert len(http.calls) > before


# --- Reports --------------------------------------------------------------

def test_passport_report():
    client, _ = make_client()
    attachment, model = reports.run(client, "missing_field", None, "passport")
    assert model["missing"] == 50
    # i % 7 == 0 and i % 5 != 0  -> has passport but still flagged
    expected_inconsistent = sum(1 for i in range(1, 251) if i % 7 == 0 and i % 5 != 0)
    assert model["has_value_but_still_flagged"] == expected_inconsistent
    assert len(attachment.tables[0].rows) == 50          # full list, not shortened
    assert attachment.tables[0].columns == ["Name", "Matric_Number"]
    assert "Student Person" not in json.dumps(model)


def test_other_reports_run():
    client, _ = make_client()
    _, inc = reports.run(client, "incomplete_profiles")
    assert inc["missing_item_counts"]["Birth Certificate"] == 83
    _, dup = reports.run(client, "duplicates")
    assert dup["duplicated_values"] == 1
    _, comp = reports.run(client, "field_completeness")
    assert comp["percent_filled_by_field"]["Passport"] == 80.0
    _, ov = reports.run(client, "overview")
    assert ov["tables"]["Departments"]["records"] == 2
    assert ov["tables"]["Fees Ledger 2026"]["records"] == 167


# --- Assistant ------------------------------------------------------------

def test_find_records_keeps_values_local_and_follow_up_remembers():
    client, _ = make_client()
    fake = FakeOpenAI([
        calls(call("find_records", {
            "table": "Student Profile/Biodata",
            "filters": [{"field": "Passport", "op": "is_empty", "value": None}],
            "columns": None, "sort_by": None, "descending": None, "limit": None,
            "title": "Missing passports"}, "c1")),
        answer("50 students have not submitted a passport. They are listed below."),
        calls(call("find_records", {
            "table": "Student Profile/Biodata",
            "filters": [{"field": "Passport", "op": "is_empty", "value": None},
                        {"field": "Level", "op": "equals", "value": "300"}],
            "columns": None, "sort_by": "Name", "descending": False, "limit": None,
            "title": "Missing passports, 300 level"}, "c2")),
        answer("12 of them are in 300 level."),
    ])
    session = Session(Tools(client), openai_client=fake)

    first = session.ask("Which students have not submitted their passport?")
    assert len(first.attachments) == 1
    assert len(first.attachments[0].tables[0].rows) == 50

    second = session.ask("Now only the 300-level ones")
    rows = second.attachments[0].tables[0].rows
    assert len(rows) == sum(1 for i in range(1, 251) if i % 5 == 0 and i % 4 == 2)
    # The follow-up request included the first question and answer.
    history = json.dumps(fake.sent[2]["input"])
    assert "Which students have not submitted" in history
    assert "50 students have not submitted" in history
    assert all(r["store"] is False for r in fake.sent)
    assert_nothing_private_sent(fake)


def test_lists_are_never_cut_short():
    client, _ = make_client()
    fake = FakeOpenAI([
        # Even if a model tries to pass a limit, every row is shown.
        calls(call("find_records", {
            "table": "Student Profile/Biodata", "filters": [], "columns": None,
            "sort_by": None, "descending": None, "limit": 50, "title": "All"}, "l")),
        answer("All 250 students are listed below."),
    ])
    reply = Session(Tools(client), openai_client=fake).ask("List every student")
    assert len(reply.attachments[0].tables[0].rows) == 250
    assert not any("Showing first" in line for line in reply.attachments[0].summary)
    assert '"limit":' not in json.dumps(fake.sent[0]["tools"])


def test_field_values_and_zero_result_hint():
    client, _ = make_client()
    tools = Tools(client)
    schema, _ = tools.run("get_table_schema", {"table": "Student Profile/Biodata"})
    fee = next(f for f in schema["fields"] if f["name"] == "School Fees Status")
    assert fee["choices"] == ["Paid", "Not Paid", "Waived"]       # option labels are schema
    level = next(f for f in schema["fields"] if f["name"] == "Level")
    assert "choices" in level
    # A guessed value returns 0 with a hint to check the real values.
    seen, _ = tools.run("count_records", {
        "table": "Student Profile/Biodata",
        "filters": [{"field": "School Fees Status", "op": "equals", "value": "Incomplete"}],
        "group_by": None})
    assert seen["count"] == 0 and "field_values" in seen["hint"]
    # The real values, with a value only one student has kept back.
    seen, _ = tools.run("field_values", {"table": "Student Profile/Biodata",
                                         "field": "school fees status"})
    assert seen["values"] == {"Paid": 166, "Not Paid": 83}
    assert "Waived" not in json.dumps(seen) and "1 rarer value" in seen["note"]
    seen, _ = tools.run("field_values", {"table": "Student Profile/Biodata", "field": "Amount Paid"})
    assert seen["min"] == 0 and seen["max"] == 150000 and "values" not in seen
    # Personal fields never reveal values.
    for personal in ("Name", "Matric_Number", "Phone", "State of Origin"):
        seen, _ = tools.run("field_values", {"table": "Student Profile/Biodata", "field": personal})
        assert "values" not in seen, personal
        assert "personal" in seen["note"]
    seen, _ = tools.run("count_records", {
        "table": "Student Profile/Biodata",
        "filters": [{"field": "School Fees Status", "op": "equals", "value": "Not Paid"}],
        "group_by": None})
    assert seen["count"] == 83


def test_finds_payment_info_in_another_table_by_itself():
    client, http = make_client()
    tools = Tools(client)
    students, payments = http.students, http.payments

    # The student table's own Payment Status is empty for everyone...
    seen, _ = tools.run("field_values", {"table": "Student Profile/Biodata", "field": "Payment Status"})
    assert seen["empty"] == 250 and "find_fields" in seen["hint"]

    # ...so a base-wide search finds the separate fees table.
    seen, _ = tools.run("find_fields", {"keywords": ["pay", "fee"]})
    found = {(m["table"], m["field"]) for m in seen["matches"]}
    assert ("Fees Ledger 2026", "Status") in found
    assert ("Fees Ledger 2026", "Matric No") in found
    status = next(m for m in seen["matches"] if m["field"] == "Status")
    assert status["common_values"] == {"Paid": 150, "Part payment": 17}
    assert seen["matches"][-1]["filled"] == 0           # empty fields ranked last
    assert "Person0" not in json.dumps(seen) and "SPPG/" not in json.dumps(seen)

    # Students with no 'Paid' record in the ledger, matched on matric number.
    seen, attachment = tools.run("match_tables", {
        "table": "Student Profile/Biodata", "filters": [], "field": "Matric_Number",
        "other_table": "Fees Ledger 2026", "other_field": "Matric No",
        "other_filters": [{"field": "Status", "op": "equals", "value": "Paid"}],
        "mode": "not_found", "columns": None, "title": "Yet to pay"})
    paid = {p["fields"]["Matric No"] for p in payments if p["fields"]["Status"] == "Paid"}
    expected = [s for s in students if s["fields"]["Matric_Number"] not in paid]
    assert seen["not_found"] == len(expected) == len(attachment.tables[0].rows)
    assert "SPPG/" not in json.dumps(seen)

    # The same via the link field (holds student names) gives the same answer.
    seen2, _ = tools.run("match_tables", {
        "table": "Student Profile/Biodata", "filters": [], "field": "Name",
        "other_table": "Fees Ledger 2026", "other_field": "Student",
        "other_filters": [{"field": "Status", "op": "equals", "value": "Paid"}],
        "mode": "not_found", "columns": None, "title": "Yet to pay"})
    by_link = {p["fields"]["Student"][0] for p in payments if p["fields"]["Status"] == "Paid"}
    assert seen2["not_found"] == sum(1 for s in students if s["id"] not in by_link)

    # Mismatched fields get a hint instead of a silent wrong answer.
    seen3, _ = tools.run("match_tables", {
        "table": "Student Profile/Biodata", "filters": [], "field": "Name",
        "other_table": "Fees Ledger 2026", "other_field": "Matric No",
        "other_filters": [], "mode": "found", "columns": None, "title": "x"})
    assert seen3["found"] == 0 and "different" in seen3["hint"]


def test_counts_and_group_by():
    client, _ = make_client()
    fake = FakeOpenAI([
        calls(call("count_records", {
            "table": "Student Profile/Biodata",
            "filters": [{"field": "Passport", "op": "is_not_empty", "value": None}],
            "group_by": None}, "a")),
        answer("200 students have submitted."),
        calls(call("count_records", {"table": "Student Profile/Biodata", "filters": [],
                                     "group_by": "Level"}, "b"),
              call("count_records", {"table": "Student Profile/Biodata", "filters": [],
                                     "group_by": "State of Origin"}, "c")),
        answer("Done."),
    ])
    session = Session(Tools(client), openai_client=fake)
    reply = session.ask("How many students have submitted their passports?")
    assert reply.attachments == []          # a count is not the missing report
    output = json.loads(fake.sent[1]["input"][-1]["output"])
    assert output["count"] == 200

    session.ask("By level and by state of origin")
    outputs = [json.loads(i["output"]) for i in fake.sent[3]["input"]
               if i.get("type") == "function_call_output"][-2:]
    assert outputs[0]["groups"]["300"] == sum(1 for i in range(1, 251) if i % 4 == 2)
    assert isinstance(outputs[1]["groups"], str)   # private field: no values for the AI
    assert "Lagos" not in json.dumps(fake.sent)
    assert_nothing_private_sent(fake)


def test_search_and_errors_are_handled():
    client, _ = make_client()
    fake = FakeOpenAI([
        calls(call("search_records", {"term": "Person007", "tables": None}, "s"),
              call("find_records", {"table": "Students", "filters": [], "columns": None,
                                    "sort_by": None, "descending": None, "limit": None,
                                    "title": "x"}, "e")),
        answer("Found it."),
    ])
    session = Session(Tools(client), openai_client=fake)
    reply = session.ask("Find Person007")
    assert reply.attachments[0].tables[0].rows[0][1] == "Name"
    outputs = [json.loads(i["output"]) for i in fake.sent[1]["input"]
               if i.get("type") == "function_call_output"]
    assert outputs[0]["total_matches"] == 2   # student row + its ledger link
    assert "not found" in outputs[1]["error"]
    sent = json.dumps(fake.sent[1:])
    assert "Student Person007" not in sent and "SPPG/0007" not in sent


def test_failed_turn_does_not_break_memory():
    client, _ = make_client()

    class Broken(FakeOpenAI):
        def create(self, **kwargs):
            raise RuntimeError("network down")

    session = Session(Tools(client), openai_client=Broken([]))
    with pytest.raises(RuntimeError):
        session.ask("hello")
    assert session.turns == []


def test_history_is_trimmed():
    client, _ = make_client()
    fake = FakeOpenAI([answer(f"a{i}") for i in range(5)])
    session = Session(Tools(client), openai_client=fake, max_turns=2)
    for i in range(5):
        session.ask(f"q{i}")
    assert len(session.turns) == 2
    assert "q0" not in json.dumps(fake.sent[-1]["input"])


# --- Web ------------------------------------------------------------------

def test_web_is_stateless_and_remembers_via_signed_state(monkeypatch):
    import config
    from web import create_app
    monkeypatch.setattr(config, "SECRET_KEY", "shared-secret-" + "x" * 50)
    client, _ = make_client()
    fake = FakeOpenAI([answer("There are 3 tables."), answer("Yes, three.")])
    app = create_app(tools=Tools(client), openai_client=fake)
    web = app.test_client()
    assert web.get("/").status_code == 200

    r = web.post("/api/report", json={"name": "passport"}).get_json()
    assert r["attachments"][0]["summary"][2] == "Missing Passport: 50"
    assert len(r["attachments"][0]["tables"][0]["rows"]) == 50   # rows go to the browser
    r = web.post("/api/report", json={"name": "missing", "field": "Phone", "state": r["state"]}).get_json()
    assert r["attachments"][0]["title"] == "Students missing Phone"
    assert r["attachments"][0]["id"] == "R2"                     # numbering carried in state

    first = web.post("/api/chat", json={"message": "What tables are there?"}).get_json()
    assert first["text"] == "There are 3 tables."
    # A brand-new app instance (like another Vercel machine) continues the conversation.
    app2 = create_app(tools=Tools(client), openai_client=fake)
    second = app2.test_client().post("/api/chat", json={"message": "Are you sure?",
                                                       "state": first["state"]}).get_json()
    assert second["text"] == "Yes, three."
    assert "What tables are there?" in json.dumps(fake.sent[1]["input"])
    # The state token never contains record rows.
    from web import read_token
    import hashlib
    key = hashlib.sha256(b"state:" + app.secret_key.encode()).digest()
    assert "Student Person" not in json.dumps(read_token(key, second["state"]))


def test_tampered_state_is_ignored():
    import hashlib
    from web import make_token, read_token
    key = hashlib.sha256(b"k").digest()
    token = make_token(key, {"turns": [[{"role": "user", "content": "hi"}]], "counter": 1})
    assert read_token(key, token)["counter"] == 1
    data, sig = token.rsplit(".", 1)
    forged = make_token(hashlib.sha256(b"other").digest(), {"turns": [], "counter": 99})
    assert read_token(key, forged) is None
    assert read_token(key, data + "." + "0" * len(sig)) is None
    assert read_token(key, "garbage") is None


def test_vercel_requires_password(monkeypatch):
    import config
    from web import create_app
    client, _ = make_client()
    monkeypatch.setattr(config, "ON_VERCEL", True)
    monkeypatch.setattr(config, "APP_PASSWORD", None)
    monkeypatch.setattr(config, "SECRET_KEY", None)
    web = create_app(tools=Tools(client), openai_client=FakeOpenAI([])).test_client()
    assert web.get("/").status_code == 503
    r = web.post("/api/report", json={"name": "passport"})
    assert r.status_code == 503 and "APP_PASSWORD" in r.get_json()["error"]

    monkeypatch.setattr(config, "APP_PASSWORD", "s3cret-pass")
    monkeypatch.setattr(config, "SECRET_KEY", "x" * 64)
    web = create_app(tools=Tools(client), openai_client=FakeOpenAI([])).test_client()
    assert web.get("/").status_code == 302                       # sent to login
    assert web.post("/api/report", json={"name": "passport"}).status_code == 401
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert "Wrong password" in web.post("/login", data={"password": "nope"}).get_data(as_text=True)
    assert web.post("/login", data={"password": "s3cret-pass"}).status_code == 302
    assert web.post("/api/report", json={"name": "passport"}).status_code == 200
    web.get("/logout")
    assert web.post("/api/report", json={"name": "passport"}).status_code == 401


def test_vercel_entrypoint_imports(monkeypatch):
    import importlib
    monkeypatch.setattr("assistant.build_tools", lambda: None)
    module = importlib.import_module("app")
    assert module.app.name == "web"

"""Fake Airtable and OpenAI used by the tests (no network, no real keys)."""

import json
from types import SimpleNamespace

DEPARTMENTS = [("recDEP1", "Computer Science"), ("recDEP2", "Accounting")]


def make_students(n=250):
    students = []
    for i in range(1, n + 1):
        fields = {
            "Name": f"Student Person{i:03d}",
            "Matric_Number": f"SPPG/{i:04d}",
            "Level": ["100", "200", "300", "400"][i % 4],
            "Phone": f"0803{i:07d}",
            "State of Origin": "Lagos" if i % 2 else "Oyo",
            "Bank Account Number": f"99{i:08d}",
            "Department": [DEPARTMENTS[i % 2][0]],
            "Onboarding Status": "Onboarded" if i % 4 else "Pending",
            "School Fees Status": "Waived" if i == 1 else ("Paid" if i % 3 else "Not Paid"),
            "Amount Paid": 150000 if i % 3 else 0,
            "Capstone Projecty Signed Code of Ethics": "signed" if i % 2 else "",
            "Capstone Projecty Signed Code of Ethics copy": "copy-a",
            "Capstone Projecty Signed Code of Ethics Copy ": "copy-b",
        }
        if i % 5 != 0:                       # every 5th student has no passport
            fields["Passport"] = [{"id": f"att{i}", "url": "https://x", "filename": f"pp{i}.jpg"}]
        flags = []
        if i % 5 == 0 or i % 7 == 0:         # some with passports are still flagged
            flags.append("Passport")
        if i % 3 == 0:
            flags.append("Birth Certificate")
        if flags:
            fields["Missing Fields"] = flags
        students.append({"id": f"recS{i:04d}", "fields": fields})
    students[9]["fields"]["Matric_Number"] = students[8]["fields"]["Matric_Number"]  # duplicate
    return students


def make_payments(students):
    """A separate fees table: most students have a Paid record, some part payments."""
    payments = []
    for i, s in enumerate(students, 1):
        if i % 3 == 0:
            continue                                    # no payment record at all
        status = "Part payment" if i % 10 == 0 else "Paid"
        payments.append({"id": f"recP{i:04d}", "fields": {
            "Payment Ref": f"PAY-{i:04d}",
            "Student": [s["id"]],
            "Matric No": s["fields"]["Matric_Number"],
            "Status": status,
            "Amount": 150000 if status == "Paid" else 50000,
        }})
    return payments


SCHEMA = {"tables": [
    {
        "id": "tblSTU", "name": "Student Profile/Biodata", "primaryFieldId": "fldName",
        "fields": [
            {"id": "fldName", "name": "Name", "type": "singleLineText"},
            {"id": "fldMat", "name": "Matric_Number", "type": "singleLineText"},
            {"id": "fldLvl", "name": "Level", "type": "singleSelect",
             "options": {"choices": [{"name": v} for v in ("100", "200", "300", "400")]}},
            {"id": "fldPP", "name": "Passport", "type": "multipleAttachments"},
            {"id": "fldMF", "name": "Missing Fields", "type": "multipleSelects"},
            {"id": "fldPh", "name": "Phone", "type": "phoneNumber"},
            {"id": "fldSO", "name": "State of Origin", "type": "singleLineText"},
            {"id": "fldBank", "name": "Bank Account Number", "type": "singleLineText"},
            {"id": "fldDep", "name": "Department", "type": "multipleRecordLinks",
             "options": {"linkedTableId": "tblDEP"}},
            {"id": "fldOnb", "name": "Onboarding Status", "type": "singleSelect",
             "options": {"choices": [{"name": "Onboarded"}, {"name": "Pending"}]}},
            {"id": "fldFee", "name": "School Fees Status", "type": "singleSelect",
             "options": {"choices": [{"name": "Paid"}, {"name": "Not Paid"}, {"name": "Waived"}]}},
            {"id": "fldAmt", "name": "Amount Paid", "type": "currency"},
            {"id": "fldPay", "name": "Payment Status", "type": "singleSelect",
             "options": {"choices": [{"name": "Paid"}, {"name": "Not paid"}]}},
            {"id": "fldCap", "name": "Capstone Projecty Signed Code of Ethics", "type": "singleLineText"},
            # Two near-identical duplicated fields, like the real base.
            {"id": "fldCap2", "name": "Capstone Projecty Signed Code of Ethics copy", "type": "singleLineText"},
            {"id": "fldCap3", "name": "Capstone Projecty Signed Code of Ethics Copy ", "type": "singleLineText"},
        ],
    },
    {
        "id": "tblDEP", "name": "Departments", "primaryFieldId": "fldDName",
        "fields": [{"id": "fldDName", "name": "Name", "type": "singleLineText"}],
    },
    {
        "id": "tblFEE", "name": "Fees Ledger 2026", "primaryFieldId": "fldRef",
        "fields": [
            {"id": "fldRef", "name": "Payment Ref", "type": "singleLineText"},
            {"id": "fldStu", "name": "Student", "type": "multipleRecordLinks",
             "options": {"linkedTableId": "tblSTU"}},
            {"id": "fldMNo", "name": "Matric No", "type": "singleLineText"},
            {"id": "fldSt", "name": "Status", "type": "singleSelect",
             "options": {"choices": [{"name": "Paid"}, {"name": "Part payment"}]}},
            {"id": "fldAm", "name": "Amount", "type": "currency"},
        ],
    },
]}


class FakeResponse:
    def __init__(self, status, data=None, headers=None):
        self.status_code = status
        self._data = data or {}
        self.headers = headers or {}
        self.text = json.dumps(self._data)

    def json(self):
        return self._data


class FakeAirtableHTTP:
    def __init__(self, students=None, fail_first_with_429=False):
        self.headers = {}
        self.calls = []
        self.requested_fields = []
        self.students = students or make_students()
        self.departments = [{"id": i, "fields": {"Name": n}} for i, n in DEPARTMENTS]
        self.payments = make_payments(self.students)
        self.rows = {"tblSTU": self.students, "tblDEP": self.departments, "tblFEE": self.payments}
        self.pending_429 = fail_first_with_429

    def request(self, method, url, json=None, params=None, timeout=None):
        self.calls.append((method, url))
        if self.pending_429:
            self.pending_429 = False
            return FakeResponse(429, {"error": "RATE_LIMIT"})
        if url.endswith("/tables"):
            return FakeResponse(200, SCHEMA)
        table_id = url.split("/")[-1]
        table = next(t for t in SCHEMA["tables"] if t["id"] == table_id)
        by_name = {f["name"]: f["id"] for f in table["fields"]}
        rows = self.rows[table_id]
        params = params or []
        get = lambda key, default=None: next((v for k, v in params if k == key), default)
        requested = [v for k, v in params if k == "fields[]"]
        self.requested_fields.append((table_id, requested))
        # Like Airtable: names that match more than one field are ambiguous.
        for item in requested:
            if not item.startswith("fld"):
                hits = [n for n in by_name if " ".join(n.split()).lower() == " ".join(item.split()).lower()]
                if len(hits) > 1:
                    return FakeResponse(422, {"error": {"type": "INVALID_REQUEST",
                                                        "message": f"Ambiguous field name {item}"}})
        wanted = set(requested) if requested else set(by_name.values())
        by_id = get("returnFieldsByFieldId") == "true"
        start = int(get("offset", 0))
        page = rows[start:start + int(get("pageSize", 100))]
        out = {"records": []}
        for r in page:
            cells = {}
            for name, value in r["fields"].items():
                fid = by_name.get(name)
                if fid in wanted:
                    cells[fid if by_id else name] = value
            out["records"].append({"id": r["id"], "fields": cells})
        if start + 100 < len(rows):
            out["offset"] = str(start + 100)
        return FakeResponse(200, out)


def call(name, args, call_id):
    return SimpleNamespace(type="function_call", name=name,
                           arguments=json.dumps(args), call_id=call_id)


def answer(text):
    return SimpleNamespace(output=[SimpleNamespace(type="message")], output_text=text)


def calls(*items):
    return SimpleNamespace(output=list(items), output_text="")


class FakeOpenAI:
    """Returns scripted responses in order and records everything sent."""

    def __init__(self, script):
        self.script = list(script)
        self.sent = []
        self.responses = self

    def create(self, **kwargs):
        self.sent.append(json.loads(json.dumps(kwargs, default=str)))
        return self.script.pop(0)

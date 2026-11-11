"""
The AI assistant.

How student data is kept away from OpenAI
-----------------------------------------
OpenAI decides WHAT to look up (which table, which filters, which report),
but the lookup runs here in Python and the records it finds are shown to
you directly. OpenAI only gets back counts, e.g. "42 records found, shown
to the user", plus category values so it can filter correctly: dropdown
option labels, and values shared by at least MIN_SHARED_COUNT records in
fields that are not personal (e.g. "Paid: 166, Not Paid: 83"). It never
receives names, matric numbers, phone numbers or other personal fields, or
any value so rare it could point to one student.

What IS sent to OpenAI: your questions, table and field names, the counts
above, and the conversation so far. Conversations are sent with store=False
so OpenAI does not keep them for later retrieval.

Conversation memory
-------------------
Each Session keeps its own history (questions, the AI's lookups, and its
answers), so follow-ups like "now only the 300-level ones" work. Only the
last MAX_HISTORY_TURNS questions are kept.
"""

import json
import threading
from collections import OrderedDict
from dataclasses import dataclass, field

import config
import reports
from airtable_client import AirtableError
from privacy import MIN_SHARED_COUNT, MODEL, looks_personal
from query import (OPERATORS, QueryError, apply_filters, as_text,
                   group_counts, resolve_field, sort_rows)
from results import Attachment, Table

MAX_TOOL_ROUNDS = 16
MAX_FIELD_MATCHES = 40
NUMBER_TYPES = {"number", "currency", "percent", "rating", "duration", "count", "autoNumber"}
TYPE_NOTES = {
    "checkbox": "ticked = 'Yes', unticked = empty (use is_empty / is_not_empty)",
    "multipleAttachments": "empty = no file uploaded",
    "multipleRecordLinks": "values are the linked records' names",
    "formula": "computed text/number",
}
MAX_ATTACHMENTS_KEPT = 50

# ---------------------------------------------------------------------------
# Tool definitions shown to the AI
# ---------------------------------------------------------------------------

_FILTERS = {
    "type": "array",
    "description": (
        "Conditions that must all be true. Use [] for none. For one_of, "
        "separate options with |. For is_empty / is_not_empty use value null."
    ),
    "items": {
        "type": "object",
        "properties": {
            "field": {"type": "string"},
            "op": {"type": "string", "enum": OPERATORS},
            "value": {"type": ["string", "null"]},
        },
        "required": ["field", "op", "value"],
        "additionalProperties": False,
    },
}


def _tool(name, description, properties):
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
        "strict": True,
    }


TOOLS = [
    _tool("list_tables", "List the tables in the Airtable base.", {}),
    _tool(
        "get_table_schema",
        "Get a table's fields, their types and privacy level. Call this before "
        "filtering a table you have not inspected in this conversation.",
        {"table": {"type": "string"}},
    ),
    _tool(
        "field_values",
        "See which values a field actually contains and how many records have "
        "each (e.g. a payment status field: Paid 120, Owing 355, empty 40). "
        "Use BEFORE filtering on a value you have not seen, and whenever a "
        "filter returns 0 unexpectedly. Values that identify a person, or "
        f"that fewer than {MIN_SHARED_COUNT} records share, are not revealed.",
        {"table": {"type": "string"}, "field": {"type": "string"}},
    ),
    _tool(
        "find_fields",
        "Search the WHOLE base for where some information is stored. Matches "
        "keywords against every table name, field name and shared field value "
        "(e.g. ['pay', 'fee', 'tuition', 'receipt', 'amount', 'bursary']). "
        "Returns each matching table/field with how many records are filled "
        "and its common values. Use whenever you are not sure which table or "
        "field holds the answer, or a field you tried is empty.",
        {"keywords": {"type": "array", "items": {"type": "string"}}},
    ),
    _tool(
        "match_tables",
        "Cross-check two tables and SHOW THE USER every record of `table` whose "
        "`field` value is (mode 'found') or is not (mode 'not_found') present in "
        "`other_field` of `other_table`, counting only other_table records that "
        "pass other_filters. Example: students with no 'Paid' record in a "
        "payments table -> table=students, field=Matric_Number, other_table="
        "payments, other_field=its matric/student field, other_filters=[status "
        "equals Paid], mode=not_found. Link fields hold the linked record's "
        "primary-field value (often the student's name), so match them against "
        "that field. You get back counts only.",
        {
            "table": {"type": "string"},
            "filters": _FILTERS,
            "field": {"type": "string"},
            "other_table": {"type": "string"},
            "other_field": {"type": "string"},
            "other_filters": _FILTERS,
            "mode": {"type": "string", "enum": ["found", "not_found"]},
            "columns": {"type": ["array", "null"], "items": {"type": "string"}},
            "title": {"type": "string"},
        },
    ),
    _tool(
        "count_records",
        "Count records matching filters, optionally grouped by a field. Use for "
        "'how many' questions. If group_by is a private field, the breakdown is "
        "shown to the user and you only get the number of groups.",
        {
            "table": {"type": "string"},
            "filters": _FILTERS,
            "group_by": {"type": ["string", "null"]},
        },
    ),
    _tool(
        "find_records",
        "Find records and SHOW THEM TO THE USER as a table under your reply. "
        "The table always contains EVERY matching record (there is no limit). "
        "You get back only the number of rows, never their contents. Use for "
        "'list', 'show', 'which students' questions. To narrow an earlier list, "
        "call again with the earlier filters plus the new ones.",
        {
            "table": {"type": "string"},
            "filters": _FILTERS,
            "columns": {
                "type": ["array", "null"],
                "items": {"type": "string"},
                "description": "Fields to show. null = name/ID fields plus the filtered fields.",
            },
            "sort_by": {"type": ["string", "null"]},
            "descending": {"type": ["boolean", "null"]},
            "title": {"type": "string", "description": "Short title for the table."},
        },
    ),
    _tool(
        "search_records",
        "Search for text across all tables (or the listed ones) and show the "
        "matching records to the user. You get back match counts per table.",
        {
            "term": {"type": "string"},
            "tables": {"type": ["array", "null"], "items": {"type": "string"}},
        },
    ),
    _tool(
        "run_report",
        "Run a built-in report and show it to the user. You get back only its "
        "counts. Reports: "
        + "; ".join(f"{k}: {v[1]}" for k, v in reports.REPORTS.items())
        + ". For passports use missing_field with field 'Passport'. table null = "
        "the student table.",
        {
            "report": {"type": "string", "enum": list(reports.REPORTS)},
            "table": {"type": ["string", "null"]},
            "field": {"type": ["string", "null"]},
        },
    ),
]

ZERO_HINT = (
    "No records matched. Before telling the user there are none, check the "
    "real values with field_values (the value may be spelled differently), "
    "and use find_fields to search the whole base: the information may be "
    "stored in another field or another table."
)

INSTRUCTIONS = f"""
You are the AI Operations Assistant for the SPPG Airtable base. You help
staff look up and understand student and operational records.

PRIVACY - HOW YOUR TOOLS WORK
- You never see record contents (names, matric numbers, passport files,
  contact details...). Your tools run lookups locally and show the matching
  records directly to the user as tables under your reply.
- You receive only counts, plus category values that several records share
  (e.g. "Paid: 120") in fields that are not personal.
- So never list, guess or invent names or other record values. Refer to the
  table shown below your reply instead ("The 37 students are listed below").
- If the user asks you to reveal or repeat values, explain that the records
  are shown in the table below and are not shared with the AI.

HOW TO ANSWER
- "How many..." -> count_records. "List / show / which..." -> find_records.
- Missing documents (e.g. passports) -> run_report missing_field with the
  field name. A question like "how many HAVE submitted passports" is a
  count_records with is_not_empty, not the missing report.
- Field names must match the schema; call get_table_schema when unsure.
- Never guess a field's values. Before filtering on a value (e.g.
  "Not Paid", "Owing", "Onboarded"), check the schema choices or call
  field_values, and use the exact spelling found. "Yet to pay" might be a
  status value, an empty payment/receipt field, an unticked checkbox, or
  an amount below the fee; look at the relevant fields (and other tables,
  e.g. a payments table) to decide.
- The main student table is "{config.STUDENT_TABLE}", but information
  may live in ANY table. Never ask the user which table or field to use:
  find it yourself with find_fields (try several related keywords) and
  get_table_schema / field_values on what it finds.
- If a field is empty for every record, or a filter returns 0, that field
  is probably not where the information is kept. Do not stop or ask: run
  find_fields across the base, inspect the other candidates, and answer
  from the one that actually holds data. When the information is in a
  separate table (e.g. a payments table), use match_tables to compare it
  with the student table (e.g. students with no payment record).
- Never report "none" or 0 until you have searched the whole base this way.
- In your reply, say briefly which table and field the answer came from
  and how you interpreted it (e.g. "students with no 'Paid' record in the
  Fees Payments table"), so the user can check it.
- Ask the user only if, after searching, two sources genuinely disagree or
  the meaning is unclear; then say what you found in each.
- Follow-up questions refer to earlier ones: reuse the earlier table,
  fields and filters (or match_tables call) and add the new condition.
- Every list you show contains ALL matching records; never say a list is
  partial or offer "the next" records. Only describe a table as complete
  if a tool call in this conversation actually produced it; when the user
  asks for a list, call find_records again rather than pointing back to an
  earlier answer.
- If a tool returns an error, fix the call (e.g. correct field name) and
  try again, or explain the problem.
- Access is read-only; you cannot change Airtable.
- Keep replies short and clear; state the counts the tools returned.
"""


@dataclass
class Reply:
    text: str
    attachments: list = field(default_factory=list)

    def to_dict(self):
        return {"text": self.text, "attachments": [a.to_dict() for a in self.attachments]}


# ---------------------------------------------------------------------------
# Tool execution (shared by all sessions)
# ---------------------------------------------------------------------------

class Tools:
    """Runs tool calls. Returns (what the AI sees, attachment for the user or None)."""

    def __init__(self, client):
        self.client = client

    def run(self, name, args):
        handler = getattr(self, f"_{name}", None)
        if handler is None:
            return {"error": f"Unknown tool '{name}'."}, None
        try:
            return handler(**args)
        except (AirtableError, QueryError, ValueError, TypeError) as error:
            return {"error": str(error)}, None
        except Exception as error:  # keep the conversation going
            return {"error": f"{type(error).__name__}: {error}"}, None

    # -- helpers --

    def _table(self, table):
        return self.client.find_table(table)["name"]

    def _field_names(self, table):
        return [f["name"] for f in self.client.fields(table)]

    def _levels(self, table):
        return {f["name"]: f["level"] for f in self.client.fields(table)}

    def _filter_summary(self, filters):
        return [
            f"{f['field']} {f['op'].replace('_', ' ')}" + (f" {f['value']}" if f.get("value") else "")
            for f in filters or []
        ]

    # -- tools --

    def _list_tables(self):
        return {"tables": [
            {"name": t["name"], "fields": len(self.client.fields(t["name"]))}
            for t in self.client.tables()
        ]}, None

    def _get_table_schema(self, table):
        table = self._table(table)
        fields = []
        for f in self.client.fields(table):
            entry = {
                "name": f["name"],
                "type": f["type"],
                "visibility": "model" if f["level"] == MODEL else "private",
            }
            choices = (f.get("options") or {}).get("choices")
            if choices and (f["level"] == MODEL or not looks_personal(f["name"])):
                entry["choices"] = [c.get("name") for c in choices[:50]]
            if f["type"] in TYPE_NOTES:
                entry["note"] = TYPE_NOTES[f["type"]]
            fields.append(entry)
        return {
            "table": table,
            "primary_field": self.client.primary_field(table),
            "fields": fields,
            "blocked_fields_hidden": self.client.blocked_count(table),
            "note": ("private = you never see individual values; use field_values "
                     "to see which shared values a field contains."),
        }, None

    def _shareable(self, table, name, counts):
        """
        Value counts the AI may see. All of them for "model" fields; for other
        fields only values shared by several records, never for personal fields.
        """
        level = self._levels(table)[name]
        if level == MODEL:
            return counts, None
        if looks_personal(name):
            return None, (f"'{name}' holds personal details, so its values are not "
                          "shared. Filter it with values the user gave you, or "
                          "is_empty / is_not_empty.")
        shared = {v: n for v, n in counts.items() if n >= MIN_SHARED_COUNT or v == "(empty)"}
        rare = {v: n for v, n in counts.items() if v not in shared}
        top = dict(list(shared.items())[:40])
        notes = []
        if rare:
            notes.append(f"{len(rare)} rarer values ({sum(rare.values())} records) not shown")
        if len(shared) > len(top):
            notes.append(f"{len(shared) - len(top)} more shared values not shown")
        return top, "; ".join(notes) or None

    def _field_values(self, table, field):
        table = self._table(table)
        name = resolve_field(self._field_names(table), field)
        meta = next(f for f in self.client.fields(table) if f["name"] == name)
        rows = self.client.records(table)
        counts = group_counts(rows, name)
        values, note = self._shareable(table, name, counts)
        result = {
            "table": table, "field": name, "type": meta["type"],
            "records": len(rows), "empty": counts.get("(empty)", 0),
            "distinct_values": len([v for v in counts if v != "(empty)"]),
        }
        if meta["type"] in TYPE_NOTES:
            result["type_note"] = TYPE_NOTES[meta["type"]]
        if values is not None:
            result["values"] = values
        if meta["type"] in NUMBER_TYPES:
            numbers = []
            for r in rows:
                try:
                    numbers.append(float(str(as_text(r["fields"].get(name))).replace(",", "")))
                except ValueError:
                    pass
            if numbers:
                result["min"], result["max"] = min(numbers), max(numbers)
                result.pop("values", None)
                note = "numeric field: compare with greater_than / less_than / at_least / at_most"
        if note:
            result["note"] = note
        if result["empty"] == len(rows) and rows:
            result["hint"] = ("This field is empty in every record, so the information is "
                              "probably kept elsewhere. Search the base with find_fields.")
        return result, None

    def _find_fields(self, keywords):
        words = [w.strip().lower() for w in keywords or [] if w and w.strip()]
        if not words:
            return {"error": "Give at least one keyword."}, None
        matches, tables_scanned = [], 0
        for t in self.client.tables():
            table = t["name"]
            fields = self.client.fields(table)
            table_hit = any(w in table.lower() for w in words)
            rows = None
            tables_scanned += 1
            for f in fields:
                name = f["name"]
                name_hit = any(w in name.lower() for w in words)
                if rows is None:
                    rows = self.client.records(table)
                counts = group_counts(rows, name)
                shared, _ = self._shareable(table, name, counts)
                value_hit = bool(shared) and any(
                    w in str(v).lower() for v in shared if v != "(empty)" for w in words
                )
                if not (table_hit or name_hit or value_hit):
                    continue
                entry = {
                    "table": table, "field": name, "type": f["type"],
                    "records": len(rows),
                    "filled": len(rows) - counts.get("(empty)", 0),
                    "matched_on": "field name" if name_hit else ("values" if value_hit else "table name"),
                }
                if shared:
                    common = {v: n for v, n in shared.items() if v != "(empty)"}
                    entry["common_values"] = dict(list(common.items())[:10])
                if f["type"] == "multipleRecordLinks":
                    linked = (f.get("options") or {}).get("linkedTableId")
                    try:
                        entry["links_to_table"] = self.client.find_table(linked)["name"]
                    except AirtableError:
                        pass
                matches.append(entry)
        # Fields that actually hold data first, field-name matches before others.
        rank = {"field name": 0, "values": 1, "table name": 2}
        matches.sort(key=lambda m: (m["filled"] == 0, rank[m["matched_on"]]))
        result = {
            "keywords": words, "tables_scanned": tables_scanned,
            "matches": matches[:MAX_FIELD_MATCHES],
        }
        if len(matches) > MAX_FIELD_MATCHES:
            result["note"] = f"{len(matches) - MAX_FIELD_MATCHES} more matches; use narrower keywords."
        if not matches:
            result["note"] = "Nothing matched. Try other related keywords, or list_tables."
        return result, None

    def _match_tables(self, table, filters, field, other_table, other_field,
                      other_filters, mode, columns=None, title=None):
        table, other_table = self._table(table), self._table(other_table)
        names, other_names = self._field_names(table), self._field_names(other_table)
        field = resolve_field(names, field)
        other_field = resolve_field(other_names, other_field)

        def keys(value):
            items = value if isinstance(value, list) else [value]
            return {" ".join(str(i).split()).lower() for i in items if str(i or "").strip()}

        other_rows = apply_filters(self.client.records(other_table), other_filters, other_names)
        present = set()
        for row in other_rows:
            present |= keys(row["fields"].get(other_field))

        rows = apply_filters(self.client.records(table), filters, names)
        no_value = [r for r in rows if not keys(r["fields"].get(field))]
        checkable = [r for r in rows if keys(r["fields"].get(field))]
        found = [r for r in checkable if keys(r["fields"].get(field)) & present]
        not_found = [r for r in checkable if not keys(r["fields"].get(field)) & present]
        chosen = found if mode == "found" else not_found

        if columns:
            shown = [resolve_field(names, c) for c in columns]
        else:
            shown = reports._identity_fields(self.client, table)
            if field not in shown:
                shown.append(field)
        describe = (f"{field} {'found' if mode == 'found' else 'not found'} in "
                    f"{other_table} › {other_field}")
        summary = [f"Table: {table}", f"Rule: {describe}"]
        summary += [f"Filter ({other_table}): {s}" for s in self._filter_summary(other_filters)]
        summary += [f"Filter ({table}): {s}" for s in self._filter_summary(filters)]
        summary += [f"Matching records: {len(chosen)}"]
        tables = [Table("Records", shown, [[as_text(r["fields"].get(c)) for c in shown] for r in chosen])]
        if no_value:
            tables.append(Table(
                f"Could not check: {field} is empty", shown,
                [[as_text(r["fields"].get(c)) for c in shown] for r in no_value],
                note="Not counted above.",
            ))
        attachment = Attachment(title or describe, summary, tables)
        result = {
            "table": table, "rule": describe,
            "records_checked": len(rows),
            f"{other_table}_records_considered": len(other_rows),
            "found": len(found), "not_found": len(not_found),
            "could_not_check_empty_field": len(no_value),
            "rows_shown_to_user": len(chosen),
            "complete_list": True,
            "note": "The rows are displayed to the user under your reply. You cannot see them.",
        }
        if not other_rows:
            result["hint"] = (f"No {other_table} records passed other_filters; check its "
                              "values with field_values.")
        elif not found and checkable:
            result["hint"] = ("Nothing matched between the two fields. They may hold different "
                              "kinds of values (e.g. names vs matric numbers); pick fields that "
                              "hold the same thing.")
        return result, attachment

    def _count_records(self, table, filters, group_by=None):
        table = self._table(table)
        names = self._field_names(table)
        rows = apply_filters(self.client.records(table), filters, names)
        result = {"table": table, "filters": self._filter_summary(filters), "count": len(rows)}
        if not rows and filters:
            result["hint"] = ZERO_HINT
        if not group_by:
            return result, None
        group_by = resolve_field(names, group_by)
        groups = group_counts(rows, group_by)
        attachment = Attachment(
            f"{table}: count by {group_by}",
            [f"Records: {len(rows)}"] + [f"Filter: {s}" for s in self._filter_summary(filters)],
            [Table(f"By {group_by}", [group_by, "Records"], [[k, str(v)] for k, v in groups.items()])],
        )
        shared, note = self._shareable(table, group_by, groups)
        if shared is not None:
            result["groups"] = shared
            if note:
                result["groups_note"] = note + " (the user sees every group)"
        else:
            result["groups"] = f"{len(groups)} groups, shown to the user (personal field)"
        return result, attachment

    def _find_records(self, table, filters, columns=None, sort_by=None,
                      descending=None, title=None, **_ignored):
        table = self._table(table)
        names = self._field_names(table)
        rows = apply_filters(self.client.records(table), filters, names)
        if sort_by:
            rows = sort_rows(rows, resolve_field(names, sort_by), bool(descending))
        total = len(rows)   # every matching row is always shown

        if columns:
            shown = [resolve_field(names, c) for c in columns]
        else:
            shown = reports._identity_fields(self.client, table)
            for f in filters or []:
                name = resolve_field(names, f["field"])
                if name not in shown:
                    shown.append(name)
        attachment = Attachment(
            title or f"Records from {table}",
            [f"Table: {table}", f"Matching records: {total}"]
            + [f"Filter: {s}" for s in self._filter_summary(filters)],
            [Table("Records", shown, [[as_text(r["fields"].get(c)) for c in shown] for r in rows])],
        )
        return {
            "table": table,
            "filters": self._filter_summary(filters),
            "matching_records": total,
            "rows_shown_to_user": len(rows),
            "complete_list": True,
            **({"hint": ZERO_HINT} if total == 0 and filters else {}),
            "columns_shown": shown,
            "note": "The rows are displayed to the user under your reply. You cannot see them.",
        }, attachment

    def _search_records(self, term, tables=None):
        term_clean = (term or "").strip().lower()
        if not term_clean:
            return {"error": "Give a search term."}, None
        targets = [self._table(t) for t in tables] if tables else [t["name"] for t in self.client.tables()]
        sections, counts, scanned = [], {}, 0
        for table in targets:
            rows = self.client.records(table)
            scanned += len(rows)
            ident = reports._identity_fields(self.client, table)
            hits = []
            for row in rows:
                for name, value in row["fields"].items():
                    text = as_text(value)
                    if term_clean in text.lower():
                        label = " / ".join(as_text(row["fields"].get(f)) or "—" for f in ident)
                        snippet = text if len(text) <= 200 else text[:200] + "…"
                        hits.append([label, name, snippet])
            if hits:
                counts[table] = len(hits)
                sections.append(Table(table, ["Record", "Found in", "Value"], hits))
        attachment = Attachment(
            f"Search: {term.strip()}",
            [f"Tables searched: {len(targets)}", f"Records scanned: {scanned}",
             f"Matches: {sum(counts.values())}"],
            sections,
        ) if sections else None
        return {
            "tables_searched": len(targets),
            "records_scanned": scanned,
            "matches_by_table": counts,
            "total_matches": sum(counts.values()),
            "note": "Matches are displayed to the user. You cannot see them." if sections else "No matches.",
        }, attachment

    def _run_report(self, report, table=None, field=None):
        attachment, summary = reports.run(self.client, report, table, field)
        summary["note"] = "The full report is displayed to the user under your reply."
        return summary, attachment


# ---------------------------------------------------------------------------
# A conversation
# ---------------------------------------------------------------------------

class Session:

    def __init__(self, tools, openai_client=None, model=None, max_turns=None):
        self.tools = tools
        self._openai = openai_client
        self.model = model or config.OPENAI_MODEL
        self.max_turns = max_turns or config.MAX_HISTORY_TURNS
        self.turns = []                      # list of lists of input items
        self.attachments = OrderedDict()
        self._counter = 0
        self.lock = threading.Lock()

    @property
    def openai(self):
        if self._openai is None:
            from openai import OpenAI
            if not config.OPENAI_API_KEY:
                raise RuntimeError(
                    "OPENAI_API_KEY is not set. Add it to .env (or to Vercel's "
                    "Environment Variables and redeploy)."
                )
            self._openai = OpenAI(api_key=config.OPENAI_API_KEY)
        return self._openai

    def reset(self):
        with self.lock:
            self.turns = []

    # The web app keeps no memory on the server (needed for Vercel), so the
    # conversation is handed to the browser between requests and back again.
    # It contains questions, the AI's lookups and counts - never record rows.
    def export_state(self):
        return {"turns": self.turns, "counter": self._counter}

    def load_state(self, state):
        state = state or {}
        turns = state.get("turns") or []
        if isinstance(turns, list) and all(isinstance(t, list) for t in turns):
            self.turns = turns[-self.max_turns:]
        counter = state.get("counter")
        self._counter = counter if isinstance(counter, int) and counter >= 0 else 0

    def _keep(self, attachment):
        self._counter += 1
        attachment.id = f"R{self._counter}"
        self.attachments[attachment.id] = attachment
        while len(self.attachments) > MAX_ATTACHMENTS_KEPT:
            self.attachments.popitem(last=False)
        return attachment

    def run_report(self, name, table=None, field=None):
        """Run a report directly, without the AI."""
        with self.lock:
            attachment, _ = reports.run(self.tools.client, name, table, field)
            return Reply("", [self._keep(attachment)])

    def ask(self, question):
        with self.lock:
            return self._ask(question)

    def _ask(self, question):
        turn = [{"role": "user", "content": question}]
        new_attachments = []

        for _ in range(MAX_TOOL_ROUNDS):
            history = [item for past in self.turns for item in past] + turn
            response = self.openai.responses.create(
                model=self.model,
                instructions=INSTRUCTIONS,
                input=history,
                tools=TOOLS,
                store=False,
            )
            calls = [item for item in response.output if item.type == "function_call"]
            if not calls:
                text = response.output_text or ""
                turn.append({"role": "assistant", "content": text})
                self._remember(turn)
                return Reply(text, new_attachments)

            for call in calls:
                turn.append({
                    "type": "function_call",
                    "call_id": call.call_id,
                    "name": call.name,
                    "arguments": call.arguments,
                })
                try:
                    args = json.loads(call.arguments or "{}")
                except json.JSONDecodeError:
                    args = None
                if args is None:
                    seen, attachment = {"error": "Arguments were not valid JSON."}, None
                else:
                    seen, attachment = self.tools.run(call.name, args)
                if attachment is not None:
                    seen["result_id"] = self._keep(attachment).id
                    new_attachments.append(attachment)
                turn.append({
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(seen, ensure_ascii=False, default=str),
                })

        text = "I needed too many steps for that. Please try a more specific question."
        turn.append({"role": "assistant", "content": text})
        self._remember(turn)
        return Reply(text, new_attachments)

    def _remember(self, turn):
        self.turns.append(turn)
        self.turns = self.turns[-self.max_turns:]


def explain_error(error):
    """Turn library errors into a message staff can act on."""
    name = type(error).__name__
    cause = error.__cause__ or error.__context__
    if name == "APIConnectionError":
        detail = f" ({type(cause).__name__}: {cause})" if cause else ""
        if cause and "header" in str(cause).lower():
            return ("Could not contact OpenAI: the OPENAI_API_KEY value contains a space "
                    "or line break. Re-enter it without extra characters and redeploy.")
        return ("Could not contact OpenAI. Check the internet connection and that "
                f"OPENAI_API_KEY is correct{detail}.")
    if name == "AuthenticationError":
        return "OpenAI rejected the API key. Check OPENAI_API_KEY (it may be wrong or revoked)."
    if name == "RateLimitError":
        return ("OpenAI says the limit or credit is used up. Check billing and usage "
                "at platform.openai.com, then try again.")
    if name == "NotFoundError" and "model" in str(error).lower():
        return f"OpenAI does not recognise the model '{config.OPENAI_MODEL}'. Check OPENAI_MODEL."
    if name == "APITimeoutError":
        return "OpenAI took too long to answer. Please try again."
    return str(error) or name


def build_tools():
    """Create the shared Airtable connection and tool runner from .env settings."""
    from airtable_client import AirtableClient
    from privacy import PrivacyPolicy

    if config.PRIVACY_JSON:
        import json as _json
        policy = PrivacyPolicy(data=_json.loads(config.PRIVACY_JSON))
    else:
        policy = PrivacyPolicy(config.PRIVACY_FILE)
    client = AirtableClient(
        config.AIRTABLE_TOKEN, config.AIRTABLE_BASE_ID, policy,
        cache_seconds=config.CACHE_SECONDS,
        schema_cache_seconds=config.SCHEMA_CACHE_SECONDS,
    )
    return Tools(client)

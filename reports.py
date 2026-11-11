"""
Built-in reports. Each report returns:
  - an Attachment with the full lists, shown to the user only, and
  - a small summary of counts, which is all the AI gets to see.

Add a new report by writing a function with the same shape and listing it
in REPORTS at the bottom.
"""

import re
from collections import Counter, defaultdict

import config
from query import as_text, is_empty
from results import Attachment, Table


def _identity_fields(client, table):
    """Fields used to identify a record in lists: primary field + ID field."""
    names = [f["name"] for f in client.fields(table)]
    lower = {n.lower(): n for n in names}
    chosen = []
    for wanted in (config.STUDENT_NAME_FIELD, client.primary_field(table), config.STUDENT_ID_FIELD):
        name = lower.get((wanted or "").lower())
        if name and name not in chosen:
            chosen.append(name)
    return chosen or names[:1]


def _cells(row, fields):
    return [as_text(row["fields"].get(f)) or "—" for f in fields]


def _flag_items(value):
    if isinstance(value, list):
        items = value
    else:
        items = re.split(r"[,;\n]+", value or "")
    return [i.strip() for i in items if i and i.strip()]


def _flags_field(client, table):
    names = {f["name"].lower(): f["name"] for f in client.fields(table)}
    return names.get((config.MISSING_FLAGS_FIELD or "").lower())


# ---------------------------------------------------------------------------

def missing_field(client, table=None, field=None):
    """Records where a field (e.g. Passport) is empty."""
    table = client.find_table(table or config.STUDENT_TABLE)["name"]
    if not field:
        raise ValueError("Say which field to check, e.g. Passport.")
    field = client.find_field(table, field)["name"]
    rows = client.records(table)
    ident = _identity_fields(client, table)
    flags = _flags_field(client, table)

    missing = [r for r in rows if is_empty(r["fields"].get(field))]
    flagged_but_present, empty_not_flagged = [], []
    if flags:
        def flagged(row):
            return any(field.lower() in item.lower() for item in _flag_items(row["fields"].get(flags)))
        flagged_but_present = [r for r in rows if not is_empty(r["fields"].get(field)) and flagged(r)]
        empty_not_flagged = [r for r in missing if not flagged(r)]

    tables = [Table(f"Missing {field}", ident, [_cells(r, ident) for r in missing])]
    summary = [
        f"Table: {table}",
        f"Records checked: {len(rows)}",
        f"Missing {field}: {len(missing)}",
    ]
    if flags:
        tables.append(Table(
            f"Has {field} but '{flags}' still lists it", ident,
            [_cells(r, ident) for r in flagged_but_present],
            note="Data inconsistency: not counted as missing.",
        ))
        summary.append(f"Inconsistent (has {field}, still flagged): {len(flagged_but_present)}")
        if empty_not_flagged:
            tables.append(Table(
                f"Missing {field} but not listed in '{flags}'", ident,
                [_cells(r, ident) for r in empty_not_flagged],
                note="These are included in the missing count above.",
            ))
            summary.append(f"Missing but not flagged: {len(empty_not_flagged)}")

    model = {
        "table": table, "field": field, "records_checked": len(rows),
        "missing": len(missing), "has_value_but_still_flagged": len(flagged_but_present),
        "missing_but_not_flagged": len(empty_not_flagged),
    }
    return Attachment(f"Students missing {field}", summary, tables), model


def incomplete_profiles(client, table=None, field=None):
    """Records whose Missing Fields column lists anything, and what is missing."""
    table = client.find_table(table or config.STUDENT_TABLE)["name"]
    flags = _flags_field(client, table)
    if not flags:
        raise ValueError(
            f"'{table}' has no '{config.MISSING_FLAGS_FIELD}' field. Use the "
            "field completeness report instead, or set MISSING_FLAGS_FIELD in .env."
        )
    rows = client.records(table)
    ident = _identity_fields(client, table)
    tally = Counter()
    listed = []
    for row in rows:
        items = _flag_items(row["fields"].get(flags))
        if items:
            tally.update(set(items))
            listed.append(_cells(row, ident) + [", ".join(items), str(len(items))])
    listed.sort(key=lambda r: -int(r[-1]))

    summary = [
        f"Table: {table}",
        f"Records checked: {len(rows)}",
        f"Incomplete profiles: {len(listed)}",
        f"Complete profiles: {len(rows) - len(listed)}",
    ]
    tables = [
        Table("What is missing most often", ["Missing item", "Students"],
              [[item, str(n)] for item, n in tally.most_common()]),
        Table("Incomplete profiles", ident + ["Missing", "Count"], listed),
    ]
    model = {
        "table": table, "records_checked": len(rows), "incomplete": len(listed),
        "missing_item_counts": dict(tally.most_common()),
    }
    return Attachment("Incomplete student profiles", summary, tables), model


def field_completeness(client, table=None, field=None):
    """How full each field is, as a percentage."""
    table = client.find_table(table or config.STUDENT_TABLE)["name"]
    rows = client.records(table)
    total = len(rows)
    stats = []
    for f in client.fields(table):
        filled = sum(1 for r in rows if not is_empty(r["fields"].get(f["name"])))
        pct = round(100 * filled / total, 1) if total else 0.0
        stats.append((f["name"], filled, total - filled, pct))
    stats.sort(key=lambda s: s[3])
    summary = [f"Table: {table}", f"Records: {total}", f"Fields checked: {len(stats)}"]
    blocked = client.blocked_count(table)
    if blocked:
        summary.append(f"Blocked fields not checked: {blocked}")
    tables = [Table("Fields from least to most complete",
                    ["Field", "Filled", "Empty", "% filled"],
                    [[n, str(a), str(b), f"{p}%"] for n, a, b, p in stats])]
    model = {"table": table, "records": total,
             "percent_filled_by_field": {n: p for n, _, _, p in stats}}
    return Attachment(f"Field completeness: {table}", summary, tables), model


def duplicates(client, table=None, field=None):
    """Values that appear on more than one record (default: matric number)."""
    table = client.find_table(table or config.STUDENT_TABLE)["name"]
    names = {f["name"].lower(): f["name"] for f in client.fields(table)}
    if field:
        field = client.find_field(table, field)["name"]
    else:
        field = names.get(config.STUDENT_ID_FIELD.lower()) or client.primary_field(table)
    rows = client.records(table)
    ident = [f for f in _identity_fields(client, table) if f != field] or [field]
    groups = defaultdict(list)
    for row in rows:
        text = as_text(row["fields"].get(field)).strip()
        if text:
            groups[" ".join(text.lower().split())].append(row)
    dupes = [(rs, key) for key, rs in groups.items() if len(rs) > 1]
    dupes.sort(key=lambda d: -len(d[0]))
    table_rows = []
    for rs, _ in dupes:
        value = as_text(rs[0]["fields"].get(field))
        who = "; ".join(" / ".join(_cells(r, ident)) for r in rs)
        table_rows.append([value, str(len(rs)), who])
    involved = sum(len(rs) for rs, _ in dupes)
    summary = [f"Table: {table}", f"Field checked: {field}", f"Records checked: {len(rows)}",
               f"Duplicated values: {len(dupes)}", f"Records involved: {involved}"]
    tables = [Table(f"Duplicate {field} values", [field, "Times used", "Records"], table_rows)]
    model = {"table": table, "field": field, "records_checked": len(rows),
             "duplicated_values": len(dupes), "records_involved": involved}
    return Attachment(f"Duplicate {field}", summary, tables), model


def overview(client, table=None, field=None):
    """Every table with its record and field counts."""
    rows, model = [], {}
    for t in client.tables():
        count = len(client.records(t["name"]))
        visible = len(client.fields(t["name"]))
        blocked = client.blocked_count(t["name"])
        rows.append([t["name"], str(count), str(visible), str(blocked)])
        model[t["name"]] = {"records": count, "fields": visible, "blocked_fields": blocked}
    total = sum(int(r[1]) for r in rows)
    summary = [f"Tables: {len(rows)}", f"Total records: {total}"]
    tables = [Table("Tables", ["Table", "Records", "Fields", "Blocked fields"], rows)]
    return Attachment("Base overview", summary, tables), {"tables": model}


REPORTS = {
    "missing_field": (missing_field, "Records where a chosen field (e.g. Passport) is empty"),
    "incomplete_profiles": (incomplete_profiles, "Students with anything in Missing Fields, and what is missing"),
    "field_completeness": (field_completeness, "Percentage of records that have each field filled in"),
    "duplicates": (duplicates, "Values used on more than one record (default: matric number)"),
    "overview": (overview, "Every table with record and field counts"),
}

# Shortcuts for /report commands and web buttons.
SHORTCUTS = {
    "passport": ("missing_field", None, "Passport"),
    "missing": ("missing_field", None, None),
    "incomplete": ("incomplete_profiles", None, None),
    "completeness": ("field_completeness", None, None),
    "duplicates": ("duplicates", None, None),
    "overview": ("overview", None, None),
}


def run(client, name, table=None, field=None):
    if name not in REPORTS:
        raise ValueError(f"Unknown report '{name}'. Reports: {', '.join(REPORTS)}")
    return REPORTS[name][0](client, table=table, field=field)

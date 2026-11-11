"""
Filtering, sorting and counting records in Python, on this computer.
"""

from collections import Counter

OPERATORS = [
    "equals", "not_equals", "contains", "not_contains",
    "is_empty", "is_not_empty",
    "greater_than", "less_than", "at_least", "at_most",
    "one_of",
]


class QueryError(Exception):
    pass


def as_text(value):
    return ", ".join(value) if isinstance(value, list) else (value or "")


def is_empty(value):
    return value in ("", None) or value == []


def _number(text):
    try:
        return float(str(text).replace(",", "").strip())
    except ValueError:
        return None


def _compare(value, target):
    """Compare as numbers when both look numeric, otherwise as text (works for ISO dates)."""
    a, b = _number(value), _number(target)
    if a is not None and b is not None:
        return (a > b) - (a < b)
    a, b = str(value).strip().lower(), str(target).strip().lower()
    return (a > b) - (a < b)


def matches(value, op, target):
    items = value if isinstance(value, list) else ([value] if not is_empty(value) else [])
    target_text = (target or "").strip().lower()

    if op == "is_empty":
        return is_empty(value)
    if op == "is_not_empty":
        return not is_empty(value)
    if op == "equals":
        return any(str(i).strip().lower() == target_text for i in items)
    if op == "not_equals":
        return not any(str(i).strip().lower() == target_text for i in items)
    if op == "contains":
        return target_text in as_text(value).lower()
    if op == "not_contains":
        return target_text not in as_text(value).lower()
    if op == "one_of":
        options = {o.strip().lower() for o in (target or "").split("|") if o.strip()}
        return any(str(i).strip().lower() in options for i in items)
    if op in ("greater_than", "less_than", "at_least", "at_most"):
        if not items:
            return False
        result = _compare(items[0], target)
        return {
            "greater_than": result > 0,
            "less_than": result < 0,
            "at_least": result >= 0,
            "at_most": result <= 0,
        }[op]
    raise QueryError(f"Unknown operator '{op}'. Use one of: {', '.join(OPERATORS)}")


def resolve_field(field_names, wanted):
    lookup = {name.lower(): name for name in field_names}
    name = lookup.get(str(wanted or "").strip().lower())
    if not name:
        raise QueryError(
            f"Field '{wanted}' does not exist (or is blocked). "
            f"Available fields: {', '.join(field_names)}"
        )
    return name


def apply_filters(rows, filters, field_names):
    resolved = []
    for f in filters or []:
        op = f.get("op")
        if op not in OPERATORS:
            raise QueryError(f"Unknown operator '{op}'. Use one of: {', '.join(OPERATORS)}")
        if op not in ("is_empty", "is_not_empty") and f.get("value") in (None, ""):
            raise QueryError(f"Operator '{op}' on '{f.get('field')}' needs a value.")
        resolved.append((resolve_field(field_names, f.get("field")), op, f.get("value")))
    return [
        row for row in rows
        if all(matches(row["fields"].get(name), op, value) for name, op, value in resolved)
    ]


def sort_rows(rows, field, descending=False):
    def key(row):
        text = as_text(row["fields"].get(field))
        number = _number(text)
        # Empty values always last; numbers sort numerically.
        return (text == "", 0 if number is not None else 1, number or 0, text.lower())
    ordered = sorted(rows, key=key)
    if descending:
        filled = [r for r in ordered if as_text(r["fields"].get(field)) != ""]
        empty = [r for r in ordered if as_text(r["fields"].get(field)) == ""]
        ordered = list(reversed(filled)) + empty
    return ordered


def group_counts(rows, field):
    counter = Counter()
    for row in rows:
        value = row["fields"].get(field)
        if is_empty(value):
            counter["(empty)"] += 1
        elif isinstance(value, list):
            for item in set(value):
                counter[item] += 1
        else:
            counter[value] += 1
    return dict(counter.most_common())

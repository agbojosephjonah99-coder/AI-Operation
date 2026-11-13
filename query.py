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


# ---------------------------------------------------------------------------
# "Similar" matching for names, matric numbers and other short text
# ---------------------------------------------------------------------------

import re as _re
from difflib import SequenceMatcher
from functools import lru_cache

SIMILAR_THRESHOLD = 0.78     # 0..1; lower finds more (and looser) matches
_SIMILAR_MAX_LENGTH = 120    # long text (notes, addresses) is not fuzzy-matched


def normalize_text(text):
    """Lower-case, punctuation to spaces, single spaces: 'Okafor,  John' -> 'okafor john'."""
    return " ".join(_re.sub(r"[^\w]+", " ", str(text).lower()).split())


def _ratio(a, b):
    return SequenceMatcher(None, a, b).ratio()


def _ratio_if(matcher, value, minimum):
    """matcher.ratio() against value, or 0.0 if it cannot reach `minimum` (cheap checks first)."""
    matcher.set_seq1(value)
    if matcher.real_quick_ratio() < minimum or matcher.quick_ratio() < minimum:
        return 0.0
    score = matcher.ratio()
    return score if score >= minimum else 0.0


def _one_slip(a, b):
    """True if a and b differ by one changed, missing, extra or swapped character."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return (len(diff) == 2 and diff[1] == diff[0] + 1
                and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


class _Term:
    """A search term prepared once, then compared against many values quickly."""

    def __init__(self, term):
        self.text = normalize_text(term)
        self.words = self.text.split()
        self.digits = "".join(ch for ch in self.text if ch.isdigit())
        self.whole = SequenceMatcher(None, "", self.text, autojunk=False)
        self.sorted = SequenceMatcher(None, "", " ".join(sorted(self.words)), autojunk=False)
        self.word_matchers = [SequenceMatcher(None, "", w, autojunk=False) for w in self.words]
        n = max(1, len(self.words))
        # A single word scoring below this cannot lift the average over the threshold.
        self.word_floor = max(0.0, n * SIMILAR_THRESHOLD - (n - 1) - 0.05)

    def score(self, text):
        a, b = self.text, normalize_text(text)
        if not a or not b or len(b) > _SIMILAR_MAX_LENGTH:
            return 0.0
        if a == b:
            return 1.0
        # IDs such as matric numbers share most characters ("SPPG/0123" vs
        # "SPPG/0456"), so compare their digits strictly instead.
        if len(self.digits) >= 3:
            b_digits = "".join(ch for ch in b if ch.isdigit())
            if not b_digits:
                return 0.0
            if self.digits == b_digits:
                return 0.95                     # same number, different formatting
            if _one_slip(self.digits, b_digits):
                # Swapped neighbours are the most common typing slip.
                swapped = len(self.digits) == len(b_digits) and sorted(self.digits) == sorted(b_digits)
                return 0.88 if swapped else 0.85
            return 0.0
        b_words = b.split()
        # Single short words (e.g. "Ade") only count on an exact word match.
        if len(self.words) == 1 and len(a) < 4:
            return 0.95 if a in b_words else 0.0
        t = SIMILAR_THRESHOLD
        whole = _ratio_if(self.whole, b, t)
        reordered = _ratio_if(self.sorted, " ".join(sorted(b_words)), t)
        # Every word of the search should closely match some word of the value.
        total = 0.0
        for matcher in self.word_matchers:
            best = 0.0
            for word in b_words:
                best = max(best, _ratio_if(matcher, word, max(self.word_floor, best)))
                if best == 1.0:
                    break
            total += best
        per_word = total / len(self.words)
        # Small penalty when the value has many more words than the search.
        per_word *= 1 - min(0.15, 0.05 * max(0, len(b_words) - len(self.words)))
        return round(max(whole, reordered, per_word), 3)


@lru_cache(maxsize=64)
def _prepared(term):
    return _Term(term)


def similarity(term, text):
    """
    How alike a search term and a value are, from 0 to 1. Handles:
      typos              'Chukwuma' ~ 'Chukwumah'
      word order         'John Okafor' ~ 'Okafor John'
      extra/missing name 'John Okafor' ~ 'John Chidi Okafor'
      ID slips           'SPPG/0123' ~ 'SPPG/0132'
    Scores below SIMILAR_THRESHOLD may be returned as 0 (they are not similar).
    """
    return _prepared(str(term)).score(text)

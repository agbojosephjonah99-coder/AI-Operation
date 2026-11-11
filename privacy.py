"""
Privacy policy: decides what happens to each Airtable field.

Every field has one of three levels:

  "model"   The values may be sent to OpenAI (only ever as totals per value,
            e.g. "Level 300: 42 students"). Use for harmless categories.
  "user"    The values are shown to you on screen and in CSV exports, but
            are NEVER sent to OpenAI. This is the default for every field.
  "blocked" The values are never downloaded from Airtable at all.

Fields that are not listed in privacy.json are "user", except fields whose
names look like credentials, bank details or medical information, which are
"blocked". Run `python setup_privacy.py` to create privacy.json listing
every field in your base, then edit it.
"""

import json
import re
from pathlib import Path

MODEL = "model"
USER = "user"
BLOCKED = "blocked"
LEVELS = (MODEL, USER, BLOCKED)

# Fields whose names match these are blocked unless privacy.json says otherwise.
DEFAULT_BLOCK_PATTERNS = [
    r"password", r"passcode", r"\bpin\b", r"token", r"secret", r"api[ _-]?key",
    r"\bbank\b", r"account (number|no|name)", r"\bbvn\b", r"card number",
    r"\bcvv\b", r"medical", r"diagnosis", r"health condition", r"disabilit",
]

# Used by setup_privacy.py so personal fields are never suggested as "model".
PERSONAL_PATTERNS = [
    r"name", r"gender", r"\bsex\b", r"relig", r"origin", r"\bstate\b", r"\blga\b",
    r"tribe", r"ethnic", r"address", r"phone", r"mobile", r"e-?mail", r"birth",
    r"\bdob\b", r"\bage\b", r"marital", r"kin", r"guardian", r"parent", r"sponsor",
    r"passport", r"photo", r"\bnin\b", r"matric", r"\bid\b", r"number", r"signature",
]

# Field types that hold categories and are suggested as "model" when the
# field name does not look personal.
CATEGORY_TYPES = {"singleSelect", "multipleSelects", "checkbox", "rating"}


def _key(name):
    """Compare names ignoring capitals and extra spaces."""
    return " ".join(str(name).split()).lower()


def _matches(name, patterns):
    lowered = name.lower()
    return any(re.search(pattern, lowered) for pattern in patterns)


def looks_personal(field_name):
    """True for fields like Name, Phone, Matric Number whose values identify people."""
    return _matches(field_name, PERSONAL_PATTERNS)


# A value is shared with the AI only if at least this many records have it,
# so a shared value can never point to one particular student.
MIN_SHARED_COUNT = 3


class PrivacyPolicy:

    def __init__(self, path=None, data=None):
        self.path = Path(path) if path else None
        if data is None and self.path and self.path.exists():
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
        data = data or {}
        self.tables = {
            _key(table): {_key(field): level for field, level in fields.items()}
            for table, fields in (data.get("tables") or {}).items()
        }
        self.block_patterns = data.get("block_patterns") or DEFAULT_BLOCK_PATTERNS
        for table, fields in self.tables.items():
            for field, level in fields.items():
                if level not in LEVELS:
                    raise ValueError(
                        f"privacy.json: '{table}' / '{field}' has level "
                        f"'{level}'. Use one of: model, user, blocked."
                    )

    def level(self, table_name, field_name):
        explicit = self.tables.get(_key(table_name), {}).get(_key(field_name))
        if explicit:
            return explicit
        if _matches(field_name, self.block_patterns):
            return BLOCKED
        return USER

    def suggest(self, field_name, field_type):
        """Suggested level for a field nobody has reviewed yet."""
        if _matches(field_name, self.block_patterns):
            return BLOCKED
        if field_type in CATEGORY_TYPES and not _matches(field_name, PERSONAL_PATTERNS):
            return MODEL
        return USER

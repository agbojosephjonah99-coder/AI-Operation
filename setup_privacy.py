"""
Create or update privacy.json with every table and field in your base.

    python setup_privacy.py

Each field gets a level:
  "model"   totals per value may be sent to the AI (e.g. Level, Department)
  "user"    shown to you only, never sent to the AI   (the default)
  "blocked" never downloaded at all

Fields already in privacy.json keep their level; new fields get a
suggestion. Open privacy.json afterwards and check it.
"""

import json

import config
from airtable_client import AirtableClient
from privacy import BLOCKED, MODEL, PrivacyPolicy


def main():
    existing = {}
    if config.PRIVACY_FILE.exists():
        with open(config.PRIVACY_FILE, encoding="utf-8") as handle:
            existing = json.load(handle)
    policy = PrivacyPolicy(data=existing)
    client = AirtableClient(config.AIRTABLE_TOKEN, config.AIRTABLE_BASE_ID, policy)

    old_tables = {t.lower(): f for t, f in (existing.get("tables") or {}).items()}
    tables, added, counts = {}, 0, {"model": 0, "user": 0, "blocked": 0}
    for table in client.tables():
        old = {k.lower(): v for k, v in old_tables.get(table["name"].lower(), {}).items()}
        fields = {}
        for field in table["fields"]:
            level = old.get(field["name"].lower())
            if not level:
                level = policy.suggest(field["name"], field["type"])
                added += 1
            fields[field["name"]] = level
            counts[level] += 1
        tables[table["name"]] = fields

    data = {
        "_help": [
            "model   = totals per value may be sent to the AI (harmless categories only)",
            "user    = shown to you only, never sent to the AI (default)",
            "blocked = never downloaded from Airtable",
        ],
        "tables": tables,
    }
    if existing.get("block_patterns"):
        data["block_patterns"] = existing["block_patterns"]
    with open(config.PRIVACY_FILE, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)

    print(f"Wrote {config.PRIVACY_FILE}")
    print(f"{len(tables)} tables, {sum(counts.values())} fields ({added} new).")
    print(f"model: {counts[MODEL]}   user: {counts['user']}   blocked: {counts[BLOCKED]}")
    print("Please open privacy.json and check the 'model' and 'blocked' fields.")


if __name__ == "__main__":
    main()

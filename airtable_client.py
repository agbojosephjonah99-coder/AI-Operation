"""
Read-only Airtable connection with caching, rate limiting and retries.

- Table and field lists are downloaded once and reused (SCHEMA_CACHE_SECONDS).
- Records are downloaded once per table and reused (CACHE_SECONDS), so
  repeated questions and cross-table searches do not hit Airtable again.
- Requests are spaced out to stay under Airtable's limit of 5 per second.
- "Too many requests" (429) and temporary server errors are retried.
- Blocked fields (see privacy.py) are never requested from Airtable.
- Records are requested by field ID, so duplicate or near-duplicate field
  names (e.g. "X copy" / "X Copy") cannot break a download.
"""

import threading
import time

import requests

from privacy import BLOCKED

API_ROOT = "https://api.airtable.com/v0"


class AirtableError(Exception):
    pass


class RateLimiter:
    """Keeps requests at least `interval` seconds apart across all threads."""

    def __init__(self, interval=0.22, clock=time.monotonic, sleep=time.sleep):
        self.interval = interval
        self.clock = clock
        self.sleep = sleep
        self.lock = threading.Lock()
        self.next_time = 0.0

    def wait(self):
        with self.lock:
            now = self.clock()
            if now < self.next_time:
                self.sleep(self.next_time - now)
                now = self.next_time
            self.next_time = now + self.interval


# ---------------------------------------------------------------------------
# Turning Airtable cell values into plain text
# ---------------------------------------------------------------------------

def _format_number(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _text(value, links=None):
    """Convert one Airtable value (or list element) to a string."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else ""
    if isinstance(value, (int, float)):
        return _format_number(value)
    if isinstance(value, str):
        if links is not None and value.startswith("rec"):
            return links.get(value, value)
        return value
    if isinstance(value, dict):
        if "filename" in value:                      # attachment
            return value.get("filename") or "file"
        for key in ("name", "email", "label", "text", "value"):
            if value.get(key):                       # collaborator, button, barcode, AI text
                return str(value[key])
        if "error" in value:
            return "#ERROR"
        return ""
    if isinstance(value, list):
        return ", ".join(t for t in (_text(v, links) for v in value) if t)
    return str(value)


def normalize(value, field_type, links=None):
    """
    Return a cell as a string, or a list of strings for multi-value fields.
    An empty cell becomes "" or [].
    """
    if field_type == "multipleAttachments":
        files = [_text(item) for item in (value or [])]
        return files
    if isinstance(value, list):
        return [t for t in (_text(item, links) for item in value) if t != ""]
    return _text(value, links)


class AirtableClient:

    def __init__(self, token, base_id, policy, cache_seconds=300,
                 schema_cache_seconds=900, http=None, sleep=time.sleep,
                 clock=time.monotonic, max_attempts=5):
        if not token or not base_id:
            raise AirtableError(
                "The Airtable token or base ID is missing. Add "
                "Airtable_Personal_Access_Token and Airtable_Base_ID to the .env file."
            )
        self.base_id = base_id
        self.policy = policy
        self.cache_seconds = cache_seconds
        self.schema_cache_seconds = schema_cache_seconds
        self.sleep = sleep
        self.clock = clock
        self.max_attempts = max_attempts
        self.http = http or requests.Session()
        self.http.headers.update({"Authorization": f"Bearer {token}"})
        self.limiter = RateLimiter(sleep=sleep, clock=clock)

        self._cache = {}
        self._cache_lock = threading.Lock()
        self._fetch_locks = {}
        self.request_count = 0

    # -- HTTP -----------------------------------------------------------------

    def _send(self, method, url, body=None, params=None):
        last_problem = "unknown error"
        for attempt in range(self.max_attempts):
            self.limiter.wait()
            self.request_count += 1
            try:
                response = self.http.request(method, url, json=body, params=params, timeout=60)
            except requests.RequestException as error:
                last_problem = f"network error: {error}"
                self.sleep(min(2 ** attempt, 30))
                continue

            status = response.status_code
            if status == 200:
                return response.json()
            if status == 429:
                # Airtable asks clients to wait 30 seconds after a 429.
                wait = response.headers.get("Retry-After")
                last_problem = "Airtable rate limit (429)"
                self.sleep(float(wait) if wait and wait.isdigit() else 30)
                continue
            if status >= 500:
                last_problem = f"Airtable server error ({status})"
                self.sleep(min(2 ** attempt, 30))
                continue

            message = response.text[:500]
            hint = ""
            if status in (401, 403):
                hint = (" Check the token in .env and that it has the "
                        "data.records:read and schema.bases:read scopes and "
                        "access to this base.")
            elif status == 404:
                hint = " Check Airtable_Base_ID in .env."
            raise AirtableError(f"Airtable returned {status}: {message}{hint}")

        raise AirtableError(f"Airtable request failed after retries ({last_problem}).")

    # -- Cache ----------------------------------------------------------------

    def _cached(self, key, ttl, loader):
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry and self.clock() - entry[0] < ttl:
                return entry[1]
            lock = self._fetch_locks.setdefault(key, threading.Lock())
        # Only one thread downloads a given table; others wait and reuse it.
        with lock:
            with self._cache_lock:
                entry = self._cache.get(key)
                if entry and self.clock() - entry[0] < ttl:
                    return entry[1]
            value = loader()
            with self._cache_lock:
                self._cache[key] = (self.clock(), value)
            return value

    def refresh(self):
        """Forget everything downloaded so the next question gets fresh data."""
        with self._cache_lock:
            self._cache.clear()

    # -- Schema ---------------------------------------------------------------

    def tables(self):
        def load():
            data = self._send("GET", f"{API_ROOT}/meta/bases/{self.base_id}/tables")
            return data.get("tables", [])
        return self._cached("schema", self.schema_cache_seconds, load)

    def find_table(self, name_or_id):
        if not name_or_id or not str(name_or_id).strip():
            raise AirtableError("A table name is required.")
        wanted = str(name_or_id).strip().lower()
        tables = self.tables()
        for table in tables:
            if table["id"].lower() == wanted or table["name"].lower() == wanted:
                return table
        names = ", ".join(t["name"] for t in tables)
        raise AirtableError(f"Table '{name_or_id}' was not found. Tables: {names}")

    def fields(self, table_name):
        """
        Fields that are not blocked, each with its privacy level added.

        Airtable can contain fields whose names differ only by capitals or
        spaces (e.g. a duplicated "X copy" next to "X Copy"). Those would be
        ambiguous, so the second one is renamed here, e.g. "X Copy (2)".
        Data is always fetched by field ID, never by name.
        """
        table = self.find_table(table_name)
        result, seen = [], set()
        for field in table.get("fields", []):
            level = self.policy.level(table["name"], field["name"])
            if level == BLOCKED:
                continue
            name = field["name"].strip() or field["id"]
            key = " ".join(name.split()).lower()
            number = 1
            while key in seen:
                number += 1
                name = f"{field['name'].strip()} ({number})"
                key = " ".join(name.split()).lower()
            seen.add(key)
            result.append({**field, "name": name, "airtable_name": field["name"], "level": level})
        return result

    def blocked_count(self, table_name):
        table = self.find_table(table_name)
        return sum(
            1 for f in table.get("fields", [])
            if self.policy.level(table["name"], f["name"]) == BLOCKED
        )

    def primary_field(self, table_name):
        """Name of the table's primary field, or None if it is blocked."""
        table = self.find_table(table_name)
        for field in self.fields(table_name):
            if field["id"] == table.get("primaryFieldId"):
                return field["name"]
        return None

    def find_field(self, table_name, field_name):
        if not field_name:
            raise AirtableError("A field name is required.")
        wanted = str(field_name).strip().lower()
        fields = self.fields(table_name)
        for field in fields:
            if field["name"].lower() == wanted:
                return field
        names = ", ".join(f["name"] for f in fields)
        raise AirtableError(
            f"Field '{field_name}' was not found in '{table_name}' (or it is "
            f"blocked). Fields: {names}"
        )

    # -- Records --------------------------------------------------------------

    def _download(self, table_id, field_ids=None):
        """
        Download every record, keyed by field ID. field_ids=None means all
        fields; otherwise only those fields are requested (blocked fields
        are left out this way).
        """
        url = f"{API_ROOT}/{self.base_id}/{table_id}"
        records, offset = [], None
        while True:
            params = [("pageSize", "100"), ("returnFieldsByFieldId", "true")]
            params += [("fields[]", fid) for fid in field_ids or []]
            if offset:
                params.append(("offset", offset))
            data = self._send("GET", url, params=params)
            records.extend(data.get("records", []))
            offset = data.get("offset")
            if not offset:
                return records

    def _link_names(self, table_id):
        """Map record IDs in a linked table to their primary-field text."""
        def load():
            table = self.find_table(table_id)
            primary = next(
                (f for f in self.fields(table["name"]) if f["id"] == table.get("primaryFieldId")),
                None,
            )
            if not primary:
                return {}
            rows = self._download(table["id"], [primary["id"]])
            names = {}
            for row in rows:
                value = normalize(row.get("fields", {}).get(primary["id"]), primary["type"])
                names[row["id"]] = ", ".join(value) if isinstance(value, list) else value
            return names
        return self._cached(f"links:{table_id}", self.cache_seconds, load)

    def records(self, table_name):
        """
        All records of a table as a list of
        {"id": "rec...", "fields": {field name: str or list[str]}}.
        Every non-blocked field is present (empty cells are "" or []).
        """
        table = self.find_table(table_name)

        def load():
            fields = self.fields(table["name"])
            # Ask only for allowed fields when some are blocked; otherwise all.
            wanted = [f["id"] for f in fields] if self.blocked_count(table["name"]) else None
            raw = self._download(table["id"], wanted)
            links = {}
            for field in fields:
                if field["type"] == "multipleRecordLinks":
                    linked_id = (field.get("options") or {}).get("linkedTableId")
                    if linked_id:
                        links[field["name"]] = self._link_names(linked_id)
            rows = []
            for record in raw:
                cells = record.get("fields", {})
                rows.append({
                    "id": record["id"],
                    "fields": {
                        f["name"]: normalize(
                            cells.get(f["id"]), f["type"], links.get(f["name"])
                        )
                        for f in fields
                    },
                })
            return rows

        return self._cached(f"records:{table['id']}", self.cache_seconds, load)

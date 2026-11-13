# AI Operations Assistant

Ask plain-English questions about the SPPG Airtable base, in your browser
or in a terminal. Access is read-only.

## Setup (Windows)

1. Install Python 3.10 or newer from python.org. During install, tick "Add
   Python to PATH".
2. Copy your existing `.env` into this folder. The variable names are the
   same as before. If you don't have one, `setup.bat` creates one from
   `.env.example` for you to fill in.
3. Double-click **`setup.bat`**. It builds a fresh `.venv` for this computer.
   Run it again any time the folder is moved or copied to another PC.
4. Double-click **`run_web.bat`**. Your browser opens at http://127.0.0.1:5000.
   For the terminal version, double-click **`run_cli.bat`** instead.

On macOS or Linux, run `./setup.sh`, then `.venv/bin/python web.py`.

Don't copy `.venv` between computers, because it only works on the machine
that built it. Copy everything else, then run setup again.

## Privacy: what goes to OpenAI

OpenAI chooses which lookups to run. The lookups themselves run on your
computer, and the records they find go straight to your screen.

| Data | Sent to OpenAI? |
|---|---|
| Your questions and the conversation so far | Yes (with `store=False`) |
| Table names, field names and field types | Yes |
| Counts ("50 records found") | Yes |
| Totals per value for fields set to `"model"` in privacy.json (e.g. Level 300: 63) | Yes |
| Dropdown option labels, and category values that at least 3 records share in non-personal fields (e.g. School Fees Status: Paid 166, Not Paid 83) | Yes, so it can filter on the right spelling |
| Smallest and largest value of number/currency fields | Yes |
| Names, matric numbers, phone numbers, passports, other personal fields, and any value fewer than 3 records share | **No.** These are shown to you only |
| Fields set to `"blocked"` | Never downloaded from Airtable at all |

Anything you type into a question is sent, though. For example, "find
John Okafor" sends that name.

### Reviewing field privacy (recommended once)

```
.venv\Scripts\python setup_privacy.py
```

This writes `privacy.json`, which lists every field in your base with one
of three levels:

- `"user"`: shown to you, never sent to the AI. This is the default for
  everything.
- `"model"`: totals per value can go to the AI, so it can answer things
  like "how many in each level". Use this only for harmless categories.
- `"blocked"`: never downloaded. By default this covers fields that look
  like passwords, tokens, bank details or medical information.

Open the file and check it. To hide a field completely, such as a
leftover duplicated "... copy" field, set it to `"blocked"`. Re-run the script whenever fields are added,
and it keeps your earlier choices.

Fields whose names differ only by capitals or spaces (e.g. a duplicated
"X copy" next to "X Copy") are handled automatically. The app reads data
by field ID, and the second one is shown as "X Copy (2)".

## Using it

Example questions:

- Which students have not submitted their passport?
- How many students *have* submitted their passport?
- Now only the 300-level ones (follow-ups remember the conversation)
- How many students are in each level?
- Search for SPPG/0123

You never need to say which table to look in. If the obvious field is
empty, the assistant searches every table in the base for related fields
(e.g. a separate fees or payments table). It cross-checks them against the
student table and says which table and field it used.

If a name or matric number has no exact match, the assistant says so and
shows **similar records** instead, with a similarity score. Similar means
misspellings, names in a different order, a missing middle name, or a
matric number with one wrong or swapped digit. Check these before relying
on them. To make matching stricter or looser, change `SIMILAR_THRESHOLD`
in `query.py`.

Every list appears in full under the answer, with a filter box and a
**Download CSV** button.

### Reports

Reports run on your computer without the AI, so they cost nothing and are
instant once the data is cached. Run them from the web sidebar, or in the
terminal:

| Command | Report |
|---|---|
| `/report passport` | Students missing a passport, plus records still wrongly flagged in Missing Fields |
| `/report missing "Birth Certificate"` | The same check for any field |
| `/report incomplete` | Students with anything listed in Missing Fields, and the most common gaps |
| `/report completeness [table]` | The percentage of records that have each field filled in |
| `/report duplicates [field]` | Duplicate matric numbers, or duplicates in another field |
| `/report overview` | Every table with its record and field counts |

Other terminal commands:

- `/export [R#]`: save a result as a CSV file in the `exports` folder
- `/new`: start a new conversation
- `/refresh`: download fresh data from Airtable
- `/help`: list the commands

To add a report, write a function in `reports.py` with the same shape as
the others and list it in `REPORTS`.

## Speed

- Tables and records are downloaded once and reused for 5 minutes
  (`CACHE_SECONDS`). Click **Refresh from Airtable** or type `/refresh` to
  get the latest data straight away.
- Requests are spaced out to stay under Airtable's limit of 5 per second.
  If Airtable answers "too many requests", the app waits and retries
  automatically, as it does for temporary server errors.
- The first question about a large table takes a few seconds while the
  data downloads. Later questions about the same table are instant.

## Deploying online (Vercel)

See **DEPLOY_VERCEL.md** for step-by-step instructions.

## Sharing the web app on your network

By default only this computer can open it. To let colleagues on the same
network use it, add these lines to `.env`:

```
WEB_HOST=0.0.0.0
APP_PASSWORD=choose-a-strong-password
SECRET_KEY=any-long-random-text
```

Colleagues then open `http://<this-computer's-IP>:5000`.

## Settings (.env)

See `.env.example` for every option. Two you may want to change:

- `STUDENT_TABLE`, `STUDENT_NAME_FIELD`, `STUDENT_ID_FIELD` and
  `MISSING_FLAGS_FIELD`: change these if your field names differ from
  the defaults.
- `MAX_HISTORY_TURNS`: how many earlier questions the assistant remembers.
  The default is 20.

## Files

| File | Purpose |
|---|---|
| `web.py`, `templates/` | The web interface |
| `app.py`, `vercel.json`, `.vercelignore` | Vercel deployment (see DEPLOY_VERCEL.md) |
| `agent.py` | The terminal interface |
| `assistant.py` | The AI conversation, its tools and its memory |
| `airtable_client.py` | The Airtable connection: caching, rate limiting and retries |
| `privacy.py`, `setup_privacy.py` | The field privacy rules |
| `query.py` | Filtering, sorting and counting |
| `reports.py` | The built-in reports |
| `tests/` | Automated tests that use a fake Airtable and OpenAI |

To run the tests:

```
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
```

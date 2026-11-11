"""
AI Operations Assistant - command-line version.

    python agent.py

Type questions in plain English, or use the commands listed by /help.
For the web version run:  python web.py
"""

import shlex
import sys
from pathlib import Path

import config
import reports
from assistant import Session, build_tools

HELP = """
Ask anything in plain English, e.g.
  How many students are in 300 level?
  List students who have not submitted their passport
  Now only the ones in Computer Science

Commands (these run on your computer and do not use the AI):
  /report passport               students missing a passport
  /report missing <field>        students missing any field, e.g. /report missing "Birth Certificate"
  /report incomplete             incomplete profiles and what is missing
  /report completeness [table]   how full each field is
  /report duplicates [field]     duplicate matric numbers (or another field)
  /report overview               every table with record counts
  /export [R#]                   save a result as a CSV file (default: the latest)
  /new                           start a new conversation (forget earlier questions)
  /refresh                       download fresh data from Airtable
  /help                          show this help
  exit                           quit
"""


def show(reply):
    if reply.text:
        print("\nAssistant:\n")
        print(reply.text)
    for attachment in reply.attachments:
        print()
        print(f"[{attachment.id}] " + attachment.to_text())


def report_command(session, parts):
    if not parts:
        print("Which report? Try /help")
        return
    name, rest = parts[0].lower(), parts[1:]
    if name not in reports.SHORTCUTS:
        print(f"Unknown report '{name}'. Reports: {', '.join(reports.SHORTCUTS)}")
        return
    report, table, field = reports.SHORTCUTS[name]
    if report == "missing_field" and field is None:
        if not rest:
            print('Say which field, e.g. /report missing "Birth Certificate"')
            return
        field = " ".join(rest)
    elif report == "duplicates" and rest:
        field = " ".join(rest)
    elif report == "field_completeness" and rest:
        table = " ".join(rest)
    show(session.run_report(report, table, field))


def export_command(session, parts):
    if not session.attachments:
        print("Nothing to export yet.")
        return
    key = parts[0].upper() if parts else next(reversed(session.attachments))
    attachment = session.attachments.get(key)
    if not attachment:
        print(f"No result called {key}. Available: {', '.join(session.attachments)}")
        return
    folder = config.BASE_DIR / "exports"
    folder.mkdir(exist_ok=True)
    for index, table in enumerate(attachment.tables):
        safe = "".join(c if c.isalnum() else "_" for c in f"{attachment.title}_{table.heading}")[:80]
        path = folder / f"{attachment.id}_{safe}.csv"
        path.write_text(attachment.to_csv(index), encoding="utf-8-sig")
        print(f"Saved {path}")


def main():
    print("AI Operations Assistant  (type /help for commands, 'exit' to quit)")
    try:
        tools = build_tools()
    except Exception as error:
        print(f"Could not start: {error}")
        sys.exit(1)
    session = Session(tools)
    print(f"Connected to Airtable base. Privacy settings: "
          f"{'privacy.json' if Path(config.PRIVACY_FILE).exists() else 'defaults (run setup_privacy.py to review)'}")

    while True:
        try:
            question = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break
        if not question:
            continue
        if question.lower() in ("exit", "quit"):
            print("Goodbye!")
            break

        try:
            if question.startswith("/"):
                try:
                    parts = shlex.split(question[1:])
                except ValueError:
                    parts = question[1:].split()
                command = parts[0].lower() if parts else ""
                if command == "help":
                    print(HELP)
                elif command == "new":
                    session.reset()
                    print("Started a new conversation.")
                elif command == "refresh":
                    tools.client.refresh()
                    print("Cached data cleared. The next question downloads fresh data.")
                elif command == "report":
                    report_command(session, parts[1:])
                elif command == "export":
                    export_command(session, parts[1:])
                else:
                    print("Unknown command. Type /help")
                continue

            show(session.ask(question))
        except Exception as error:
            print("\nRequest failed:", error)


if __name__ == "__main__":
    main()

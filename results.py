"""
Result objects shown to the user. These hold real record values and are
never sent to OpenAI; the AI only receives the separate `model_view`.
"""

import csv
import io
from dataclasses import dataclass, field


@dataclass
class Table:
    heading: str
    columns: list
    rows: list            # list of lists of strings
    note: str = ""


@dataclass
class Attachment:
    title: str
    summary: list = field(default_factory=list)   # short lines, counts only
    tables: list = field(default_factory=list)
    id: str = ""

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "tables": [
                {"heading": t.heading, "columns": t.columns, "rows": t.rows, "note": t.note}
                for t in self.tables
            ],
        }

    def to_csv(self, table_index=0):
        table = self.tables[table_index]
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(table.columns)
        writer.writerows(table.rows)
        return buffer.getvalue()

    def to_text(self, max_width=40):
        lines = [self.title, "=" * max(len(self.title), 20)]
        lines += self.summary
        for table in self.tables:
            lines += ["", f"{table.heading} ({len(table.rows)})", "-" * 20]
            if table.note:
                lines.append(table.note)
            if not table.rows:
                lines.append("(none)")
                continue
            columns = ["#"] + table.columns
            rows = [[str(i)] + [str(c) for c in r] for i, r in enumerate(table.rows, 1)]
            widths = [
                min(max_width, max(len(columns[i]), *(len(r[i]) for r in rows)))
                for i in range(len(columns))
            ]

            def fmt(cells):
                out = []
                for cell, width in zip(cells, widths):
                    cell = cell if len(cell) <= width else cell[: width - 1] + "…"
                    out.append(cell.ljust(width))
                return "  ".join(out).rstrip()

            lines.append(fmt(columns))
            lines.append(fmt(["-" * w for w in widths]))
            lines += [fmt(r) for r in rows]
        return "\n".join(lines)

"""Command line: python cli.py examples/college.sql "SELECT ... " [--rows 200000] [--json]"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqldoctor import analyze


def main() -> None:
    p = argparse.ArgumentParser(description="Find missing indexes for a SQL query and measure the speedup.")
    p.add_argument("schema", help="path to a .sql file with CREATE TABLE statements")
    p.add_argument("query", help="the SELECT query to analyze (or a path to a .sql file)")
    p.add_argument("--rows", type=int, default=100_000, help="rows to generate for large tables")
    p.add_argument("--json", action="store_true", help="print the full report as JSON")
    a = p.parse_args()

    query = Path(a.query).read_text() if a.query.endswith(".sql") and Path(a.query).exists() else a.query
    r = analyze(Path(a.schema).read_text(), query.rstrip("; \n"), rows=a.rows)
    if a.json:
        print(json.dumps(r.to_dict(), indent=2))
        return

    print(f"Test data: {', '.join(f'{t}={n:,}' for t, n in r.rows_per_table.items())}")
    print(f"Full scans before: {', '.join(r.full_scans) or 'none'}")
    print(f"Before: {r.before_ms:.3f} ms   After: {r.after_ms:.3f} ms   Speedup: {r.speedup}x\n")
    for s in r.suggestions:
        print(f"  {s['sql']}\n      why: {s['reason']}")
    for n in r.notes:
        print(f"  note: {n}")
    print("\nPlan after:")
    for line in r.plan_after:
        print("  " + line)


if __name__ == "__main__":
    main()

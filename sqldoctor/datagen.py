"""Fill an empty schema with realistic synthetic rows so query timings mean something.

Rules are driven by column names and declared types, and by foreign keys:
- INTEGER PRIMARY KEY            -> 1..N
- columns with a FOREIGN KEY     -> ids that exist in the parent table
- *_id without a FOREIGN KEY     -> ids in a plausible range
- status / type / category / ... -> a few repeated values (low cardinality)
- *date* / *_at                  -> ISO dates over the last 3 years
- REAL / amount / price / fee    -> money-like numbers
- email / name / city            -> generated strings
"""
from __future__ import annotations

import datetime as dt
import random
import sqlite3

_LOW_CARD = {
    "status": ["active", "inactive", "pending", "graduated", "suspended"],
    "state": ["TX", "CA", "NY", "WA", "IL", "RJ", "GJ"],
    "type": ["A", "B", "C", "D"],
    "category": ["general", "obc", "sc", "st", "ews"],
    "gender": ["F", "M", "X"],
    "method": ["card", "upi", "cash", "bank"],
    "semester": ["1", "2", "3", "4", "5", "6", "7", "8"],
}
_FIRST = ["Aarav", "Diya", "Kabir", "Isha", "Rohan", "Meera", "Arjun", "Sara", "Vivaan", "Anaya", "Dev", "Riya"]
_LAST = ["Patel", "Shah", "Sharma", "Mehta", "Gupta", "Singh", "Joshi", "Desai", "Rao", "Iyer"]
_CITY = ["Jaipur", "Ahmedabad", "Dallas", "Arlington", "Austin", "Pune", "Udaipur", "Surat"]


def _parents_first(conn: sqlite3.Connection, tables: list[str]) -> list[str]:
    deps = {t: {r[2] for r in conn.execute(f"PRAGMA foreign_key_list({t})") if r[2] != t} for t in tables}
    order, done = [], set()
    while len(order) < len(tables):
        ready = [t for t in tables if t not in done and deps[t] <= done]
        if not ready:  # cycle: fall back to declared order
            ready = [t for t in tables if t not in done]
        for t in ready:
            order.append(t)
            done.add(t)
    return order


def _value(rng: random.Random, name: str, decl: str, i: int, fk_max: int | None, n: int):
    lname, ltype = name.lower(), (decl or "").upper()
    if fk_max is not None:
        return rng.randint(1, max(fk_max, 1))
    if lname.endswith("_id"):
        return rng.randint(1, max(n // 10, 1))
    for key, values in _LOW_CARD.items():
        if key in lname:
            # Skewed like real data: the first value is common, the rest are rarer.
            weights = [max(60 - 15 * k, 4) for k in range(len(values))]
            return rng.choices(values, weights=weights)[0]
    if "email" in lname:
        return f"user{i}@example.edu"
    if lname in ("name", "full_name", "student_name") or lname.endswith("_name"):
        return f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
    if "city" in lname:
        return rng.choice(_CITY)
    if "date" in lname or lname.endswith("_at") or "DATE" in ltype or "TIME" in ltype:
        day = dt.date(2026, 10, 1) - dt.timedelta(days=rng.randint(0, 3 * 365))
        return day.isoformat()
    if any(k in lname for k in ("amount", "price", "fee", "total", "balance")) or "REAL" in ltype or "NUM" in ltype or "DEC" in ltype:
        return round(rng.uniform(100, 50_000), 2)
    if "INT" in ltype:
        return rng.randint(0, 1000)
    return f"{lname}_{rng.randint(1, 5000)}"


def populate(conn: sqlite3.Connection, rows: int = 50_000, seed: int = 7) -> dict[str, int]:
    """Insert rows into every table. Tables that others point to get fewer rows (like real lookup tables)."""
    rng = random.Random(seed)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    referenced = {r[2] for t in tables for r in conn.execute(f"PRAGMA foreign_key_list({t})")}
    counts: dict[str, int] = {}
    for table in _parents_first(conn, tables):
        n = max(rows // 10, 50) if table in referenced else rows
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        fks = {r[3]: r[2] for r in conn.execute(f"PRAGMA foreign_key_list({table})")}
        cols = [c for c in info if not (c[5] == 1 and "INT" in (c[2] or "").upper())]  # skip rowid PK
        names = [c[1] for c in cols]
        placeholders = ", ".join("?" for _ in names)
        batch = []
        for i in range(1, n + 1):
            batch.append(tuple(
                _value(rng, c[1], c[2], i, counts.get(fks[c[1]]) if c[1] in fks else None, n) for c in cols
            ))
        conn.executemany(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({placeholders})", batch)
        counts[table] = n
    conn.commit()
    return counts

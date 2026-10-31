"""Core analysis: build a test database, time a query, read its plan, suggest indexes, re-time.

Everything runs on SQLite from the Python standard library, so the tool has no dependencies.
"""
from __future__ import annotations

import re
import sqlite3
import statistics
import time
from dataclasses import dataclass, field, asdict

from .datagen import populate

# SQL keywords that can never be column names in the clauses we scan.
_KEYWORDS = {
    "and", "or", "not", "in", "is", "null", "like", "between", "exists", "select", "from", "where",
    "join", "inner", "left", "right", "outer", "cross", "on", "as", "group", "by", "order", "having",
    "limit", "offset", "asc", "desc", "case", "when", "then", "else", "end", "distinct", "count",
    "sum", "avg", "min", "max", "date", "true", "false", "union", "all", "using", "lower", "upper",
}

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"


@dataclass
class IndexSuggestion:
    table: str
    columns: list[str]
    reason: str

    @property
    def name(self) -> str:
        return "idx_" + self.table + "_" + "_".join(self.columns)

    @property
    def sql(self) -> str:
        cols = ", ".join(self.columns)
        return f"CREATE INDEX {self.name} ON {self.table} ({cols});"


@dataclass
class Report:
    query: str
    rows_per_table: dict[str, int]
    result_rows: int
    before_ms: float
    after_ms: float
    plan_before: list[str]
    plan_after: list[str]
    full_scans: list[str]
    suggestions: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def speedup(self) -> float:
        return round(self.before_ms / self.after_ms, 1) if self.after_ms > 0 else 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["speedup"] = self.speedup
        return d


# --------------------------------------------------------------------------- schema helpers

def _tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [r[0] for r in rows]


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _primary_key(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in sorted(rows, key=lambda r: r[5]) if r[5] > 0]


def _existing_index_leads(conn: sqlite3.Connection, table: str) -> set[str]:
    """First column of every index already on the table (including the primary key)."""
    leads = set(_primary_key(conn, table)[:1])
    for idx in conn.execute(f"PRAGMA index_list({table})").fetchall():
        cols = conn.execute(f"PRAGMA index_info({idx[1]})").fetchall()
        if cols:
            leads.add(cols[0][2])
    return leads


# --------------------------------------------------------------------------- query parsing

def _strip_strings(sql: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", "''", sql)


def _clause(sql: str, start: str, enders: list[str]) -> str:
    m = re.search(rf"\b{start}\b(.*)", sql, re.I | re.S)
    if not m:
        return ""
    body = m.group(1)
    cut = len(body)
    for e in enders:
        em = re.search(rf"\b{e}\b", body, re.I)
        if em:
            cut = min(cut, em.start())
    return body[:cut]


def _aliases(sql: str, tables: list[str]) -> dict[str, str]:
    """Map alias (and bare table name) -> table, from FROM and JOIN clauses."""
    lower = {t.lower(): t for t in tables}
    out: dict[str, str] = {}
    for m in re.finditer(rf"\b(?:from|join)\s+({_IDENT})(?:\s+(?:as\s+)?({_IDENT}))?", sql, re.I):
        table = lower.get(m.group(1).lower())
        if not table:
            continue
        out[table.lower()] = table
        alias = m.group(2)
        if alias and alias.lower() not in _KEYWORDS:
            out[alias.lower()] = table
    return out


def _refs(text: str, aliases: dict[str, str], cols_by_table: dict[str, set[str]],
          query_tables: list[str]) -> list[tuple[str, str]]:
    """Find (table, column) references in a clause."""
    found: list[tuple[str, str]] = []
    for m in re.finditer(rf"\b(?:({_IDENT})\.)?({_IDENT})\b", text):
        qual, col = m.group(1), m.group(2)
        if col.lower() in _KEYWORDS or col.isdigit():
            continue
        if qual:
            table = aliases.get(qual.lower())
            if table and col in cols_by_table[table]:
                found.append((table, col))
            continue
        owners = [t for t in query_tables if col in cols_by_table[t]]
        if len(owners) == 1:
            found.append((owners[0], col))
    return found


def _classify_where(where: str, aliases, cols_by_table, query_tables):
    """Split WHERE references into equality and range predicates (equality columns lead an index)."""
    eq, rng = [], []
    for part in re.split(r"\band\b|\bor\b", where, flags=re.I):
        refs = _refs(part, aliases, cols_by_table, query_tables)
        if not refs:
            continue
        is_eq = re.search(r"(?<![<>!])=|\bin\s*\(|\bis\s+null", part, re.I) and not re.search(r"[<>]|\bbetween\b|\blike\b", part, re.I)
        # a.col = b.col inside WHERE is really a join condition: treat both sides as equality
        (eq if is_eq else rng).extend(refs)
    return eq, rng


# --------------------------------------------------------------------------- planning

def explain(conn: sqlite3.Connection, query: str) -> list[str]:
    return [r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + query).fetchall()]


def full_scans(plan: list[str]) -> list[str]:
    """Tables SQLite reads row by row. 'SCAN t USING COVERING INDEX' is fine, a bare SCAN is not."""
    out = []
    for line in plan:
        m = re.match(rf"SCAN ({_IDENT})(?:\s+AS\s+{_IDENT})?\s*$", line.strip())
        if m:
            out.append(m.group(1))
    return out


def suggest_indexes(conn: sqlite3.Connection, query: str) -> list[IndexSuggestion]:
    tables = _tables(conn)
    cols_by_table = {t: set(_columns(conn, t)) for t in tables}
    sql = _strip_strings(query)
    aliases = _aliases(sql, tables)
    query_tables = sorted(set(aliases.values()))

    where = _clause(sql, "where", ["group by", "order by", "having", "limit"])
    joins = " ".join(m.group(1) for m in re.finditer(
        r"\bon\b(.*?)(?=\b(?:join|left|inner|right|where|group|order|limit)\b|$)", sql, re.I | re.S))
    order = _clause(sql, "order by", ["limit", "offset"])
    group = _clause(sql, "group by", ["having", "order by", "limit"])

    eq, rng = _classify_where(where, aliases, cols_by_table, query_tables)
    join_refs = _refs(joins, aliases, cols_by_table, query_tables)
    order_refs = _refs(order, aliases, cols_by_table, query_tables) + _refs(group, aliases, cols_by_table, query_tables)

    suggestions: list[IndexSuggestion] = []
    for table in query_tables:
        pk = set(_primary_key(conn, table))
        leads = _existing_index_leads(conn, table)

        def pick(refs):
            seen = []
            for t, c in refs:
                if t == table and c not in seen and c not in pk:
                    seen.append(c)
            return seen

        t_eq, t_rng, t_join, t_ord = pick(eq), pick(rng), pick(join_refs), pick(order_refs)

        # Join keys get their own index so the join can look rows up instead of scanning.
        # Skip when this table has its own filter: SQLite will start the join from it.
        if not (t_eq or t_rng):
            for c in t_join:
                if c not in leads:
                    suggestions.append(IndexSuggestion(table, [c], f"join key {table}.{c} has no index"))
                    leads.add(c)

        # Filters: equality columns first, then at most one range column, then sort columns.
        cols = t_eq + [c for c in t_rng if c not in t_eq][:1]
        if cols:
            cols += [c for c in t_ord if c not in cols]
            if cols[0] not in leads or len(cols) > 1:
                parts = []
                if t_eq:
                    parts.append("equality filter on " + ", ".join(t_eq))
                if len(cols) > len(t_eq):
                    parts.append("then range/sort on " + ", ".join(cols[len(t_eq):]))
                suggestions.append(IndexSuggestion(table, cols[:4], "; ".join(parts)))
        elif t_ord and t_ord[0] not in leads:
            suggestions.append(IndexSuggestion(table, t_ord[:3], "sort/group without a matching index"))
    return suggestions


# --------------------------------------------------------------------------- timing

def time_query(conn: sqlite3.Connection, query: str, runs: int = 5) -> tuple[float, int]:
    samples, n = [], 0
    conn.execute(query).fetchall()  # warm-up
    for _ in range(runs):
        t0 = time.perf_counter()
        n = len(conn.execute(query).fetchall())
        samples.append((time.perf_counter() - t0) * 1000)
    return round(statistics.median(samples), 3), n


def analyze(schema_sql: str, query: str, rows: int = 50_000, seed: int = 7, runs: int = 5) -> Report:
    if not re.match(r"\s*(select|with)\b", query, re.I):
        raise ValueError("Only SELECT queries can be analyzed.")
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(schema_sql)
        counts = populate(conn, rows=rows, seed=seed)
        conn.execute("ANALYZE")

        plan_before = explain(conn, query)
        scans = full_scans(plan_before)
        before_ms, n = time_query(conn, query, runs)

        candidates = suggest_indexes(conn, query)
        notes = []
        # Keep an index only if it measurably helps (>= 10% faster). Indexes cost writes and disk.
        suggestions, current = [], before_ms
        for s in candidates:
            conn.execute(s.sql)
            conn.execute("ANALYZE")
            ms, _ = time_query(conn, query, runs)
            if ms <= current * 0.9:
                suggestions.append(s)
                current = ms
            else:
                conn.execute(f"DROP INDEX {s.name}")
                notes.append(f"Tried {s.sql} but it did not make this query faster, so it was dropped.")
        conn.execute("ANALYZE")
        plan_after = explain(conn, query)
        after_ms, n_after = time_query(conn, query, runs)
        if n_after != n:
            notes.append("Row count changed after indexing; please report this as a bug.")
        if not candidates:
            notes.append("No missing indexes found for the tables this query filters, joins or sorts.")
        if scans and not suggestions:
            notes.append("Full scans remain; the query may need rewriting (functions on columns, leading wildcards in LIKE, OR across columns).")

        return Report(
            query=query.strip(),
            rows_per_table=counts,
            result_rows=n,
            before_ms=before_ms,
            after_ms=after_ms,
            plan_before=plan_before,
            plan_after=plan_after,
            full_scans=scans,
            suggestions=[{"table": s.table, "columns": s.columns, "reason": s.reason, "sql": s.sql} for s in suggestions],
            notes=notes,
        )
    finally:
        conn.close()

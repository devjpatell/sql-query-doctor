import sqlite3
from pathlib import Path

import pytest

from sqldoctor import analyze, suggest_indexes, full_scans
from sqldoctor.datagen import populate

SCHEMA = (Path(__file__).parent.parent / "examples" / "college.sql").read_text()


def db(rows=2_000):
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    populate(conn, rows=rows, seed=1)
    return conn


def cols(sugs):
    return {(s.table, tuple(s.columns)) for s in sugs}


def test_full_scan_detection():
    plan = ["SCAN f", "SEARCH s USING INTEGER PRIMARY KEY (rowid=?)", "SCAN t USING COVERING INDEX i"]
    assert full_scans(plan) == ["f"]


def test_equality_then_sort_column_order():
    q = "SELECT amount FROM fee_payments WHERE student_id = 5 ORDER BY paid_at"
    assert ("fee_payments", ("student_id", "paid_at")) in cols(suggest_indexes(db(), q))


def test_equality_before_range():
    q = "SELECT * FROM fee_payments f WHERE f.paid_at >= '2026-01-01' AND f.status = 'pending'"
    assert ("fee_payments", ("status", "paid_at")) in cols(suggest_indexes(db(), q))


def test_unqualified_column_resolved_to_query_table_only():
    # student_id exists in two tables; only fee_payments is in the query
    q = "SELECT * FROM fee_payments WHERE student_id = 9"
    assert cols(suggest_indexes(db(), q)) == {("fee_payments", ("student_id",))}


def test_join_key_indexed_when_table_has_no_filter():
    q = "SELECT s.full_name FROM students s JOIN enrollments e ON e.student_id = s.id WHERE s.status = 'active'"
    assert ("enrollments", ("student_id",)) in cols(suggest_indexes(db(), q))


def test_primary_key_lookup_needs_nothing():
    assert suggest_indexes(db(), "SELECT * FROM students WHERE id = 3") == []


def test_existing_index_is_respected():
    conn = db()
    conn.execute("CREATE INDEX idx_courses_semester ON courses (semester)")
    assert suggest_indexes(conn, "SELECT * FROM courses WHERE semester = '3'") == []


def test_string_literals_are_ignored():
    q = "SELECT * FROM students WHERE full_name = 'order by status'"
    assert cols(suggest_indexes(db(), q)) == {("students", ("full_name",))}


def test_analyze_end_to_end_is_faster_and_keeps_results():
    r = analyze(SCHEMA, "SELECT amount FROM fee_payments WHERE student_id = 42", rows=50_000, runs=3)
    assert r.full_scans == ["fee_payments"]
    assert r.suggestions and r.after_ms < r.before_ms
    assert not any("Row count changed" in n for n in r.notes)


def test_rejects_non_select():
    with pytest.raises(ValueError):
        analyze(SCHEMA, "DELETE FROM students")


def test_foreign_keys_point_at_real_rows():
    conn = db()
    orphans = conn.execute(
        "SELECT COUNT(*) FROM fee_payments f LEFT JOIN students s ON s.id = f.student_id WHERE s.id IS NULL"
    ).fetchone()[0]
    assert orphans == 0

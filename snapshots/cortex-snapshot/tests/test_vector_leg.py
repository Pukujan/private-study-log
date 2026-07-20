"""ADR-0001 (gate 2.1): sqlite-vec must load as a real SQLite extension.

This is the CI-matrix proof the gate calls for -- CI runs windows-latest
across Python 3.10/3.11/3.12 (`.github/workflows/ci.yml`), so this test
running green there is the actual evidence, not a one-off local check.
"""

import sqlite3

import sqlite_vec


def test_sqlite_vec_loads_and_answers_a_knn_query():
    conn = sqlite3.connect(":memory:")
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)

    (version,) = conn.execute("select vec_version()").fetchone()
    assert version

    conn.execute("create virtual table vec_test using vec0(embedding float[4])")
    conn.execute(
        "insert into vec_test(rowid, embedding) values (1, ?)",
        (sqlite_vec.serialize_float32([0.1, 0.2, 0.3, 0.4]),),
    )
    conn.execute(
        "insert into vec_test(rowid, embedding) values (2, ?)",
        (sqlite_vec.serialize_float32([0.9, 0.8, 0.7, 0.6]),),
    )

    # Use the explicit `k = ?` KNN constraint (the canonical vec0 form) rather than a
    # bare `limit`: newer sqlite-vec builds *require* `k = ?` (or a pushed-down LIMIT) and
    # reject a plain `order by ... limit`, which broke the windows/3.10 runner's wheel.
    rows = conn.execute(
        "select rowid, distance from vec_test where embedding match ? and k = ? "
        "order by distance",
        (sqlite_vec.serialize_float32([0.1, 0.2, 0.3, 0.4]), 5),
    ).fetchall()

    assert rows[0] == (1, 0.0)
    assert rows[0][1] < rows[1][1]

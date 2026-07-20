"""Deterministic checkers for the highest-risk SILENT failure mode in Cortex:
retrieval that returns nothing (or misses the answer) while the corpus actually
contains it. Silent because it raises no error -- search just hands back `[]`, and
every consumer (scope-pack, deep-research, an agent) degrades invisibly.

Two versioned checkers:

  * ``fts5_safe(query)`` -- does this query, once normalized, run against FTS5 WITHOUT
    a syntax error? Guards the exact class of the 2026-07-05 regression: a version
    token like ``GLM-5.2`` normalized to ``... AND 5.2 AND ...``; the ``5.2`` threw
    ``fts5: syntax error near "."`` which silently dropped the whole query to the LIKE
    fallback and returned 0 hits even though the doc was indexed.

  * ``retrieval_canary(index, cases)`` -- run known (query -> expected) pairs against a
    live index and report which SILENTLY return zero / miss the expected doc. This turns
    a silent retrieval regression into a loud, testable, promotion-gateable failure.

These are CHECKERS (deterministic pass/fail), not judges. See docs/CHECKERS.md for the
false-positive / false-negative envelope.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .search import _normalize_query

CHECKER_VERSION = "retrieval_health/v1"


def fts5_safe(query: str) -> bool:
    """True iff the query's normalized AND/OR forms execute against FTS5 without a
    syntax error. Deterministic: builds a throwaway in-memory FTS5 table and actually
    runs the MATCH -- no heuristic guessing about which characters FTS5 dislikes."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(content)")
        conn.execute("INSERT INTO t(content) VALUES ('probe row')")
        for joiner in (" AND ", " OR "):
            mq = _normalize_query(query, joiner=joiner)
            if not mq:
                continue
            try:
                conn.execute("SELECT rowid FROM t WHERE t MATCH ?", (mq,)).fetchall()
            except sqlite3.OperationalError:
                # We own the table, so the ONLY source of an OperationalError here is a
                # malformed MATCH query (syntax error, unterminated string, unknown
                # column, ...). Any of them => the query is unsafe to run against FTS5.
                return False
        return True
    finally:
        conn.close()


@dataclass
class CanaryCase:
    query: str
    expect_nonzero: bool = True          # the query MUST return at least one hit
    expect_path_substr: str | None = None  # ... and (optionally) this path must appear


@dataclass
class CanaryReport:
    checker_version: str
    total: int
    passed: int
    failures: list = field(default_factory=list)  # list of {query, reason}

    @property
    def ok(self) -> bool:
        return not self.failures


def retrieval_canary(index, cases: list[CanaryCase]) -> CanaryReport:
    """Run each canary case against a live search index. A case FAILS (a caught silent
    failure) when a query that should retrieve returns zero, or misses its expected
    doc. `index` is any object with `.search(query, limit=...) -> [SearchResult]`."""
    failures: list[dict] = []
    for case in cases:
        results = index.search(case.query, limit=10)
        if case.expect_nonzero and not results:
            failures.append({"query": case.query, "reason": "SILENT ZERO (indexed answer not returned)"})
            continue
        if case.expect_path_substr is not None:
            if not any(case.expect_path_substr in (r.path or "") for r in results):
                failures.append({"query": case.query,
                                 "reason": f"expected doc containing {case.expect_path_substr!r} not surfaced"})
    passed = len(cases) - len(failures)
    return CanaryReport(CHECKER_VERSION, len(cases), passed, failures)

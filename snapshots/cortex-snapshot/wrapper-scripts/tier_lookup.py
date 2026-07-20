#!/usr/bin/env python3
"""Lazy model-tier lookup — query a few models, never resident-read all 358.

`.cortex/models.tiers.md` is ~8.4k tokens (358 rows). Loading it into context
every task to "pick a model" is a Disease-A context bomb — the exact no-MCP /
no-context-bloat rule this wrapper is built around. This helper parses the table
and returns JUST the requested tier's routable models, so the agent reads a
handful of lines instead of the whole table.

"Routable" = the onboarding's rule (models.tiers.md header): only `allow=yes`
AND a `live` status are safe to route to. Default output enforces both.
`--allow-only` relaxes the live requirement (allow=yes, any status) for when
nothing is currently responding and you just want the tier's allow-list.

Parsing is deliberately forgiving: it reads the pipe table, pulls
`model_id | tier | allow | status`, and ignores everything else (header prose,
the counts line, blank rows). No PyYAML, no markdown lib.

Stdlib only. Offline. No install.

CLI:
    python tier_lookup.py --tier strong                 # allow=yes + live, this tier
    python tier_lookup.py --tier strong --limit 5       # top-N (table order)
    python tier_lookup.py --tier medium --allow-only    # allow=yes, live not required
    python tier_lookup.py --tier weak --json            # structured rows
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_DEFAULT_TIERS = _HERE.parent / "models.tiers.md"

_TIERS = ("strong", "medium", "weak", "utility")

# One table row: | `model_id` | tier | allow | status | notes |
# The model_id cell is wrapped in backticks; the rest are bare words.
_ROW = re.compile(
    r"^\|\s*`(?P<model_id>[^`]+)`\s*"
    r"\|\s*(?P<tier>\w+)\s*"
    r"\|\s*(?P<allow>\w+)\s*"
    r"\|\s*(?P<status>[^|]*?)\s*"
    r"\|",
)


def _is_live(status: str) -> bool:
    """A model is routable-live only when the probe recorded a `live·Nms` status.

    'empty' (200 w/o content) and any 'error:*' are NOT live — the header is
    explicit that only live models are safe to route to.
    """
    return status.strip().lower().startswith("live")


def parse_rows(text: str) -> list[dict[str, Any]]:
    """Parse every model row from the tiers markdown. Order preserved (table order)."""
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        m = _ROW.match(line.rstrip())
        if not m:
            continue
        tier = m.group("tier").strip().lower()
        # Skip the header separator / header label rows that can look tabley.
        if tier not in _TIERS:
            continue
        status = m.group("status").strip()
        rows.append({
            "model_id": m.group("model_id").strip(),
            "tier": tier,
            "allow": m.group("allow").strip().lower() == "yes",
            "status": status,
            "live": _is_live(status),
        })
    return rows


def lookup(
    text: str,
    tier: str,
    *,
    allow_only: bool = False,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return routable models for `tier`.

    Default: allow=yes AND live. `allow_only=True`: allow=yes, live not required.
    `limit`: cap to the first N (table order). Only the asked tier is ever returned.
    """
    tier = tier.strip().lower()
    if tier not in _TIERS:
        raise ValueError(f"unknown tier {tier!r}; expected one of {_TIERS}")
    out = []
    for r in parse_rows(text):
        if r["tier"] != tier:
            continue
        if not r["allow"]:
            continue
        if not allow_only and not r["live"]:
            continue
        out.append(r)
    if limit is not None:
        out = out[: max(0, limit)]
    return out


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Lazy per-tier model lookup — query a few, never read all 358.")
    ap.add_argument("--tier", required=True, choices=_TIERS)
    ap.add_argument("--allow-only", action="store_true",
                    help="allow=yes only (do not require a live status)")
    ap.add_argument("--limit", type=int, default=None, help="cap to first N (table order)")
    ap.add_argument("--json", action="store_true", help="emit structured rows as JSON")
    ap.add_argument("--tiers-file", default=str(_DEFAULT_TIERS),
                    help="path to models.tiers.md (default: the wrapper's)")
    args = ap.parse_args(argv)

    path = Path(args.tiers_file)
    if not path.exists():
        print(f"tiers file not found: {path}", file=sys.stderr)
        return 2
    text = path.read_text(encoding="utf-8")
    rows = lookup(text, args.tier, allow_only=args.allow_only, limit=args.limit)

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            print(r["model_id"])
        if not rows:
            hint = "" if args.allow_only else " (try --allow-only; none are live right now)"
            print(f"# no routable {args.tier} models{hint}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))

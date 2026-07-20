"""Independent second implementation of the datetime oracle (D1 cross-validation).

This module deliberately shares NO CODE with
`evals.objective_datetime_correctness.checker_datetime`. Where the primary checker leans on
Python's high-level `datetime` / `calendar` / `zoneinfo`, this one computes every answer from
first principles with integer arithmetic over the **Julian Day Number (JDN)** and the explicit
Gregorian leap rule. Two independent algorithms that agree on a candidate's verdict give the
BFCL-style "measured agreement between two implementations" signal (see
`evals/objective_tool_calling/STAGE2A_TOOL_CALLING_REPORT.md`, cross-checker agreement 99.93%).

One op — `tz_convert` — is HONESTLY not reimplementable here: it needs the IANA time-zone
database, of which there is no second, independent copy in the stdlib. `independent_verdict`
returns ``"abstain"`` for it rather than fake an answer. Every other op is fully independent.
"""

from __future__ import annotations

_MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _is_leap(y: int) -> bool:
    return (y % 4 == 0 and y % 100 != 0) or (y % 400 == 0)


def _last_day(y: int, m: int) -> int:
    if m == 2:
        return 29 if _is_leap(y) else 28
    return _MONTH_DAYS[m - 1]


def _parse_ymd(s: str) -> tuple[int, int, int]:
    y, m, d = s.split("-")
    return int(y), int(m), int(d)


def gregorian_to_jdn(y: int, m: int, d: int) -> int:
    """Fliegel & Van Flandern conversion, proleptic Gregorian, integer-only."""
    a = (14 - m) // 12
    yy = y + 4800 - a
    mm = m + 12 * a - 3
    return d + (153 * mm + 2) // 5 + 365 * yy + yy // 4 - yy // 100 + yy // 400 - 32045


def jdn_to_gregorian(jdn: int) -> tuple[int, int, int]:
    """Inverse of gregorian_to_jdn (Richards algorithm), integer-only."""
    a = jdn + 32044
    b = (4 * a + 3) // 146097
    c = a - (146097 * b) // 4
    d = (4 * c + 3) // 1461
    e = c - (1461 * d) // 4
    m = (5 * e + 2) // 153
    day = e - (153 * m + 2) // 5 + 1
    month = m + 3 - 12 * (m // 10)
    year = 100 * b + d - 4800 + m // 10
    return year, month, day


def _iso(y: int, m: int, d: int) -> str:
    return f"{y:04d}-{m:02d}-{d:02d}"


# --------------------------------------------------------------------------- per-op computation
def add_months(start: str, months: int) -> str:
    y, m, d = _parse_ymd(start)
    total = y * 12 + (m - 1) + months
    ny, nm0 = divmod(total, 12)
    nm = nm0 + 1
    day = min(d, _last_day(ny, nm))
    return _iso(ny, nm, day)


def add_days(start: str, days: int) -> str:
    y, m, d = _parse_ymd(start)
    ny, nm, nd = jdn_to_gregorian(gregorian_to_jdn(y, m, d) + days)
    return _iso(ny, nm, nd)


def is_leap_year(year: int) -> str:
    return "leap" if _is_leap(year) else "not_leap"


def weekday(date_str: str) -> str:
    y, m, d = _parse_ymd(date_str)
    # JDN mod 7: JDN 0 was a Monday, so index 0 == Monday.
    return _WEEKDAYS[gregorian_to_jdn(y, m, d) % 7]


def day_diff(start: str, end: str, inclusive: bool) -> int:
    ys, ms, ds = _parse_ymd(start)
    ye, me, de = _parse_ymd(end)
    n = gregorian_to_jdn(ye, me, de) - gregorian_to_jdn(ys, ms, ds)
    return n + 1 if inclusive else n


def iso_week(date_str: str) -> str:
    """ISO-8601 week date computed from the JDN, no `datetime.isocalendar`."""
    y, m, d = _parse_ymd(date_str)
    jdn = gregorian_to_jdn(y, m, d)
    iso_wd = (jdn % 7) + 1                       # Mon=1 .. Sun=7
    doy = jdn - gregorian_to_jdn(y, 1, 1) + 1    # 1-based day of calendar year
    week = (doy - iso_wd + 10) // 7
    if week < 1:                                 # belongs to the last week of the prior year
        iso_year = y - 1
        week = _weeks_in_year(iso_year)
    elif week > _weeks_in_year(y):               # belongs to week 1 of the next year
        iso_year = y + 1
        week = 1
    else:
        iso_year = y
    return f"{iso_year}-W{week:02d}"


def _weeks_in_year(y: int) -> int:
    """A long ISO year (53 weeks) is one whose Jan 1 is Thursday, or a leap year whose Jan 1 is
    Wednesday. Computed from the weekday of Jan 1, independently."""
    jan1_wd = (gregorian_to_jdn(y, 1, 1) % 7) + 1   # Mon=1..Sun=7
    if jan1_wd == 4 or (jan1_wd == 3 and _is_leap(y)):
        return 53
    return 52


# --------------------------------------------------------------------------- dispatch
def independent_verdict(op: str, inputs: dict, candidate_answer) -> str:
    """Return 'pass' | 'fail' | 'abstain' for a datetime candidate, independently of the
    primary checker. 'abstain' == the second authority cannot decide this op (tz_convert)."""
    if op == "tz_convert":
        return "abstain"      # needs the IANA tz DB; no independent stdlib copy exists
    fn = {
        "add_months": lambda i: add_months(i["start"], i["months"]),
        "add_days": lambda i: add_days(i["start"], i["days"]),
        "is_leap_year": lambda i: is_leap_year(i["year"]),
        "weekday": lambda i: weekday(i["date"]),
        "day_diff": lambda i: day_diff(i["start"], i["end"], i["inclusive"]),
        "iso_week": lambda i: iso_week(i["date"]),
    }.get(op)
    if fn is None:
        return "abstain"
    return "pass" if candidate_answer == fn(inputs) else "fail"

"""Evidence that must exist before a write: is there attendance data for the month about to be paid?

Pure rules. ``workers.run_run_payroll`` counts attendance rows per day (the list tool filters on an exact
date only) and this module decides what the counts mean. It judges coverage; it does not judge pay.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any

MAX_DAYS = 31
TAIL_TOLERANCE_DAYS = 3        # a weekend plus a holiday at the end of a window is not missing data


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def window_days(*, month: Any = None, start: Any = None, end: Any = None) -> list[str] | None:
    """Every ISO date in a calendar month (``YYYY-MM``) or in ``start``..``end``; None when unusable."""
    if month is not None:
        try:
            year, number = int(str(month)[:4]), int(str(month)[5:7])
            first = date(year, number, 1)
            last = date(year, number, calendar.monthrange(year, number)[1])
        except ValueError:
            return None
    else:
        first, last = _day(start), _day(end)
        if first is None or last is None:
            return None
    if last < first or (last - first).days + 1 > MAX_DAYS:
        return None
    return [(first + timedelta(days=offset)).isoformat() for offset in range((last - first).days + 1)]


def assess(counts: dict[str, int | None]) -> dict[str, Any]:
    """What per-day attendance row counts say about a window. ``None`` means a day could not be counted."""
    days = sorted(counts)
    unreadable = [d for d in days if counts[d] is None]
    with_rows = [d for d in days if (counts[d] or 0) > 0]
    last = max(with_rows) if with_rows else None
    tail = (date.fromisoformat(days[-1]) - date.fromisoformat(last)).days if last else None
    if unreadable:
        verdict = "unverified"
    elif not with_rows:
        verdict = "none"
    elif tail > TAIL_TOLERANCE_DAYS:
        verdict = "partial"
    else:
        verdict = "full"
    return {"verdict": verdict, "period_days": len(days), "days_with_rows": len(with_rows),
            "last_date_with_rows": last, "unreadable_days": unreadable, "window": [days[0], days[-1]]}

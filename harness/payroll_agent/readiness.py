"""Payroll readiness: pure rules for what could make a run wrong before it is calculated or submitted.

Attendance coverage, pending leave, joiner/leaver proration, LOP mismatches and Basic below 50% of
gross. No network: ``workers.run_payroll_readiness`` fetches, this module judges. A finding states
what the rows show; it does not decide whether the run is wrong.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date, timedelta
from typing import Any

from .cost_report import _finite as finite

MAX_DAYS = 45
MAX_LOP_CANDIDATES = 25
MAX_FINDINGS = 40
BASIC_SHARE_MIN = 50.0
TAIL_TOLERANCE_DAYS = 3        # a weekend plus a holiday at the end of the period is not missing data
_SEVERITY_ORDER = {"high": 0, "medium": 1, "info": 2}


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _finding(check: str, severity: str, employee_id: Any, employee_name: Any, message: str,
             evidence: dict[str, Any]) -> dict[str, Any]:
    return {"check": check, "severity": severity, "employee_id": employee_id, "employee_name": employee_name,
            "message": message, "evidence": evidence}


def _name(person: dict[str, Any] | None, row: dict[str, Any] | None = None) -> str | None:
    if row and row.get("employee_name"):
        return row["employee_name"]
    if person:
        return " ".join(str(person.get(k)) for k in ("first_name", "last_name") if person.get(k)) or None
    return None


def period_dates(start: Any, end: Any) -> list[str] | None:
    first, last = _day(start), _day(end)
    if first is None or last is None or last < first or (last - first).days + 1 > MAX_DAYS:
        return None
    return [(first + timedelta(days=offset)).isoformat() for offset in range((last - first).days + 1)]


def proration(rows: list[dict[str, Any]], employees: dict[Any, dict[str, Any]], start: Any, end: Any) -> list[dict[str, Any]]:
    first, last = _day(start), _day(end)
    found: list[dict[str, Any]] = []
    if first is None or last is None:
        return found
    for row in rows:
        person = employees.get(row.get("employee_id"))
        days, working = finite(row.get("days_payable")), finite(row.get("working_days"))
        if person is None or days is None or not working or working <= 0:
            continue
        prorated = days < working
        joined, left = _day(person.get("date_of_joining")), _day(person.get("exit_date"))
        joins_inside = joined is not None and first < joined <= last
        leaves_inside = left is not None and first <= left < last
        evidence = {"days_payable": days, "working_days": working, "date_of_joining": person.get("date_of_joining"),
                    "exit_date": person.get("exit_date")}
        who = (row.get("employee_id"), _name(person, row))
        if joins_inside and not prorated:
            found.append(_finding("proration", "high", *who, f"Joined {joined.isoformat()} inside the period but "
                                  f"paid {days:g} of {working:g} days (not prorated)", evidence))
        if leaves_inside and not prorated:
            found.append(_finding("proration", "high", *who, f"Exit date {left.isoformat()} is inside the period "
                                  f"but paid {days:g} of {working:g} days (not prorated)", evidence))
        if prorated and not (joins_inside or leaves_inside) and (finite(row.get("lop_days")) or 0.0) == 0:
            found.append(_finding("proration", "info", *who, f"Paid {days:g} of {working:g} days with no joining or "
                                  "exit date in the period and no LOP recorded", evidence))
    return found


def pending_leave(applications: list[dict[str, Any]], employees: dict[Any, dict[str, Any]], start: Any,
                  end: Any) -> list[dict[str, Any]]:
    first, last = _day(start), _day(end)
    severity = {"pending_approval": "medium", "draft": "info"}
    found: list[dict[str, Any]] = []
    if first is None or last is None:
        return found
    for item in applications:
        person = employees.get(item.get("employee_id"))
        from_date, to_date = _day(item.get("from_date")), _day(item.get("to_date"))
        if (person is None or item.get("status") not in severity or from_date is None or to_date is None
                or from_date > last or to_date < first):
            continue
        found.append(_finding("pending_leave", severity[item["status"]], item.get("employee_id"), _name(person),
                              f"Leave {item.get('number')} ({item['status']}) {from_date.isoformat()} to "
                              f"{to_date.isoformat()} overlaps the period and may change days payable",
                              {"application_id": item.get("id"), "number": item.get("number"),
                               "from_date": item.get("from_date"), "to_date": item.get("to_date")}))
    return found


def attendance_coverage(day_counts: dict[str, int | None], start: Any, end: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    days = sorted(day_counts)
    unreadable = [d for d in days if day_counts[d] is None]
    with_rows = [d for d in days if (day_counts[d] or 0) > 0]
    last_row = max(with_rows) if with_rows else None
    tail = [d for d in days if last_row is None or d > last_row]
    coverage = {"period_days": len(days), "days_with_rows": len(with_rows), "last_date_with_rows": last_row,
                "uncovered_tail_days": len(tail) if last_row is not None else len(days),
                "unreadable_days": unreadable}
    found: list[dict[str, Any]] = []
    if not with_rows and not unreadable:
        found.append(_finding("attendance_coverage", "high", None, None,
                              f"No attendance rows at all between {start} and {end}", coverage))
    elif with_rows and len(tail) > TAIL_TOLERANCE_DAYS and not unreadable:
        found.append(_finding("attendance_coverage", "medium", None, None,
                              f"Attendance has no rows after {last_row} ({len(tail)} days before {end})", coverage))
    if unreadable:
        found.append(_finding("attendance_coverage", "info", None, None,
                              f"Attendance could not be counted for {len(unreadable)} day(s): {', '.join(unreadable[:5])}",
                              coverage))
    return coverage, found


def lop_mismatch(row: dict[str, Any], lop_rows: list[dict[str, Any]], start: Any, end: Any) -> dict[str, Any] | None:
    first, last = _day(start), _day(end)
    line_lop = finite(row.get("lop_days"))
    if first is None or last is None or line_lop is None:
        return None
    counted = sum(1 for item in lop_rows if item.get("is_lop") and (d := _day(item.get("date"))) and first <= d <= last)
    if abs(counted - line_lop) < 0.5:
        return None
    return _finding("lop_mismatch", "medium", row.get("employee_id"), row.get("employee_name"),
                    f"The pay line has {line_lop:g} LOP days but attendance shows {counted}",
                    {"line_lop_days": line_lop, "attendance_lop_days": counted})


def _basic_and_gross(row: dict[str, Any]) -> tuple[float, float] | None:
    raw = row.get("earnings")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    gross = finite(row.get("gross_pay"))
    if not isinstance(raw, list) or not gross or gross <= 0:
        return None
    for item in raw:
        if isinstance(item, dict) and str(item.get("component_name", "")).strip().lower() == "basic":
            basic = finite(item.get("amount"))
            return (basic, gross) if basic is not None else None
    return None


def basic_share(rows: list[dict[str, Any]]) -> dict[str, Any]:
    shares, unreadable = [], 0
    for row in rows:
        pair = _basic_and_gross(row)
        if pair is None:
            unreadable += 1
            continue
        shares.append({"employee_id": row.get("employee_id"), "employee_name": row.get("employee_name"),
                       "basic": pair[0], "gross": pair[1], "share_percent": round(pair[0] / pair[1] * 100, 2)})
    below = sorted((s for s in shares if s["share_percent"] < BASIC_SHARE_MIN), key=lambda s: s["share_percent"])
    return {"checked": len(shares), "unreadable": unreadable, "below_50_percent": len(below),
            "lowest_percent": below[0]["share_percent"] if below else None, "examples": below[:10],
            "note": "A flag for review against the wage-code definition of wages, not a legal conclusion."}


def assemble(*, run: dict[str, Any], calculated: bool, findings: list[dict[str, Any]], coverage: dict[str, Any],
             basic: dict[str, Any], skipped: list[dict[str, str]], candidates_skipped: int) -> dict[str, Any]:
    ordered = sorted(findings, key=lambda f: (_SEVERITY_ORDER[f["severity"]], f.get("employee_name") or "", f["check"]))
    numbered = [{"id": f"F-{index:03d}", **item} for index, item in enumerate(ordered, 1)]
    return {"payrun_id": run.get("id"), "run_status": run.get("status"), "calculated": calculated,
            "period_start": run.get("pay_period_start"), "period_end": run.get("pay_period_end"),
            "attendance": coverage, "basic_share": basic, "skipped": skipped,
            "lop_candidates_not_checked": candidates_skipped,
            "counts": {"by_severity": dict(Counter(f["severity"] for f in numbered)),
                       "by_check": dict(Counter(f["check"] for f in numbered)), "total": len(numbered)},
            "findings": numbered[:MAX_FINDINGS], "truncated": len(numbered) > MAX_FINDINGS}

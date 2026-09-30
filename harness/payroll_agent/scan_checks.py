"""Pure checks for the pre-payroll scan.

No network, no async: each check takes plain lists of rows shaped like
AgentSwitch ``PayRunEmployee`` / ``Employee`` records and returns findings.
``workers.run_pre_payroll_scan`` does the fetching; this module only judges.
Every number and id in a finding's ``evidence`` is copied from a fetched row.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

CHECKS = ("payee_status", "net_pay_change", "net_pay_sanity", "duplicate_payees")
DEFAULT_CHANGE_PCT = 30
HIGH_CHANGE_PCT = 50
MAX_FINDINGS = 40
_SEVERITY_ORDER = {"high": 0, "medium": 1, "info": 2}
_INACTIVE_STATUSES = {"left", "suspended"}


def _number(value: Any) -> float | None:
    """Amounts may arrive as numbers or numeric strings; anything else is unusable."""
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_calculated(rows: list[dict[str, Any]]) -> bool:
    """A run with no rows, or only zero/missing net pay, has not been calculated."""
    return any((_number(row.get("net_pay")) or 0) > 0 for row in rows)


def _finding(check: str, severity: str, row: dict[str, Any], evidence: dict[str, Any], message: str) -> dict[str, Any]:
    return {"check": check, "severity": severity, "employee_id": row.get("employee_id"),
            "employee_name": row.get("employee_name"), "payrun_id": row.get("payrun_id"),
            "evidence": evidence, "message": message}


def _date(value: Any) -> str:
    """The YYYY-MM-DD part; AgentSwitch dates sometimes carry a time suffix."""
    return str(value or "")[:10]


def payee_status(rows: list[dict[str, Any]], employees: dict[str, dict[str, Any]],
                 period_end: str | None) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    end = _date(period_end)
    for row in rows:
        person = employees.get(row.get("employee_id"))
        if person is None:
            found.append(_finding("payee_status", "high", row, {"reason": "not_in_employee_list"},
                                  "Payee is not in the employee list"))
            continue
        status, exit_date = person.get("status"), _date(person.get("exit_date"))
        if status in _INACTIVE_STATUSES:
            found.append(_finding("payee_status", "high", row,
                                  {"status": status, "exit_date": person.get("exit_date")},
                                  f"Paid while employee status is {status}"))
        # Reported separately from status: AgentSwitch data has exit dates on
        # employees whose status was never set to `left`, and that gap is itself useful.
        if exit_date and end and exit_date <= end:
            found.append(_finding("payee_status", "high", row,
                                  {"status": status, "exit_date": person.get("exit_date"), "period_end": period_end},
                                  "Paid although the exit date is on or before the period end"))
        if status == "on_leave":
            found.append(_finding("payee_status", "info", row, {"status": status},
                                  "Paid while on leave (may be legitimate)"))
    return found


def net_pay_change(rows: list[dict[str, Any]], prev_rows: list[dict[str, Any]],
                   threshold_pct: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    current_counts = Counter(row.get("employee_id") for row in rows)
    previous_counts = Counter(row.get("employee_id") for row in prev_rows)
    current = {row.get("employee_id"): row for row in rows}
    previous = {row.get("employee_id"): row for row in prev_rows}
    found: list[dict[str, Any]] = []
    new_in_run = not_comparable = 0
    for key in current_counts:
        if key not in previous_counts:
            new_in_run += 1
            continue
        # A duplicated employee has no single row to compare; duplicate_payees reports it.
        if current_counts[key] > 1 or previous_counts[key] > 1:
            not_comparable += 1
            continue
        raw_before, raw_after = previous[key].get("net_pay"), current[key].get("net_pay")
        before, after = _number(raw_before), _number(raw_after)
        if before is None or before <= 0 or after is None:
            not_comparable += 1
            continue
        change = (after - before) / before * 100
        if abs(change) >= HIGH_CHANGE_PCT:
            severity = "high"
        elif abs(change) >= threshold_pct:
            severity = "medium"
        else:
            continue
        found.append(_finding("net_pay_change", severity, current[key],
                              {"previous_net": raw_before, "current_net": raw_after, "change_pct": round(change, 1)},
                              f"Net pay {'up' if change > 0 else 'down'} {abs(round(change, 1))}% vs the comparison run"))
    return found, {"new_in_run": new_in_run, "not_comparable": not_comparable}


def net_pay_sanity(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_finding("net_pay_sanity", "high", row, {"net_pay": row.get("net_pay")},
                     "Net pay is zero, negative or missing")
            for row in rows if (amount := _number(row.get("net_pay"))) is None or amount <= 0]


def duplicate_payees(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[Any, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row.get("employee_id"), []).append(row)
    return [_finding("duplicate_payees", "high", group[0],
                     {"occurrences": len(group), "row_ids": [item.get("id") for item in group]},
                     f"Employee appears {len(group)} times in this run")
            for group in grouped.values() if len(group) > 1]


def run_checks(*, rows: list[dict[str, Any]], prev_rows: list[dict[str, Any]] | None,
               employees: list[dict[str, Any]], period_end: str | None,
               threshold_pct: int = DEFAULT_CHANGE_PCT,
               prev_reason: str = "no earlier calculated regular run") -> dict[str, Any]:
    """Run every check and assemble the capped, sorted, numbered result."""
    counts = {"by_check": {}, "by_severity": {}, "new_in_run": 0, "not_comparable": 0, "rows_checked": len(rows)}
    if not is_calculated(rows):
        return {"calculated": False, "counts": counts, "findings": [], "truncated": False,
                "skipped": [{"check": name, "reason": "run not calculated"} for name in CHECKS]}
    by_employee = {person["id"]: person for person in employees if "id" in person}
    skipped: list[dict[str, str]] = []
    per_check: dict[str, list[dict[str, Any]]] = {"payee_status": payee_status(rows, by_employee, period_end)}
    if prev_rows is None:
        skipped.append({"check": "net_pay_change", "reason": prev_reason})
    else:
        per_check["net_pay_change"], info = net_pay_change(rows, prev_rows, threshold_pct)
        counts["new_in_run"], counts["not_comparable"] = info["new_in_run"], info["not_comparable"]
    per_check["net_pay_sanity"] = net_pay_sanity(rows)
    per_check["duplicate_payees"] = duplicate_payees(rows)
    found = sorted((item for items in per_check.values() for item in items),
                   key=lambda f: (_SEVERITY_ORDER[f["severity"]], f.get("employee_name") or "", f["check"]))
    found = [{"id": f"F-{index:03d}", **item} for index, item in enumerate(found, 1)]
    counts["by_check"] = {name: len(items) for name, items in per_check.items()}
    counts["by_severity"] = dict(Counter(item["severity"] for item in found))
    return {"calculated": True, "skipped": skipped, "counts": counts,
            "findings": found[:MAX_FINDINGS], "truncated": len(found) > MAX_FINDINGS}

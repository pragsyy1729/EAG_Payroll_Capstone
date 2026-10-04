"""Pure statutory-dues calculations for one payroll run.

No network, no async: ``workers.run_statutory_dues`` fetches; this module computes. Every
amount comes from the rows passed in. The only values not from AgentSwitch are the labelled
standard India due-date rules and the public US statutory rates used for a sanity check.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from .cost_report import _finite as finite
from .scan_checks import is_calculated

MONEY_TOLERANCE = 1.0
MAX_PEOPLE = 25
INDIA_BASIS = "standard India calendar, not from AgentSwitch"
RATE_BASIS = "public statutory rate, not from AgentSwitch; observed as a percentage of gross pay"
DEPOSIT_NOTE = "not tracked in AgentSwitch; whether these dues were paid is unknown"
NOT_IN_DATA = "not in AgentSwitch data for this seat"

# (statute, side, slip field, header field, due-date key)
_INDIA_ROWS = (
    ("EPF", "employee", "epf_employee", "total_epf_employee", "epf"),
    ("EPF", "employer", "epf_employer", "total_epf_employer", "epf"),
    ("EPS", "employer", "eps_employer", "total_eps_employer", "epf"),
    ("EDLI", "employer", "edli_employer", "total_edli_employer", "epf"),
    ("EPF admin charges", "employer", "epf_admin_charges", "total_epf_admin_charges", "epf"),
    ("ESI", "employee", "esi_employee", "total_esi_employee", "esi"),
    ("ESI", "employer", "esi_employer", "total_esi_employer", "esi"),
    ("Professional tax", "employee", "professional_tax", "total_pt", None),
    ("TDS", "employee", "tds", "total_tds", "tds"),
    ("LWF", "employee", "lwf_employee", "total_lwf_employee", None),
    ("LWF", "employer", "lwf_employer", "total_lwf_employer", None),
)
_US_ORDER = ("Federal income tax", "State income tax", "Social Security", "Medicare")
_US_EMPLOYER = ("Social Security (employer match)", "Medicare (employer match)", "FUTA", "SUTA")
_ECHO = {
    "EPFConfig": ("employee_contribution_rate", "employer_contribution_rate", "eps_rate", "eps_wage_ceiling",
                  "edli_rate", "edli_wage_ceiling", "admin_charges_rate"),
    "ESIConfig": ("employee_contribution_rate", "employer_contribution_rate", "wage_ceiling"),
    "PTConfig": ("deduction_cycle", "annual_cap"),
    "LWFConfig": ("deduction_cycle", "employee_contribution", "employer_contribution", "state"),
}


def _amount(value: Any, bad: list[int]) -> float:
    """A usable amount, or 0.0; a present-but-unusable one is counted in ``bad[0]``."""
    amount = finite(value)
    if amount is None:
        bad[0] += value is not None
        return 0.0
    return amount


def _entry(due: date, rule: str, today: date) -> dict[str, Any]:
    days = (due - today).days
    return {"date": due.isoformat(), "rule": rule, "basis": INDIA_BASIS,
            "status": "past_due_date" if days < 0 else "due_today" if days == 0 else "upcoming", "days_left": days}


def due_dates(period_end: str | None, today: date) -> dict[str, dict[str, Any]]:
    """Standard India calendar for a period ending ``period_end`` (YYYY-MM-DD); {} if unusable."""
    try:
        end = date.fromisoformat(str(period_end)[:10])
        if not 2000 <= end.year <= 2100:               # the next month must also be a real date
            return {}
        year, month = (end.year + 1, 1) if end.month == 12 else (end.year, end.month + 1)
        if year > 2100:
            return {}
        tds = date(year, 4, 30) if end.month == 3 else date(year, month, 7)
        return {"tds": _entry(tds, "TDS: 7th of the following month (30 April for March)", today),
                "epf": _entry(date(year, month, 15), "EPF: 15th of the following month", today),
                "esi": _entry(date(year, month, 15), "ESI: 15th of the following month", today)}
    except ValueError:
        return {}


def _india(rows: list[dict[str, Any]], run: dict[str, Any], dues: dict[str, Any],
           bad: list[int]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    statutes, differences, compared = [], {}, 0
    for statute, side, slip_field, header_field, due_key in _INDIA_ROWS:
        amount = round(sum(_amount(row.get(slip_field), bad) for row in rows), 2)
        header = finite(run.get(header_field))
        if not amount and not header:
            continue
        if header is not None:
            compared += 1
            if abs(header - amount) > MONEY_TOLERANCE:
                differences[header_field] = {"header": header, "slips": amount}
        statutes.append({"statute": statute, "side": side, "amount": amount, "source": "slips",
                         "available": True, "header_total": header,
                         "due": dues.get(due_key) if due_key else None,
                         "note": None if due_key else "no standard due date asserted: state schedules vary"})
    consistency = {"matches": (not differences) if compared else None, "compared": compared,
                   "differences": differences}
    return statutes, consistency


_EMPLOYER_SIDE = ("employer", "match", "unemployment", "futa", "suta")


def _us_label(name: str) -> str | None:
    """An employee withholding, or None for anything else. Employer-side items (an employer match,
    FUTA/SUTA) and a Medicare surtax must never be summed into the employee rows."""
    text = name.lower()
    if any(word in text for word in _EMPLOYER_SIDE) or "surtax" in text:
        return None
    if "social security" in text:
        return "Social Security"
    if "medicare" in text:
        return "Medicare"
    if "federal" in text:
        return "Federal income tax"
    if "state" in text and any(word in text for word in ("withholding", "income", "tax")):
        return "State income tax"
    return None


def _us(rows: list[dict[str, Any]], run: dict[str, Any], bad: list[int]
        ) -> tuple[list[dict[str, Any]], dict[str, Any], float, dict[str, float], dict[str, float]]:
    sums: dict[str, float] = defaultdict(float)
    components: dict[str, float] = defaultdict(float)
    other = total = 0.0
    for row in rows:
        items = row.get("deductions")
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            amount = _amount(item.get("amount"), bad)
            total += amount
            label = _us_label(str(item.get("component_name", "")))
            if label:
                sums[label] += amount
            else:
                other += amount
                components[str(item.get("component_name", ""))] += amount
    statutes = [{"statute": label, "side": "employee", "amount": round(sums[label], 2),
                 "source": "slip deductions", "available": True, "header_total": None, "due": None, "note": None}
                for label in _US_ORDER if sums.get(label)]
    statutes += [{"statute": label, "side": "employer", "amount": None, "source": None, "available": False,
                  "header_total": None, "due": None, "note": NOT_IN_DATA} for label in _US_EMPLOYER]
    header = finite(run.get("total_deductions"))
    differences = {}
    if header is not None and abs(header - total) > MONEY_TOLERANCE:
        differences["total_deductions"] = {"header": header, "slips": round(total, 2)}
    consistency = {"matches": (not differences) if header is not None else None,
                   "compared": int(header is not None), "differences": differences}
    return statutes, consistency, round(other, 2), dict(sums), dict(components)


def _rate_checks(rows: list[dict[str, Any]], sums: dict[str, float], bad: list[int]) -> list[dict[str, Any]]:
    gross = sum(_amount(row.get("gross_pay"), bad) for row in rows)
    if not gross:
        return []
    checks = []
    for label, expected in (("Social Security", 6.2), ("Medicare", 1.45)):
        if label in sums:
            observed = round(sums[label] / gross * 100, 2)
            checks.append({"statute": label, "expected_pct": expected, "observed_pct": observed,
                           "within_tolerance": abs(observed - expected) <= 0.5, "basis": RATE_BASIS})
    return checks


def _esi_flag(rows: list[dict[str, Any]], configs: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    ceiling = finite((configs.get("ESIConfig") or [{}])[0].get("wage_ceiling"))
    if ceiling is None:
        return None
    hits = [row for row in rows if row.get("esi_covered") and (finite(row.get("gross_pay")) or 0.0) > ceiling]
    return {"count": len(hits), "ceiling": ceiling, "severity": "info",
            "employees": [{"employee_id": row.get("employee_id"), "employee_name": row.get("employee_name"),
                           "gross_pay": finite(row.get("gross_pay"))} for row in hits[:MAX_PEOPLE]]}


def _echo(configs: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    out = {}
    for name, fields in _ECHO.items():
        row = (configs.get(name) or [None])[0]
        if row:
            out[name] = {field: row.get(field) for field in fields}
    return out


def build_statutory(*, run: dict[str, Any], rows: list[dict[str, Any]], jurisdiction: str,
                    configs: dict[str, list[dict[str, Any]]], today: date) -> dict[str, Any]:
    head = {"payrun_id": run.get("id"), "run_status": run.get("status"), "jurisdiction": jurisdiction,
            "period": {"start": run.get("pay_period_start"), "end": run.get("pay_period_end")}}
    if not is_calculated(rows):
        return {**head, "calculated": False, "skipped": [{"what": "report", "reason": "run not calculated"}]}
    bad, skipped = [0], []
    if jurisdiction == "IN":
        dues = due_dates(run.get("pay_period_end"), today)
        if not dues:
            skipped.append({"what": "due dates", "reason": "run has no usable pay period end"})
        statutes, consistency = _india(rows, run, dues, bad)
        flag = _esi_flag(rows, configs)
        if flag is None:
            skipped.append({"what": "esi flag", "reason": "no ESI config with a wage ceiling"})
        config = _echo(configs)
        if not config:
            skipped.append({"what": "config", "reason": "no India config rows found"})
        extra: dict[str, Any] = {"flags": {"esi_covered_above_ceiling": flag} if flag else {}, "config": config}
    else:
        statutes, consistency, other, sums, components = _us(rows, run, bad)
        biggest = sorted(components.items(), key=lambda item: (-abs(item[1]), item[0]))[:10]
        extra = {"rate_checks": _rate_checks(rows, sums, bad), "non_statutory_deductions": other,
                 "non_statutory_components": {name: round(amount, 2) for name, amount in biggest}}
    if bad[0]:
        skipped.append({"what": "amounts", "reason": f"{bad[0]} unusable amount(s) counted as 0"})
    return {**head, "calculated": True, "statutes": statutes, "consistency": consistency, **extra,
            "deposit_tracking": DEPOSIT_NOTE, "skipped": skipped}

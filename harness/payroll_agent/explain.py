"""Employee "why" answers: the rules, kept pure so the model never does the arithmetic.

A pay line is broken down gross to net with the platform's own identities checked (a line that does
not add up is reported, not smoothed over). Two lines are compared by component and by the facts
that can move pay. A salary structure is shown with figures only when it belongs to the employee
or is a grade template. Reimbursement claims are grouped by status with data problems flagged.
The workers in ``workers.py`` do the fetching.
"""
from __future__ import annotations

import json
from typing import Any

from .cost_report import _finite as finite

TOLERANCE = 1.0
MAX_CLAIMS = 50
_NET_CHECK = "net_equals_gross_minus_deductions_plus_reimbursements"
_DRIVERS = ("days_payable", "working_days", "lop_days", "pf_wage", "tax_regime", "taxable_earnings", "tds",
            "loan_deduction", "other_deductions", "overtime_hours", "overtime_pay", "reimbursements", "esi_covered")


class ExplainError(ValueError):
    """The rows cannot support the requested explanation."""


def components(raw: Any) -> list[dict[str, Any]] | None:
    """[{"name", "amount"}] from a JSON string or a list. None when anything is unreadable; [] is valid."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, list):
        return None
    out = []
    for item in raw:
        amount = finite(item.get("amount")) if isinstance(item, dict) else None
        name = item.get("component_name") if isinstance(item, dict) else None
        if amount is None or not isinstance(name, str) or not name.strip():
            return None
        out.append({"name": name.strip(), "amount": round(amount, 2)})
    return out


def _total(items: list[dict[str, Any]]) -> float:
    return round(sum(item["amount"] for item in items), 2)


def usable_lines(rows: list[dict[str, Any]], runs: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(run, line) pairs for calculated lines of regular, non-cancelled runs, newest run first."""
    by_id = {run.get("id"): run for run in runs}
    out = []
    for row in rows:
        run = by_id.get(row.get("payrun_id"))
        if run is None or run.get("run_type") not in (None, "regular") or run.get("status") == "cancelled":
            continue
        gross, net = finite(row.get("gross_pay")), finite(row.get("net_pay"))
        if gross and gross > 0 and net and net > 0:
            out.append((run, row))
    out.sort(key=lambda pair: (str(pair[0].get("pay_period_start") or ""), str(pair[1].get("created_at") or "")),
             reverse=True)
    return out


def breakdown(row: dict[str, Any], run: dict[str, Any] | None = None) -> dict[str, Any]:
    run = run or {}
    earnings, deductions = components(row.get("earnings")), components(row.get("deductions"))
    gross, total, net = finite(row.get("gross_pay")), finite(row.get("total_deductions")), finite(row.get("net_pay"))
    reimbursements = finite(row.get("reimbursements")) or 0.0
    problems: list[str] = []
    if earnings is None:
        problems.append("the earnings components are unreadable")
    if deductions is None:
        problems.append("the deduction components are unreadable")
    if None in (gross, total, net):
        problems.append("gross pay, total deductions or net pay is missing or not a number")
    checks: dict[str, bool] = {}
    if earnings is not None and gross is not None:
        checks["earnings_add_up_to_gross"] = abs(_total(earnings) - gross) <= TOLERANCE
    if deductions is not None and total is not None:
        checks["deductions_add_up_to_total"] = abs(_total(deductions) - total) <= TOLERANCE
    expected = None
    if None not in (gross, total, net):
        expected = round(gross - total + reimbursements, 2)
        checks[_NET_CHECK] = abs(expected - net) <= TOLERANCE
    problems += [f"check failed: {name}" for name, ok in checks.items() if not ok]
    return {"employee_id": row.get("employee_id"), "employee_name": row.get("employee_name"),
            "payrun_id": row.get("payrun_id"), "payrun_number": run.get("number"),
            "pay_period": run.get("pay_period"), "run_status": run.get("status"),
            "earnings": earnings, "gross": gross, "deductions": deductions, "total_deductions": total,
            "reimbursements": reimbursements, "net": net, "expected_net": expected, "checks": checks,
            "problems": problems, "consistent": not problems,
            "days_payable": finite(row.get("days_payable")), "working_days": finite(row.get("working_days")),
            "lop_days": finite(row.get("lop_days")), "tax_regime": row.get("tax_regime")}


def _by_name(items: list[dict[str, Any]]) -> dict[str, float]:
    return {item["name"]: item["amount"] for item in items}


def _deltas(before: dict[str, float], after: dict[str, float]) -> list[dict[str, Any]]:
    out = []
    for name in sorted(set(before) | set(after)):
        b, a = before.get(name), after.get(name)
        change = round((a or 0.0) - (b or 0.0), 2)
        if change != 0 or b is None or a is None:
            out.append({"component": name, "before": b, "after": a, "change": change})
    return out


def _same(a: Any, b: Any) -> bool:
    fa, fb = finite(a), finite(b)
    if fa is not None and fb is not None:
        return abs(fa - fb) < 0.005
    return a == b


def _side(run: dict[str, Any], row: dict[str, Any], gross_net: tuple[float, float]) -> dict[str, Any]:
    return {"payrun_id": row.get("payrun_id"), "payrun_number": run.get("number"), "pay_period": run.get("pay_period"),
            "gross": gross_net[0], "net": gross_net[1]}


def change(*, to_run: dict[str, Any], to_row: dict[str, Any], from_run: dict[str, Any],
           from_row: dict[str, Any]) -> dict[str, Any]:
    """What differs between two of one employee's pay lines. Facts and differences only: a cause the
    data does not show is not offered."""
    if to_row.get("employee_id") != from_row.get("employee_id"):
        raise ExplainError("the two pay lines belong to different employees")
    parts = {}
    for label, row in (("from", from_row), ("to", to_row)):
        earn, ded = components(row.get("earnings")), components(row.get("deductions"))
        gross, total, net = finite(row.get("gross_pay")), finite(row.get("total_deductions")), finite(row.get("net_pay"))
        if earn is None or ded is None or None in (gross, total, net):
            raise ExplainError(f"the {label} pay line has unreadable components or totals")
        parts[label] = (earn, ded, gross, total, net)
    earnings = _deltas(_by_name(parts["from"][0]), _by_name(parts["to"][0]))
    deductions = _deltas(_by_name(parts["from"][1]), _by_name(parts["to"][1]))
    drivers = {name: {"before": from_row.get(name), "after": to_row.get(name)} for name in _DRIVERS
               if name in from_row or name in to_row}
    movers = sorted([{**d, "kind": "earning"} for d in earnings] + [{**d, "kind": "deduction"} for d in deductions],
                    key=lambda d: abs(d["change"]), reverse=True)[:3]
    return {"from": _side(from_run, from_row, (parts["from"][2], parts["from"][4])),
            "to": _side(to_run, to_row, (parts["to"][2], parts["to"][4])),
            "gross_change": round(parts["to"][2] - parts["from"][2], 2),
            "total_deductions_change": round(parts["to"][3] - parts["from"][3], 2),
            "net_change": round(parts["to"][4] - parts["from"][4], 2),
            "earnings": earnings, "deductions": deductions, "drivers": drivers,
            "drivers_changed": [name for name, d in drivers.items() if not _same(d["before"], d["after"])],
            "largest_movers": movers,
            "note": "Facts and differences only; a cause not shown by these fields is not in the data."}


def _structure_amount(item: dict[str, Any]) -> float | None:
    value = finite(item.get("monthly_amount"))
    return value if value is not None else finite(item.get("amount"))


def _revision_summary(revisions: list[dict[str, Any]], profile_ctc: float | None) -> dict[str, Any]:
    items = []
    for revision in revisions:
        structure = revision.get("revised_structure")
        named = isinstance(structure, list) and bool(structure) and all(
            isinstance(c, dict) and isinstance(c.get("component_name"), str) and c["component_name"].strip()
            for c in structure)
        current = finite(revision.get("current_ctc"))
        matches = bool(current and profile_ctc and abs(current - profile_ctc) <= 0.01 * profile_ctc)
        items.append({"number": revision.get("number"), "status": revision.get("status"),
                      "approval_status": revision.get("approval_status"), "current_ctc": current,
                      "revised_ctc": finite(revision.get("revised_ctc")), "has_component_names": named,
                      "matches_profile_ctc": matches})
    return {"count": len(items), "usable": sum(1 for i in items if i["has_component_names"] and i["matches_profile_ctc"]),
            "items": items}


def structure_view(*, employee: dict[str, Any], structure: dict[str, Any] | None, line_count: int,
                   revisions: list[dict[str, Any]]) -> dict[str, Any]:
    profile_annual, profile_monthly = finite(employee.get("ctc_annual")), finite(employee.get("ctc_monthly"))
    problems: list[str] = []
    view: dict[str, Any] = {
        "employee_id": employee.get("id"), "status": employee.get("status"),
        "employee_name": " ".join(str(employee.get(k)) for k in ("first_name", "last_name") if employee.get(k)),
        "profile_ctc_annual": profile_annual, "profile_ctc_monthly": profile_monthly,
        "salary_structure_id": employee.get("salary_structure_id"), "pay_lines": line_count}
    owner = structure.get("employee_id") if structure else None
    if structure is None:
        view["ownership"] = "missing"
        problems.append("the profile points to no salary structure, or the structure record was not found")
    elif owner and owner != employee.get("id"):
        view.update(ownership="other_employee", owner_employee_id=owner, structure_name=structure.get("name"),
                    figures_withheld=True)
        problems.append("the structure on the profile belongs to another employee, so its figures are not "
                        "this employee's")
    else:
        view["ownership"] = "own" if owner else "template"
        structure_monthly = finite(structure.get("ctc_monthly"))
        earnings = [{"name": c.get("component_name"), "monthly": _structure_amount(c)}
                    for c in structure.get("earnings") or [] if isinstance(c, dict)]
        for item in earnings:
            item["share_of_ctc_percent"] = (round(item["monthly"] / structure_monthly * 100, 2)
                                            if item["monthly"] is not None and structure_monthly else None)
        total = round(sum(i["monthly"] or 0.0 for i in earnings), 2)
        view.update(structure_name=structure.get("name"), structure_ctc_annual=finite(structure.get("ctc_annual")),
                    structure_ctc_monthly=structure_monthly, earnings=earnings, earnings_total=total,
                    not_itemised=round(structure_monthly - total, 2) if structure_monthly is not None else None,
                    deductions=[{"name": c.get("component_name"), "monthly": _structure_amount(c)}
                                for c in structure.get("deductions") or [] if isinstance(c, dict)])
        if owner and profile_annual and view["structure_ctc_annual"] is not None and abs(
                view["structure_ctc_annual"] - profile_annual) > 0.01 * profile_annual:
            problems.append("the profile CTC differs from the structure's CTC")
    if line_count == 0:
        problems.append("there are no pay lines for this employee, so no actual pay can be shown")
    view["revisions"] = _revision_summary(revisions, profile_annual)
    view["problems"] = problems
    return view


def reimbursement_report(claims: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, dict[str, Any]] = {}
    rows, flagged = [], []
    for item in claims:
        status = item.get("status") or "unknown"
        amount, approved = finite(item.get("amount")), finite(item.get("approved_amount"))
        group = by_status.setdefault(status, {"count": 0, "claimed": 0.0, "approved": 0.0})
        group["count"] += 1
        group["claimed"] = round(group["claimed"] + (amount or 0.0), 2)
        group["approved"] = round(group["approved"] + (approved or 0.0), 2)
        problems = []
        if amount is None:
            problems.append("the claim amount is missing or not a number")
        if status == "partially_approved" and approved is None:
            problems.append("partially approved but there is no approved amount")
        if status == "partially_approved" and approved is not None and amount is not None and approved >= amount:
            problems.append("partially approved but the approved amount is not below the claim")
        if approved is not None and amount is not None and approved > amount + TOLERANCE:
            problems.append("the approved amount is above the claim")
        if status == "paid" and not item.get("paid_in_payrun"):
            problems.append("paid but no pay run is recorded")
        rows.append({"number": item.get("number"), "employee_id": item.get("employee_id"),
                     "claim_month": item.get("claim_month"), "component": item.get("component_name"),
                     "status": status, "amount": amount, "approved_amount": approved,
                     "paid_in_payrun": item.get("paid_in_payrun"), "problems": problems})
        if problems:
            flagged.append({"number": item.get("number"), "problems": problems})
    return {"total": len(claims), "by_status": by_status, "claims": rows[:MAX_CLAIMS],
            "truncated": len(rows) > MAX_CLAIMS, "flagged": flagged[:MAX_CLAIMS], "flagged_count": len(flagged),
            "note": "The reason a claim was rejected or only partly approved is not recorded in the data this seat can read."}

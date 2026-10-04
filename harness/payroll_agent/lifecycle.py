"""Pure lifecycle reports: loans, salary revisions, final settlements, investments.

No network, no async: ``workers.run_lifecycle_report`` fetches; this module computes. The data
on the shared tenants can be noisy (negative loan amounts, dates in year 0009), so every value is
checked before use: a bad one is flagged or counted, never trusted and never a crash.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any

from .cost_report import _finite as finite

MAX_ITEMS = 25
MAX_EXAMPLES = 10
MAX_MONTHS = 12
_SETTLEMENT_FIELDS = ("bonus_payable", "gratuity_payable", "leave_encashment", "epf_settlement",
                      "notice_pay_recovery", "gross_settlement", "net_settlement")


def _amount(value: Any, bad: list[int]) -> float | None:
    """A usable amount, or None; a present-but-unusable one is counted in ``bad[0]``."""
    amount = finite(value)
    if amount is None:
        bad[0] += value is not None
    return amount


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _name(employee: dict[str, Any]) -> str | None:
    full = f"{employee.get('first_name') or ''} {employee.get('last_name') or ''}".strip()
    return full or employee.get("_party_id_display") or employee.get("_display")


def employee_names(employees: list[dict[str, Any]]) -> dict[Any, str | None]:
    return {employee["id"]: _name(employee) for employee in employees if "id" in employee}


def _who(row: dict[str, Any], names: dict[Any, Any]) -> dict[str, Any]:
    return {"employee_id": row.get("employee_id"), "employee_name": names.get(row.get("employee_id"))}


def _count(rows: list[dict[str, Any]], field: str = "status") -> dict[Any, int]:
    return dict(Counter(row.get(field) for row in rows))


def _only(rows: list[dict[str, Any]], employee_id: str | None) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("employee_id") == employee_id] if employee_id else rows


def _loan_issues(loan: dict[str, Any], bad: list[int]) -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    amount, emi, tenure, rate = (_amount(loan.get(key), bad)
                                 for key in ("loan_amount", "emi_amount", "tenure_months", "interest_rate"))
    if amount is not None and amount <= 0:
        found.append(("loan_amount_not_positive", amount))
    if emi is not None and emi <= 0:
        found.append(("emi_not_positive", emi))
    if tenure is not None and tenure <= 0:
        found.append(("tenure_not_positive", tenure))
    if rate is not None and rate < 0:
        found.append(("negative_interest_rate", rate))
    raw = loan.get("disbursement_date")
    if raw:
        disbursed = _date(raw)
        if disbursed is None or not 2000 <= disbursed.year <= 2100:
            found.append(("implausible_disbursement_date", raw))
    return found


def loans_report(loans: list[dict[str, Any]], repayments: list[dict[str, Any]], names: dict[Any, Any],
                 today: date, employee_id: str | None = None) -> dict[str, Any]:
    loans, repayments = _only(loans, employee_id), _only(repayments, employee_id)
    bad = [0]
    issues: dict[str, list[dict[str, Any]]] = defaultdict(list)
    flawed: set[Any] = set()
    valid_amount = valid_repaid = 0.0
    counted = excluded = 0
    for loan in loans:
        ref = {"number": loan.get("number"), "employee_id": loan.get("employee_id")}
        found = _loan_issues(loan, bad)
        for check, value in found:
            issues[check].append({**ref, "value": value})
        if found:
            flawed.add(loan.get("number") or loan.get("id"))
        amount = finite(loan.get("loan_amount"))
        if amount is not None and amount > 0:
            counted += 1
            valid_amount += amount
            valid_repaid += _amount(loan.get("total_repaid"), bad) or 0.0
        else:
            excluded += 1
    by_month: dict[str, dict[str, float]] = defaultdict(lambda: {"count": 0, "total": 0.0})
    past = {"count": 0, "total": 0.0}
    for repayment in repayments:
        ref = {"loan_id": repayment.get("loan_id"), "employee_id": repayment.get("employee_id"),
               "date": repayment.get("date")}
        principal, total = _amount(repayment.get("principal_amount"), bad), _amount(repayment.get("total_amount"), bad)
        if principal is not None and principal < 0:
            issues["repayment_principal_negative"].append({**ref, "value": principal})
        if total is not None and total <= 0:
            issues["repayment_total_not_positive"].append({**ref, "value": total})
        if repayment.get("status") == "scheduled":
            month = by_month[str(repayment.get("date"))[:7]]
            month["count"] += 1
            month["total"] += total or 0.0
            when = _date(repayment.get("date"))
            if when is not None and when < today:
                past["count"] += 1
                past["total"] += total or 0.0
    months = [{"month": key, "count": value["count"], "total": round(value["total"], 2)}
              for key, value in sorted(by_month.items())]
    items = [{**_who(loan, names), "number": loan.get("number"), "status": loan.get("status"),
              "loan_name": loan.get("loan_name"), "loan_amount": finite(loan.get("loan_amount")),
              "emi_amount": finite(loan.get("emi_amount")), "total_repaid": finite(loan.get("total_repaid")),
              "tenure_months": finite(loan.get("tenure_months"))} for loan in loans[:MAX_ITEMS]] if employee_id else []
    return {"topic": "loans", "total": len(loans), "by_status": _count(loans),
            "totals": {"loan_amount": round(valid_amount, 2), "total_repaid": round(valid_repaid, 2),
                       "loans_counted": counted, "excluded_invalid": excluded},
            "repayments": {"total": len(repayments), "by_status": _count(repayments),
                           "scheduled_by_month": months[:MAX_MONTHS], "scheduled_months_truncated": len(months) > MAX_MONTHS,
                           "scheduled_in_the_past": {"count": past["count"], "total": round(past["total"], 2)}},
            "data_quality": {"records_with_issues": len(flawed),
                             "checks": [{"check": check, "count": len(found), "examples": found[:MAX_EXAMPLES]}
                                        for check, found in sorted(issues.items())]},
            "items": items, "unusable_amounts": bad[0]}


def revisions_report(revisions: list[dict[str, Any]], names: dict[Any, Any], today: date,
                     employee_id: str | None = None) -> dict[str, Any]:
    revisions = _only(revisions, employee_id)
    bad = [0]

    def row(revision: dict[str, Any]) -> dict[str, Any]:
        return {**_who(revision, names), "number": revision.get("number"),
                "effective_date": revision.get("effective_date"),
                "revised_ctc": _amount(revision.get("revised_ctc"), bad),
                "approval_status": revision.get("approval_status")}

    pending = sorted((r for r in revisions if r.get("status") == "pending_approval"),
                     key=lambda r: (str(r.get("effective_date")), str(r.get("number"))))
    backdated = [r for r in pending if (d := _date(r.get("effective_date"))) is not None and d < today]
    soon = [r for r in revisions if r.get("status") in ("pending_approval", "approved")
            and (d := _date(r.get("effective_date"))) is not None and today <= d <= today + timedelta(days=30)]
    return {"topic": "revisions", "total": len(revisions), "by_status": _count(revisions),
            "pending_count": len(pending), "pending": [row(r) for r in pending[:MAX_ITEMS]],
            "backdated_pending": {"count": len(backdated),
                                  "note": "effective date already passed: arrears likely once approved",
                                  "examples": [row(r) for r in backdated[:MAX_EXAMPLES]]},
            "upcoming_30_days": len(soon),
            "applied_count": sum(1 for r in revisions if r.get("status") == "applied"),
            "unusable_amounts": bad[0]}


def settlements_report(settlements: list[dict[str, Any]], names: dict[Any, Any],
                       employee_id: str | None = None) -> dict[str, Any]:
    settlements = _only(settlements, employee_id)
    bad = [0]
    items = [{**_who(s, names), "status": s.get("status"), "approval_status": s.get("approval_status"),
              "last_working_date": s.get("last_working_date"),
              **{field: _amount(s.get(field), bad) for field in _SETTLEMENT_FIELDS}}
             for s in settlements[:MAX_ITEMS]]
    net = sum(amount for s in settlements if (amount := finite(s.get("net_settlement"))) is not None and amount > 0)
    return {"topic": "settlements", "total": len(settlements), "by_status": _count(settlements),
            "total_net_settlement": round(net, 2), "items": items, "unusable_amounts": bad[0]}


def investments_report(declarations: list[dict[str, Any]], proofs: list[dict[str, Any]], names: dict[Any, Any],
                       employee_id: str | None = None) -> dict[str, Any]:
    declarations, proofs = _only(declarations, employee_id), _only(proofs, employee_id)
    bad = [0]
    sections: dict[str, float] = defaultdict(float)
    for declaration in declarations:
        if declaration.get("status") != "approved":
            continue
        listed = declaration.get("investments")
        for item in listed if isinstance(listed, list) else []:
            if isinstance(item, dict):
                sections[str(item.get("section"))] += _amount(item.get("declared_amount"), bad) or 0.0
    proven = {proof.get("declaration_id") for proof in proofs}
    unproven = [d for d in declarations if d.get("status") not in ("draft", "rejected") and d.get("id") not in proven]
    return {"topic": "investments", "total": len(declarations), "by_status": _count(declarations),
            "by_fiscal_year": _count(declarations, "fiscal_year"),
            "approved_declared_by_section": {key: round(value, 2) for key, value in sections.items()},
            "without_proof": {"count": len(unproven),
                              "employees": [{**_who(d, names), "status": d.get("status"),
                                             "fiscal_year": d.get("fiscal_year")} for d in unproven[:MAX_ITEMS]]},
            "proofs": {"total": len(proofs), "by_approval_status": _count(proofs, "approval_status")},
            "unusable_amounts": bad[0]}


def build_lifecycle(topic: str, *, data: dict[str, list[dict[str, Any]]], names: dict[Any, Any], today: date,
                    employee_id: str | None = None) -> dict[str, Any]:
    if topic == "loans":
        report = loans_report(data["EmployeeLoan"], data["LoanRepayment"], names, today, employee_id)
    elif topic == "revisions":
        report = revisions_report(data["SalaryRevision"], names, today, employee_id)
    elif topic == "settlements":
        report = settlements_report(data["FinalSettlement"], names, employee_id)
    elif topic == "investments":
        report = investments_report(data["InvestmentDeclaration"], data["ProofOfInvestment"], names, employee_id)
    else:
        raise ValueError(f"unknown lifecycle topic {topic!r}")
    unusable = report.pop("unusable_amounts")
    report["skipped"] = [{"what": "amounts", "reason": f"{unusable} unusable amount(s) counted as 0"}] if unusable else []
    return report

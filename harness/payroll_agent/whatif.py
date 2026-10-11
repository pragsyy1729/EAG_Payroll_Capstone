"""Salary what-if estimator: the arithmetic, kept pure so the model never does it.

Takes an employee's latest full-month pay line, applies a hypothetical change in code and
recomputes the statutory lines from the tenant's own EPF, ESI and professional-tax configuration.
It reads nothing and writes nothing; the worker in ``workers.py`` supplies the rows. Every result is
labelled an estimate. TDS is scaled in proportion to gross because the platform holds no tax slabs.
"""
from __future__ import annotations

import json
from typing import Any

from .cost_report import _finite as finite

MONEY_TOLERANCE = 1.0
ESTIMATE_NOTE = "Estimate only. Nothing was written to AgentSwitch."
_CARRIED = ("loan_deduction", "other_deductions", "lwf_employee")
_COMPARED = ("epf_employee", "esi_employee", "esi_employer", "professional_tax", "employer_contribution")


class WhatIfError(ValueError):
    """The request or the data cannot support an estimate."""


def parse_change(percent_raise: Any = None, new_monthly_gross: Any = None) -> dict[str, float]:
    given = {name: raw for name, raw in (("percent_raise", percent_raise),
                                         ("new_monthly_gross", new_monthly_gross)) if raw is not None}
    if len(given) != 1:
        raise WhatIfError("give exactly one of percent_raise or new_monthly_gross")
    (name, raw), = given.items()
    value = finite(raw)
    if value is None:
        raise WhatIfError(f"{name} must be a finite number")
    if name == "percent_raise" and value <= -100:
        raise WhatIfError("percent_raise must be greater than -100")
    if name == "new_monthly_gross" and value <= 0:
        raise WhatIfError("new_monthly_gross must be positive")
    return {name: value}


def _json_list(raw: Any) -> list[Any] | None:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    return raw if isinstance(raw, list) else None


def _earnings(row: dict[str, Any]) -> list[dict[str, Any]]:
    items = _json_list(row.get("earnings"))
    if not items:
        raise WhatIfError("the pay line has no readable earnings list")
    out = []
    for item in items:
        amount = finite(item.get("amount")) if isinstance(item, dict) else None
        name = item.get("component_name") if isinstance(item, dict) else None
        if amount is None or not isinstance(name, str):
            raise WhatIfError("the pay line has an unreadable earnings component")
        out.append({"component_name": name, "amount": amount})
    return out


def pick_basis_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The latest full-month calculated pay line, or None. Part-month and uncalculated lines are skipped."""
    usable = []
    for row in rows:
        gross, net = finite(row.get("gross_pay")), finite(row.get("net_pay"))
        days, working = finite(row.get("days_payable")), finite(row.get("working_days"))
        if gross and gross > 0 and net and net > 0 and working and working > 0 and days == working:
            usable.append(row)
    return max(usable, key=lambda row: str(row.get("created_at") or ""), default=None)


def _config(configs: dict[str, list[dict[str, Any]]], name: str) -> dict[str, Any] | None:
    rows = configs.get(name) or []
    active = [row for row in rows if row.get("is_active")]
    return (active or rows or [None])[0]


def _round(value: float) -> float:
    return round(value + 0.0, 2)


def _side(gross: float, basic: float, tds: float, carried: dict[str, float],
          configs: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """One side (before or after) of the comparison. A line that cannot be computed is None."""
    side: dict[str, Any] = {"gross": _round(gross), "basic": _round(basic), "tds": _round(tds), **carried,
                            "epf_employee": None, "employer_contribution": None, "esi_employee": None,
                            "esi_employer": None, "professional_tax": None}
    missing: list[str] = []

    epf = _config(configs, "EPFConfig")
    epf_vals = [finite(epf.get(key)) if epf else None for key in (
        "employee_contribution_rate", "employer_contribution_rate", "pf_wage_ceiling", "edli_rate",
        "edli_wage_ceiling", "admin_charges_rate")]
    if None in epf_vals:
        missing.append("EPF")
    else:
        rate_e, rate_r, ceiling, edli_rate, edli_ceiling, admin_rate = epf_vals
        pf_wage = min(basic, ceiling) if epf.get("restrict_pf_to_ceiling") else basic
        side["epf_employee"] = _round(pf_wage * rate_e / 100)
        side["employer_contribution"] = _round(pf_wage * rate_r / 100 + min(basic, edli_ceiling) * edli_rate / 100
                                               + pf_wage * admin_rate / 100)

    esi = _config(configs, "ESIConfig")
    esi_vals = [finite(esi.get(key)) if esi else None for key in (
        "wage_ceiling", "employee_contribution_rate", "employer_contribution_rate")]
    if None in esi_vals:
        missing.append("ESI")
    else:
        ceiling, rate_e, rate_r = esi_vals
        covered = gross <= ceiling
        side["esi_employee"] = _round(gross * rate_e / 100) if covered else 0.0
        side["esi_employer"] = _round(gross * rate_r / 100) if covered else 0.0

    pt = _config(configs, "PTConfig")
    slabs = _json_list(pt.get("slabs")) if pt else None
    tiers = [(finite(s.get("from_amount")), finite(s.get("tax_amount"))) for s in slabs or [] if isinstance(s, dict)]
    if not tiers or any(a is None or b is None for a, b in tiers):
        missing.append("PT")
    else:
        eligible = [tier for tier in tiers if tier[0] <= gross]
        side["professional_tax"] = _round(max(eligible)[1]) if eligible else 0.0

    side["not_computed"] = missing
    if missing:
        side["net"], side["employer_cost"] = None, None
    else:
        deductions = (side["epf_employee"] + side["esi_employee"] + side["professional_tax"] + side["tds"]
                      + sum(carried.values()))
        side["net"] = _round(gross - deductions)
        side["employer_cost"] = _round(gross + side["employer_contribution"] + side["esi_employer"])
    return side


def _differences(side: dict[str, Any], row: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for field in _COMPARED:
        calculated, actual = side.get(field), finite(row.get(field))
        if calculated is not None and actual is not None and abs(calculated - actual) > MONEY_TOLERANCE:
            out.append({"field": field, "calculated": calculated, "actual": actual})
    return out


def _inputs(row: dict[str, Any]) -> tuple[list[dict[str, Any]], float, float, float, dict[str, float]]:
    components = _earnings(row)
    gross = _round(sum(c["amount"] for c in components))
    stated = finite(row.get("gross_pay"))
    if stated is None or abs(stated - gross) > MONEY_TOLERANCE:
        raise WhatIfError("the earnings components do not add up to the pay line's gross pay")
    basic = next((c["amount"] for c in components if c["component_name"].strip().lower() == "basic"),
                 finite(row.get("basic_pay")))
    if basic is None:
        raise WhatIfError("the pay line has no Basic component")
    carried = {name: finite(row.get(name)) or 0.0 for name in _CARRIED}
    return components, gross, basic, finite(row.get("tds")) or 0.0, carried


def baseline_differences(rows: list[dict[str, Any]], configs: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Where the model cannot reproduce a pay line's own statutory figures (full-month lines only)."""
    out = []
    for row in rows:
        if pick_basis_row([row]) is None:
            continue
        try:
            _, gross, basic, tds, carried = _inputs(row)
        except WhatIfError:
            continue
        for item in _differences(_side(gross, basic, tds, carried, configs), row):
            out.append({"employee_id": row.get("employee_id"), "employee_name": row.get("employee_name"),
                        "pay_line_id": row.get("id"), **item})
    return out


def estimate(*, row: dict[str, Any], change: dict[str, float], configs: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    components, gross0, basic0, tds0, carried = _inputs(row)
    if "percent_raise" in change:
        gross1 = gross0 * (1 + change["percent_raise"] / 100)
    else:
        gross1 = change["new_monthly_gross"]
    factor = gross1 / gross0
    before = _side(gross0, basic0, tds0, carried, configs)
    after = _side(gross1, basic0 * factor, tds0 * factor, carried, configs)
    delta = {key: _round(after[key] - before[key])
             for key in ("gross", "net", "employer_cost") if after[key] is not None and before[key] is not None}
    assumptions = [
        f"Basis is the employee's latest full-month pay line; every earning is scaled by the same factor ({factor:.4f}).",
        "TDS is scaled in proportion to gross; the platform holds no tax slabs, so treat it as a rough estimate.",
        "Loan, other deductions and LWF are carried over unchanged.",
        "ESI applies only while gross is at or below the ESI wage ceiling.",
        "Professional tax uses the monthly slab for the gross; the annual cap is not applied.",
    ]
    if tds0 == 0 and factor > 1:
        assumptions.append("TDS is currently 0 and is kept at 0; a raise could start TDS.")
    return {"estimate": True, "written": False, "note": ESTIMATE_NOTE,
            "employee_id": row.get("employee_id"), "employee_name": row.get("employee_name"),
            "basis": {"pay_line_id": row.get("id"), "payrun_id": row.get("payrun_id"),
                      "created_at": row.get("created_at")},
            "change": change, "before": before, "after": after, "delta": delta,
            "after_earnings": [{**c, "amount": _round(c["amount"] * factor)} for c in components],
            "assumptions": assumptions, "differs_from_payslip": _differences(before, row)}

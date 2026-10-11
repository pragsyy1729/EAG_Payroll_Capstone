"""Pure payroll cost and variance calculations.

No network, no async: every function takes plain lists of rows shaped like AgentSwitch
``PayRunEmployee`` and ``Employee`` records. ``workers.run_payroll_cost_report`` does the
fetching; this module only computes. Every figure comes from the rows passed in.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from typing import Any

from .scan_checks import _number as number
from .scan_checks import is_calculated

MAX_GROUPS = 30
MAX_PEOPLE = 25
MAX_REGISTER = 200
MONEY_TOLERANCE = 1.0
UNKNOWN_GROUP = "(unknown)"
_GROUP_FIELD = {"department": "_department_id_display", "location": "_work_location_id_display"}
_SUMMED = ("gross_pay", "net_pay", "total_deductions", "employer_contribution", "overtime_pay")
_HEADER = (("total_gross_pay", "gross_pay"), ("total_net_pay", "net_pay"),
           ("total_deductions", "total_deductions"),
           ("total_employer_contribution", "employer_contribution"), ("employee_count", "headcount"))


def _finite(value: Any) -> float | None:
    """A usable amount: numeric, and not nan or inf (which would slip past every comparison)."""
    amount = number(value)
    return amount if amount is not None and math.isfinite(amount) else None


def _overtime(row: dict[str, Any]) -> tuple[float | None, int]:
    """(overtime paid on a slip, count of present-but-unusable amounts).

    AgentSwitch's `overtime_pay` field can be 0 while the pay is in the `earnings` list as an
    "Overtime" component (confirmed on the India tenant), so any such component decides the
    answer, including when its amount is unusable (counted, not replaced by the field). The
    field is the fallback only when the slip has no overtime component."""
    earnings = row.get("earnings")
    found, total, unusable = False, 0.0, 0
    for item in earnings if isinstance(earnings, list) else []:
        if isinstance(item, dict) and "overtime" in str(item.get("component_name", "")).lower():
            found = True
            amount = _finite(item.get("amount"))
            if amount is None:
                unusable += item.get("amount") is not None
            else:
                total += amount
    if found:
        return total, unusable
    amount = _finite(row.get("overtime_pay"))
    return amount, int(amount is None and row.get("overtime_pay") is not None)


def _blank() -> dict[str, float]:
    return {"headcount": 0, **{name: 0.0 for name in _SUMMED}}


def _group_of(row: dict[str, Any], people: dict[Any, dict[str, Any]], group_by: str) -> str | None:
    if group_by == "none":
        return None
    person = people.get(row.get("employee_id"))
    return (person or {}).get(_GROUP_FIELD[group_by]) or UNKNOWN_GROUP


def _aggregate(rows: list[dict[str, Any]], employees: list[dict[str, Any]],
               group_by: str) -> tuple[dict[str, float], dict[str, dict[str, float]], int]:
    """(totals, groups by name, count of present-but-unusable amounts)."""
    people = {person["id"]: person for person in employees if "id" in person}
    totals, groups, unusable = _blank(), defaultdict(_blank), 0
    for row in rows:
        group = _group_of(row, people, group_by)
        buckets = [totals] if group is None else [totals, groups[group]]
        for bucket in buckets:
            bucket["headcount"] += 1
        for name in _SUMMED:
            if name == "overtime_pay":
                amount, bad = _overtime(row)
                unusable += bad
            else:
                amount = _finite(row.get(name))
                unusable += amount is None and row.get(name) is not None   # missing is simply 0; bad is reported
            amount = amount or 0.0
            for bucket in buckets:
                bucket[name] += amount
    return totals, dict(groups), unusable


def _finish(bucket: dict[str, float]) -> dict[str, float]:
    done = {key: round(value, 2) for key, value in bucket.items()}
    done["headcount"] = int(bucket["headcount"])
    done["total_cost"] = round(bucket["gross_pay"] + bucket["employer_contribution"], 2)
    return done


def summarize(rows: list[dict[str, Any]], employees: list[dict[str, Any]], group_by: str) -> dict[str, Any]:
    totals, groups, unusable = _aggregate(rows, employees, group_by)
    totals = _finish(totals)
    ranked = sorted(((name, _finish(bucket)) for name, bucket in groups.items()),
                    key=lambda item: (-item[1]["total_cost"], item[0]))
    listing = []
    for name, bucket in ranked[:MAX_GROUPS]:
        share = round(bucket["total_cost"] / totals["total_cost"] * 100, 1) if totals["total_cost"] else None
        listing.append({"group": name, **bucket, "share_of_cost_pct": share})
    return {"totals": totals, "groups": listing, "groups_truncated": len(ranked) > MAX_GROUPS,
            "unusable_amounts": unusable}


def check_header(run: dict[str, Any], totals: dict[str, float]) -> dict[str, Any]:
    """Compare the PayRun header with the slip sums; only mismatches are listed."""
    differences: dict[str, Any] = {}
    compared = 0
    for header_key, total_key in _HEADER:
        header = _finite(run.get(header_key))
        if header is None:
            continue
        compared += 1
        tolerance = 0.5 if total_key == "headcount" else MONEY_TOLERANCE
        if abs(header - totals[total_key]) > tolerance:
            differences[header_key] = {"header": header, "slips": totals[total_key]}
    # With nothing to compare, "matches" would be false comfort: say so with None.
    return {"header_matches_slips": (not differences) if compared else None, "compared": compared,
            "differences": differences}


def _pct(previous: float, current: float) -> float | None:
    return round((current - previous) / previous * 100, 1) if previous else None


def _delta(previous: float, current: float) -> dict[str, Any]:
    return {"previous": previous, "current": current, "change": round(current - previous, 2),
            "change_pct": _pct(previous, current)}


def _people_missing_from(rows: list[dict[str, Any]], other_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Distinct employees in ``rows`` who are not in ``other_rows``."""
    names = {row.get("employee_id"): row.get("employee_name") for row in rows if row.get("employee_id")}
    others = {row.get("employee_id") for row in other_rows}
    missing = sorted((key for key in names if key not in others), key=lambda key: (str(names[key]), str(key)))
    return {"count": len(missing),
            "employees": [{"employee_id": key, "employee_name": names[key]} for key in missing[:MAX_PEOPLE]]}


def compare(rows: list[dict[str, Any]], prev_rows: list[dict[str, Any]], employees: list[dict[str, Any]],
            group_by: str) -> dict[str, Any]:
    now_totals, now_groups, _ = _aggregate(rows, employees, group_by)
    was_totals, was_groups, was_unusable = _aggregate(prev_rows, employees, group_by)
    now_totals, was_totals = _finish(now_totals), _finish(was_totals)
    now_cost = {name: _finish(bucket)["total_cost"] for name, bucket in now_groups.items()}
    was_cost = {name: _finish(bucket)["total_cost"] for name, bucket in was_groups.items()}
    moves = [{"group": name, "previous_cost": was_cost.get(name, 0.0), "current_cost": now_cost.get(name, 0.0),
              "change": round(now_cost.get(name, 0.0) - was_cost.get(name, 0.0), 2),
              "change_pct": _pct(was_cost.get(name, 0.0), now_cost.get(name, 0.0))}
             for name in set(now_cost) | set(was_cost)]
    moves.sort(key=lambda move: (-abs(move["change"]), move["group"]))
    fields = ("headcount", "gross_pay", "net_pay", "employer_contribution", "total_cost", "overtime_pay")
    overtime = _delta(was_totals["overtime_pay"], now_totals["overtime_pay"])
    return {"totals": {name: _delta(was_totals[name], now_totals[name]) for name in fields},
            "groups": moves[:MAX_GROUPS], "groups_truncated": len(moves) > MAX_GROUPS,
            "joiners": _people_missing_from(rows, prev_rows), "leavers": _people_missing_from(prev_rows, rows),
            "overtime": {key: overtime[key] for key in ("previous", "current", "change")},
            "previous_unusable_amounts": was_unusable}


def _parse_components(raw: Any) -> list[tuple[str, float]] | None:
    """[(name, amount)] from a JSON string or a list; None when anything is unreadable."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, list):
        return None
    out = []
    for item in raw:
        amount = _finite(item.get("amount")) if isinstance(item, dict) else None
        name = item.get("component_name") if isinstance(item, dict) else None
        if amount is None or not isinstance(name, str) or not name.strip():
            return None
        out.append((name.strip(), amount))
    return out


def component_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Totals by earnings and by deduction component name. A row with unreadable components is counted
    in ``rows_unreadable`` and left out of both lists, never summed as zero."""
    earnings: dict[str, dict[str, float]] = {}
    deductions: dict[str, dict[str, float]] = {}
    unreadable = 0
    for row in rows:
        earned, deducted = _parse_components(row.get("earnings")), _parse_components(row.get("deductions"))
        if earned is None or deducted is None:
            unreadable += 1
            continue
        for target, items in ((earnings, earned), (deductions, deducted)):
            for name, amount in items:
                bucket = target.setdefault(name, {"total": 0.0, "people": 0})
                bucket["total"] += amount
                bucket["people"] += 1
    gross = sum(bucket["total"] for bucket in earnings.values())

    def listing(source: dict[str, dict[str, float]], share: bool) -> list[dict[str, Any]]:
        items = []
        for name, bucket in sorted(source.items(), key=lambda pair: (-pair[1]["total"], pair[0]))[:MAX_GROUPS]:
            item = {"component": name, "total": round(bucket["total"], 2), "people": int(bucket["people"])}
            if share:
                item["share_of_gross_percent"] = round(bucket["total"] / gross * 100, 2) if gross else None
            items.append(item)
        return items

    return {"earnings": listing(earnings, True), "deductions": listing(deductions, False),
            "earnings_total": round(gross, 2), "rows_unreadable": unreadable}


def build_register(*, run: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """One line per employee for a calculated run, with totals and the header check."""
    head = {"payrun_id": run.get("id"), "run_status": run.get("status"),
            "period": {"start": run.get("pay_period_start"), "end": run.get("pay_period_end")}}
    if not is_calculated(rows):
        return {**head, "calculated": False, "skipped": [{"what": "register", "reason": "run not calculated"}]}
    totals = _finish(_aggregate(rows, [], "none")[0])
    lines = [{"employee_id": row.get("employee_id"), "employee_name": row.get("employee_name"),
              "days_payable": _finite(row.get("days_payable")), "working_days": _finite(row.get("working_days")),
              "lop_days": _finite(row.get("lop_days")), "gross": _finite(row.get("gross_pay")),
              "total_deductions": _finite(row.get("total_deductions")),
              "reimbursements": _finite(row.get("reimbursements")), "net": _finite(row.get("net_pay"))}
             for row in sorted(rows, key=lambda r: (str(r.get("employee_name") or ""), str(r.get("employee_id"))))]
    result = {**head, "calculated": True, "totals": totals, "consistency": check_header(run, totals),
              "lines": lines[:MAX_REGISTER], "lines_total": len(lines), "truncated": len(lines) > MAX_REGISTER}
    if run.get("status") == "cancelled":
        result["caveats"] = [{"what": "run", "reason": "this run is cancelled; its figures are not payroll that was paid"}]
    return result


def same_month_last_year(run: dict[str, Any], runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The regular, non-cancelled run for the same calendar month one year earlier, or None."""
    start = str(run.get("pay_period_start") or "")[:7]
    try:
        year, month = int(start[:4]), int(start[5:7])
    except ValueError:
        return None
    wanted = f"{year - 1:04d}-{month:02d}"
    matches = [candidate for candidate in runs
               if str(candidate.get("pay_period_start") or "")[:7] == wanted and candidate.get("id") != run.get("id")
               and candidate.get("run_type") in (None, "regular") and candidate.get("status") != "cancelled"]
    # A paid run beats an unpaid one, then the newest wins, so the answer does not depend on list order.
    return max(matches, key=lambda c: (c.get("status") == "paid", str(c.get("created_at") or ""), str(c.get("id"))),
               default=None)


def build_report(*, run: dict[str, Any], rows: list[dict[str, Any]], employees: list[dict[str, Any]],
                 group_by: str, prev_rows: list[dict[str, Any]] | None = None, compared_to: str | None = None,
                 variance_wanted: bool = False,
                 variance_reason: str = "no earlier calculated regular run",
                 with_components: bool = False) -> dict[str, Any]:
    head = {"payrun_id": run.get("id"), "run_status": run.get("status"),
            "period": {"start": run.get("pay_period_start"), "end": run.get("pay_period_end")},
            "group_by": group_by}
    if not is_calculated(rows):
        return {**head, "calculated": False, "skipped": [{"what": "report", "reason": "run not calculated"}]}
    summary = summarize(rows, employees, group_by)
    skipped: list[dict[str, str]] = []
    if summary["unusable_amounts"]:
        skipped.append({"what": "amounts",
                        "reason": f"{summary['unusable_amounts']} unusable amount(s) counted as 0"})
    variance = None
    if variance_wanted:
        if prev_rows is None:
            skipped.append({"what": "variance", "reason": variance_reason})
        else:
            variance = {"compared_to": compared_to, **compare(rows, prev_rows, employees, group_by)}
            if variance["previous_unusable_amounts"]:
                skipped.append({"what": "variance amounts", "reason": (
                    f"{variance['previous_unusable_amounts']} unusable amount(s) in the comparison run counted as 0")})
    result = {**head, "calculated": True, "totals": summary["totals"], "groups": summary["groups"],
              "groups_truncated": summary["groups_truncated"], "consistency": check_header(run, summary["totals"]),
              "variance": variance, "skipped": skipped}
    if with_components:
        result["components"] = component_totals(rows)
        gross = summary["totals"]["gross_pay"]
        result["components"]["gross_pay_total"] = gross
        result["components"]["difference_from_gross_pay"] = round(result["components"]["earnings_total"] - gross, 2)
    if run.get("status") == "cancelled":
        result["caveats"] = [{"what": "run", "reason": "this run is cancelled; its figures are not payroll that was paid"}]
    return result

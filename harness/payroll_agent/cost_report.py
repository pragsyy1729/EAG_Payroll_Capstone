"""Pure payroll cost and variance calculations.

No network, no async: every function takes plain lists of rows shaped like AgentSwitch
``PayRunEmployee`` and ``Employee`` records. ``workers.run_payroll_cost_report`` does the
fetching; this module only computes. Every figure comes from the rows passed in.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .scan_checks import _number as number
from .scan_checks import is_calculated

MAX_GROUPS = 30
MAX_PEOPLE = 25
MONEY_TOLERANCE = 1.0
UNKNOWN_GROUP = "(unknown)"
_GROUP_FIELD = {"department": "_department_id_display", "location": "_work_location_id_display"}
_SUMMED = ("gross_pay", "net_pay", "total_deductions", "employer_contribution", "overtime_pay")
_HEADER = (("total_gross_pay", "gross_pay"), ("total_net_pay", "net_pay"),
           ("total_deductions", "total_deductions"),
           ("total_employer_contribution", "employer_contribution"), ("employee_count", "headcount"))


def _overtime(row: dict[str, Any]) -> float | None:
    """Overtime paid on a slip. AgentSwitch's `overtime_pay` field can be 0 while the pay is
    in the `earnings` list as an "Overtime" component (confirmed on the India tenant), so the
    component wins and the field is only the fallback."""
    parts = [number(item.get("amount")) for item in (row.get("earnings") or [])
             if isinstance(item, dict) and "overtime" in str(item.get("component_name", "")).lower()]
    if parts:
        return sum(part for part in parts if part is not None)
    return number(row.get("overtime_pay"))


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
            amount = _overtime(row) if name == "overtime_pay" else number(row.get(name))
            if amount is None:
                unusable += row.get(name) is not None      # a missing field is simply 0; a bad one is reported
                amount = 0.0
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
    for header_key, total_key in _HEADER:
        header = number(run.get(header_key))
        if header is None:
            continue
        tolerance = 0.5 if total_key == "headcount" else MONEY_TOLERANCE
        if abs(header - totals[total_key]) > tolerance:
            differences[header_key] = {"header": header, "slips": totals[total_key]}
    return {"header_matches_slips": not differences, "differences": differences}


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
    was_totals, was_groups, _ = _aggregate(prev_rows, employees, group_by)
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
            "overtime": {key: overtime[key] for key in ("previous", "current", "change")}}


def build_report(*, run: dict[str, Any], rows: list[dict[str, Any]], employees: list[dict[str, Any]],
                 group_by: str, prev_rows: list[dict[str, Any]] | None = None, compared_to: str | None = None,
                 variance_wanted: bool = False,
                 variance_reason: str = "no earlier calculated regular run") -> dict[str, Any]:
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
    return {**head, "calculated": True, "totals": summary["totals"], "groups": summary["groups"],
            "groups_truncated": summary["groups_truncated"], "consistency": check_header(run, summary["totals"]),
            "variance": variance, "skipped": skipped}

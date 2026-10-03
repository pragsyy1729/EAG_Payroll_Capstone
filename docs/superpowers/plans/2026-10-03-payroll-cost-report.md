# Payroll Cost and Variance Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `payroll_cost_report` capability that reports what a payroll run cost, by department or location, and optionally how it changed against the previous run.

**Architecture:** A pure module `payroll_agent/cost_report.py` computes totals, groups, a header-vs-slips consistency check and variance from plain row lists. A worker fetches the rows (reusing the scan's paged-fetch and comparison-run helpers) and hands them over. The LLM plans one call and explains the result; it never reads rows or does arithmetic.

**Tech Stack:** Python 3, asyncio, pytest (`asyncio_mode = "auto"`), the existing `AgentSwitchClient`, live graph, planner and `evals/` harness. Run everything from `harness/` with `.venv/bin/python`.

**Spec:** `docs/superpowers/specs/2026-10-03-payroll-cost-report-design.md`

## Global Constraints

- The report is read-only: only `.get` and `.list` AgentSwitch calls. It never calculates or changes a run. The capability has no `side_effect`.
- Total cost = `gross_pay + employer_contribution`. Money is rounded to 2 decimals; percentages to 1.
- `group_by` is one of `department`, `location`, `none` (default `department`). A group is the employee's `_department_id_display` / `_work_location_id_display`; an employee missing from `Employee.list`, or with no value, falls in the group `(unknown)`. `none` returns no groups.
- At most 30 groups (cost report) and 30 variance groups, with a `truncated` flag; at most 25 joiners and 25 leavers listed, with the full `count`.
- `change_pct` is `null` when the previous value is zero or missing.
- Not calculated (no rows, or no row with net pay above 0): `calculated: false`, no totals, groups or variance, and `skipped` = `[{"what": "report", "reason": "run not calculated"}]`. `Employee.list` and the comparison run are not fetched in that case.
- `consistency` compares the PayRun header (`total_gross_pay`, `total_net_pay`, `total_deductions`, `total_employer_contribution`, `employee_count`) with the slip sums: money within 1.0, headcount within 0.5; a header field that is missing is skipped; only mismatches are listed.
- `Employee.list` is fetched only when `group_by` is not `none`. A tool error on any fetch returns the tool's own error result; a short fetch of the run's own rows or of `Employee.list` returns `scan_incomplete`; a short or missing comparison run skips only the variance, with the reason.
- Amounts may be numbers or numeric strings; a present-but-unusable amount counts as 0 and is reported once under `skipped` as `{"what": "amounts", "reason": "<n> unusable amount(s) counted as 0"}`.
- Graded tests are hand-written by the team; a test written by Claude scores zero. Every test file here says it is implementation scaffolding and not a graded submission; scaffold eval tasks use `authored_by: "scaffold"`.
- **Git:** commit with this identity, set for the command only and never in config, and keep the trailer:
  `git -c user.name="Hari Prasath" -c user.email="52521279+Batflash5@users.noreply.github.com" commit ...`
  with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` ending the message. Work on branch `payroll-cost-report`. Push **only that branch** (`git push -u origin payroll-cost-report`), never `main`, and open no pull request.
- Match existing style: `from __future__ import annotations`, a module docstring stating purpose, comments only where the why is not obvious.

## Review Focus

Inputs the spec implies that a happy-path test would miss, most likely first:

1. A header total that disagrees with the slip sums: reported under `consistency.differences`, never a crash or a silent pass (Task 1).
2. A payee missing from `Employee.list`: grouped as `(unknown)`, never a `KeyError` (Task 1).
3. A group that exists only in the current run: its `change_pct` is `null`, not a division by zero (Task 1).
4. Amounts that are strings or unusable: parsed or counted and reported, never a `TypeError` (Task 1).
5. An uncalculated run, a denied `Employee.list`, and a short fetch: each ends visibly and correctly, and the uncalculated run needs neither the employee list nor a comparison (Task 2).

---

### Task 1: The pure calculations

**Files:**
- Create: `harness/payroll_agent/cost_report.py`
- Test: `harness/tests/test_cost_report_scaffold.py`

**Interfaces:**
- Consumes (existing): `payroll_agent.scan_checks._number` (numeric coercion) and `scan_checks.is_calculated(rows) -> bool`.
- Produces (used by Task 2):
  - `MAX_GROUPS = 30`, `MAX_PEOPLE = 25`
  - `summarize(rows, employees, group_by) -> {"totals", "groups", "groups_truncated", "unusable_amounts"}`
  - `compare(rows, prev_rows, employees, group_by) -> {"totals", "groups", "groups_truncated", "joiners", "leavers", "overtime"}`
  - `check_header(run, totals) -> {"header_matches_slips": bool, "differences": dict}`
  - `build_report(*, run, rows, employees, group_by, prev_rows=None, compared_to=None, variance_wanted=False, variance_reason="no earlier calculated regular run") -> dict`

- [ ] **Step 1: Write the failing tests**

Create `harness/tests/test_cost_report_scaffold.py`:

```python
"""Implementation scaffolding for payroll_agent/cost_report.py.

NOT the team's graded tests. These only check that the code behaves as
designed while it is being built.
"""
from __future__ import annotations

from payroll_agent import cost_report as cr


def slip(emp, gross, net, employer=0.0, ot=0.0, deductions=0.0, name=None):
    return {"id": f"s-{emp}", "employee_id": emp, "employee_name": name or f"Name {emp}",
            "gross_pay": gross, "net_pay": net, "employer_contribution": employer,
            "overtime_pay": ot, "total_deductions": deductions}


def person(emp, dept, loc):
    return {"id": emp, "_department_id_display": dept, "_work_location_id_display": loc}


EMPLOYEES = [person("a", "Production", "Pune"), person("b", "Production", "Pune"),
             person("c", "Stores", "Nashik"), person("d", "Stores", "Nashik")]
NOW = [slip("a", 1000, 900, 100, 50), slip("b", 2000, 1800, 200), slip("c", 500, 450, 50, 10)]
WAS = [slip("a", 800, 700, 80, 100), slip("b", 2000, 1800, 200), slip("d", 400, 350, 40)]
RUN = {"id": "R1", "status": "paid", "pay_period_start": "2026-07-01", "pay_period_end": "2026-07-31",
       "total_gross_pay": 3500, "total_net_pay": 3150, "total_deductions": 0,
       "total_employer_contribution": 350, "employee_count": 3}


def test_summarize_groups_by_department_and_ranks_by_cost():
    out = cr.summarize(NOW, EMPLOYEES, "department")
    assert out["totals"] == {"headcount": 3, "gross_pay": 3500.0, "net_pay": 3150.0, "total_deductions": 0.0,
                             "employer_contribution": 350.0, "overtime_pay": 60.0, "total_cost": 3850.0}
    assert [g["group"] for g in out["groups"]] == ["Production", "Stores"]
    production = out["groups"][0]
    assert production["headcount"] == 2 and production["total_cost"] == 3300.0
    assert production["share_of_cost_pct"] == 85.7
    assert out["groups"][1]["share_of_cost_pct"] == 14.3


def test_summarize_groups_by_location():
    out = cr.summarize(NOW, EMPLOYEES, "location")
    assert [g["group"] for g in out["groups"]] == ["Pune", "Nashik"]


def test_group_by_none_returns_totals_and_no_groups():
    out = cr.summarize(NOW, EMPLOYEES, "none")
    assert out["groups"] == [] and out["totals"]["headcount"] == 3


def test_an_employee_missing_from_the_employee_list_is_grouped_as_unknown():
    out = cr.summarize([slip("z", 100, 90)], EMPLOYEES, "department")
    assert [g["group"] for g in out["groups"]] == ["(unknown)"]


def test_groups_are_capped_but_totals_are_not():
    employees = [person(f"e{i}", f"D{i:02d}", "L") for i in range(35)]
    rows = [slip(f"e{i}", 100, 90) for i in range(35)]
    out = cr.summarize(rows, employees, "department")
    assert len(out["groups"]) == cr.MAX_GROUPS == 30
    assert out["groups_truncated"] is True and out["totals"]["headcount"] == 35


def test_string_amounts_are_parsed():
    out = cr.summarize([slip("a", "1000.00", "900")], EMPLOYEES, "none")
    assert out["totals"]["gross_pay"] == 1000.0 and out["unusable_amounts"] == 0


def test_an_unusable_amount_counts_as_zero_and_is_counted():
    row = {"employee_id": "a", "gross_pay": "abc", "net_pay": 10}      # other fields simply missing
    out = cr.summarize([row], EMPLOYEES, "none")
    assert out["totals"]["gross_pay"] == 0.0 and out["unusable_amounts"] == 1


def test_check_header_reports_only_mismatches():
    totals = cr.summarize(NOW, EMPLOYEES, "none")["totals"]
    ok = {"total_gross_pay": 3500, "total_net_pay": 3150, "total_deductions": 0,
          "total_employer_contribution": 350, "employee_count": 3}
    assert cr.check_header(ok, totals) == {"header_matches_slips": True, "differences": {}}
    off = cr.check_header({**ok, "total_gross_pay": 3510, "employee_count": 4}, totals)
    assert off["header_matches_slips"] is False
    assert off["differences"] == {"total_gross_pay": {"header": 3510.0, "slips": 3500.0},
                                  "employee_count": {"header": 4.0, "slips": 3}}


def test_check_header_allows_rounding_and_skips_missing_header_fields():
    totals = cr.summarize(NOW, EMPLOYEES, "none")["totals"]
    assert cr.check_header({"total_gross_pay": 3500.9}, totals)["header_matches_slips"] is True
    assert cr.check_header({"total_gross_pay": None}, totals)["differences"] == {}


def test_compare_reports_totals_groups_joiners_leavers_and_overtime():
    out = cr.compare(NOW, WAS, EMPLOYEES, "department")
    assert out["totals"]["gross_pay"] == {"previous": 3200.0, "current": 3500.0, "change": 300.0, "change_pct": 9.4}
    assert out["totals"]["total_cost"] == {"previous": 3520.0, "current": 3850.0, "change": 330.0, "change_pct": 9.4}
    assert out["overtime"] == {"previous": 100.0, "current": 60.0, "change": -40.0}
    assert out["groups"] == [
        {"group": "Production", "previous_cost": 3080.0, "current_cost": 3300.0, "change": 220.0, "change_pct": 7.1},
        {"group": "Stores", "previous_cost": 440.0, "current_cost": 550.0, "change": 110.0, "change_pct": 25.0}]
    assert out["joiners"] == {"count": 1, "employees": [{"employee_id": "c", "employee_name": "Name c"}]}
    assert out["leavers"] == {"count": 1, "employees": [{"employee_id": "d", "employee_name": "Name d"}]}


def test_a_group_with_no_previous_cost_has_no_percentage():
    out = cr.compare(NOW, [slip("a", 800, 700, 80)], EMPLOYEES, "department")
    stores = next(g for g in out["groups"] if g["group"] == "Stores")
    assert stores["previous_cost"] == 0.0 and stores["change_pct"] is None


def test_joiner_list_is_capped_but_the_count_is_full():
    rows = [slip(f"e{i:02d}", 100, 90) for i in range(30)]
    out = cr.compare(rows, [], [], "none")
    assert out["joiners"]["count"] == 30
    assert len(out["joiners"]["employees"]) == cr.MAX_PEOPLE == 25


def test_duplicate_rows_count_in_headcount_but_not_as_extra_joiners():
    rows = [slip("a", 100, 90), slip("a", 100, 90)]
    assert cr.summarize(rows, EMPLOYEES, "none")["totals"]["headcount"] == 2
    assert cr.compare(rows, [], EMPLOYEES, "none")["joiners"]["count"] == 1


def test_build_report_for_a_calculated_run():
    out = cr.build_report(run=RUN, rows=NOW, employees=EMPLOYEES, group_by="department")
    assert out["calculated"] is True and out["variance"] is None and out["skipped"] == []
    assert out["consistency"]["header_matches_slips"] is True
    assert out["period"] == {"start": "2026-07-01", "end": "2026-07-31"} and out["group_by"] == "department"


def test_build_report_for_an_uncalculated_run_reports_nothing_else():
    out = cr.build_report(run=RUN, rows=[slip("a", 0, 0)], employees=EMPLOYEES, group_by="department")
    assert out["calculated"] is False and "totals" not in out and "groups" not in out
    assert out["skipped"] == [{"what": "report", "reason": "run not calculated"}]


def test_build_report_notes_when_variance_was_wanted_but_there_is_no_baseline():
    out = cr.build_report(run=RUN, rows=NOW, employees=EMPLOYEES, group_by="none", variance_wanted=True)
    assert out["variance"] is None and out["groups"] == []
    assert out["skipped"] == [{"what": "variance", "reason": "no earlier calculated regular run"}]


def test_build_report_with_variance():
    out = cr.build_report(run=RUN, rows=NOW, employees=EMPLOYEES, group_by="department",
                          prev_rows=WAS, compared_to="R0", variance_wanted=True)
    assert out["variance"]["compared_to"] == "R0" and out["variance"]["joiners"]["count"] == 1


def test_build_report_reports_unusable_amounts():
    out = cr.build_report(run=RUN, rows=[slip("a", "abc", 900, 100)], employees=EMPLOYEES, group_by="none")
    assert {"what": "amounts", "reason": "1 unusable amount(s) counted as 0"} in out["skipped"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd harness && .venv/bin/python -m pytest tests/test_cost_report_scaffold.py -q`
Expected: FAIL at collection with `ImportError: cannot import name 'cost_report'`.

- [ ] **Step 3: Write the implementation**

Create `harness/payroll_agent/cost_report.py`:

```python
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
            amount = number(row.get(name))
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
```

- [ ] **Step 4: Run the tests, then the whole suite and lint**

Run: `cd harness && .venv/bin/python -m pytest tests/test_cost_report_scaffold.py -q`
Expected: 18 passed.

Run: `cd harness && .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check payroll_agent tests`
Expected: all pass (the existing 95 plus these), lint clean. If ruff reports import ordering in `cost_report.py`, run `.venv/bin/python -m ruff check --fix payroll_agent/cost_report.py` and re-run the tests.

- [ ] **Step 5: Commit**

```bash
git add harness/payroll_agent/cost_report.py harness/tests/test_cost_report_scaffold.py
git -c user.name="Hari Prasath" -c user.email="52521279+Batflash5@users.noreply.github.com" commit -m "Add pure payroll cost and variance calculations

Totals, groups by department or location, a header-vs-slips consistency
check, and month-over-month variance with joiners and leavers, all
computed from plain row lists with no network access.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The capability and its worker

**Files:**
- Modify: `harness/payroll_agent/capabilities.py` (one `Capability` added in `default_registry`)
- Modify: `harness/payroll_agent/workers.py` (import, one worker, one `_WORKERS` entry)
- Test: `harness/tests/test_cost_report_worker_scaffold.py`

**Interfaces:**
- Consumes (Task 1): `cost_report.build_report(...)`. Existing: `workers._call_tool`, `workers._fetch_all(ctx, tool, args, jurisdiction) -> (rows, error_dict | None)`, `workers._comparison_rows(ctx, run, explicit, jurisdiction) -> (compared_to, rows | None, reason)`, `scan_checks.is_calculated`, `RunContext`, `TaskSpec`.
- Produces (used by Task 3): capability name `payroll_cost_report`; `workers.run_payroll_cost_report(ctx, task) -> dict` returning the `build_report` shape, or the tool's own error result, or `{"error": True, "tool": "pre_payroll_scan", "code": "scan_incomplete", ...}` for a short fetch.

- [ ] **Step 1: Write the failing tests**

Create `harness/tests/test_cost_report_worker_scaffold.py`:

```python
"""Implementation scaffolding for the payroll_cost_report capability and worker.

NOT the team's graded tests. A fake AgentSwitch client stands in for the platform
so fetching, paging, comparison-run choice and error handling can be checked offline.
"""
from __future__ import annotations

import pytest

from payroll_agent import workers
from payroll_agent.agentswitch import AgentSwitchToolError
from payroll_agent.capabilities import CapabilityError, default_registry
from payroll_agent.core.live_graph import TaskSpec


class FakeAgentSwitch:
    def __init__(self, *, runs, slips, employees, page_cap=100, short_total=None, denied=()):
        self.runs = {item["id"]: item for item in runs}
        self.slips, self.employees = slips, employees
        self.page_cap, self.short_total, self.denied = page_cap, short_total, set(denied)
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name, arguments=None, *, jurisdiction):
        arguments = arguments or {}
        self.calls.append((name, arguments))
        if name in self.denied:
            raise AgentSwitchToolError(name, -32001, "denied")
        if name == "PayRun.get":
            if arguments["id"] not in self.runs:
                raise AgentSwitchToolError(name, -32000, "not found")
            return dict(self.runs[arguments["id"]])
        if name == "PayRun.list":
            return self._page(list(self.runs.values()), arguments)
        if name == "PayRunEmployee.list":
            return self._page(self.slips.get(arguments.get("payrun_id"), []), arguments)
        if name == "Employee.list":
            return self._page(self.employees, arguments)
        raise AssertionError(f"the report must stay read-only; unexpected tool {name}")

    def _page(self, rows, arguments):
        offset, limit = arguments.get("offset", 0), min(arguments.get("limit", 10), self.page_cap)
        total = len(rows) if self.short_total is None else self.short_total
        return {"data": rows[offset:offset + limit], "limit": limit, "offset": offset, "total": total}


def run(rid, start, end, *, status="paid", header=None, run_type="regular"):
    return {"id": rid, "pay_period_start": start, "pay_period_end": end, "status": status,
            "run_type": run_type, **(header or {})}


def slip(rid, emp, gross, net, employer=0.0, ot=0.0):
    return {"id": f"{rid}-{emp}", "payrun_id": rid, "employee_id": emp, "employee_name": emp.upper(),
            "gross_pay": gross, "net_pay": net, "employer_contribution": employer, "overtime_pay": ot,
            "total_deductions": 0.0}


def person(emp, dept="Production", loc="Pune"):
    return {"id": emp, "_department_id_display": dept, "_work_location_id_display": loc}


R3_HEADER = {"total_gross_pay": 3600, "total_net_pay": 3240, "total_deductions": 0,
             "total_employer_contribution": 360, "employee_count": 3}


def standard_fake(**overrides):
    kwargs = dict(
        runs=[run("R1", "2026-06-01", "2026-06-30"), run("R2", "2026-07-01", "2026-07-31"),
              run("R3", "2026-08-01", "2026-08-31", status="pending_approval", header=R3_HEADER)],
        slips={"R1": [slip("R1", "a", 800, 700), slip("R1", "b", 2000, 1800)],
               "R2": [slip("R2", "a", 1000, 900, 100, 50), slip("R2", "b", 2000, 1800, 200)],
               "R3": [slip("R3", "a", 1100, 990, 110, 60), slip("R3", "b", 2000, 1800, 200),
                      slip("R3", "c", 500, 450, 50)]},
        employees=[person("a"), person("b"), person("c", "Stores", "Nashik")])
    kwargs.update(overrides)
    return FakeAgentSwitch(**kwargs)


def report(fake, **extra):
    ctx = workers.RunContext(run_id="r", store=None, llm=None, agentswitch=fake)
    task = TaskSpec("rep", "payroll_cost_report", {"jurisdiction": "IN", "payrun_id": "R3", **extra})
    return workers.run_payroll_cost_report(ctx, task)


def called(fake):
    return [name for name, _ in fake.calls]


async def test_default_report_groups_by_department_and_matches_the_header():
    fake = standard_fake()
    result = await report(fake)
    assert result["calculated"] is True and result["totals"]["headcount"] == 3
    assert [g["group"] for g in result["groups"]] == ["Production", "Stores"]
    assert result["consistency"]["header_matches_slips"] is True
    assert result["variance"] is None
    assert set(called(fake)) == {"PayRun.get", "PayRunEmployee.list", "Employee.list"}


async def test_group_by_none_does_not_fetch_the_employee_list():
    fake = standard_fake()
    result = await report(fake, group_by="none")
    assert result["groups"] == [] and "Employee.list" not in called(fake)


async def test_with_variance_compares_to_the_latest_earlier_calculated_run():
    fake = standard_fake()
    result = await report(fake, with_variance=True)
    assert result["variance"]["compared_to"] == "R2"
    assert result["variance"]["joiners"]["count"] == 1          # employee c is new
    assert result["variance"]["totals"]["gross_pay"]["previous"] == 3000.0


async def test_an_explicit_compare_to_is_used_and_implies_variance():
    fake = standard_fake()
    result = await report(fake, compare_to="R1")
    assert result["variance"]["compared_to"] == "R1"


async def test_an_uncalculated_run_needs_no_employee_list_or_comparison():
    fake = standard_fake(slips={"R3": [slip("R3", "a", 0, 0)]})
    result = await report(fake, with_variance=True)
    assert result["calculated"] is False and "totals" not in result
    assert called(fake).count("PayRunEmployee.list") == 1 and "Employee.list" not in called(fake)


async def test_no_earlier_run_skips_only_the_variance():
    fake = standard_fake(runs=[run("R3", "2026-08-01", "2026-08-31", header=R3_HEADER)])
    result = await report(fake, with_variance=True)
    assert result["variance"] is None and result["totals"]["headcount"] == 3
    assert result["skipped"] == [{"what": "variance", "reason": "no earlier calculated regular run"}]


async def test_a_short_fetch_of_the_runs_own_rows_is_scan_incomplete():
    result = await report(standard_fake(short_total=50))
    assert result["error"] is True and result["code"] == "scan_incomplete"


async def test_a_denied_employee_list_keeps_its_own_error():
    result = await report(standard_fake(denied=("Employee.list",)))
    assert result["error"] is True and result["tool"] == "Employee.list" and result["code"] == -32001


async def test_paging_collects_every_row():
    result = await report(standard_fake(page_cap=2))
    assert result["totals"]["headcount"] == 3


def test_capability_is_registered_read_only_with_defaults_and_id_provenance():
    registry = default_registry()
    capability = registry.get("payroll_cost_report")
    assert capability.side_effect is False
    assert "payroll_cost_report" in registry.family("evidence")
    assert "payroll_cost_report" in workers._WORKERS
    clean = registry.validate("payroll_cost_report", {"jurisdiction": "IN", "payrun_id": "R3"},
                              known_values={"R3"})
    assert clean["group_by"] == "department" and clean["with_variance"] is False
    with pytest.raises(CapabilityError):
        registry.validate("payroll_cost_report", {"jurisdiction": "IN", "payrun_id": "R3"}, known_values=set())


@pytest.mark.parametrize("bad", [{"group_by": "team"}, {"with_variance": "yes"}, {"compare_to": "R9"}])
def test_capability_rejects_bad_arguments(bad):
    with pytest.raises(CapabilityError):
        default_registry().validate("payroll_cost_report", {"jurisdiction": "IN", "payrun_id": "R3", **bad},
                                    known_values={"R3"})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd harness && .venv/bin/python -m pytest tests/test_cost_report_worker_scaffold.py -q`
Expected: FAIL (`AttributeError: ... no attribute 'run_payroll_cost_report'` and `unknown capability 'payroll_cost_report'`).

- [ ] **Step 3: Add the capability**

In `harness/payroll_agent/capabilities.py`, inside `default_registry()`, insert immediately before the `Capability(` whose name is `"run_payroll"` (that is, right after the `pre_payroll_scan` entry):

```python
        Capability(
            "payroll_cost_report",
            "Report what one calculated PayRun cost: totals (gross pay, net pay, employer "
            "contribution, total cost = gross + employer contribution, headcount, overtime) and "
            "a breakdown by department or work location. Set `with_variance` (or pass "
            "`compare_to`) to also compare against the previous calculated run: change in totals "
            "and per group, joiners and leavers, and the overtime change. Read-only; if the run "
            "is not calculated it says so and reports nothing else. Department and location are "
            "each employee's current ones. Use a `payrun_id` taken from an earlier outcome (for "
            "example `list_payruns`).",
            {"jurisdiction": _JURISDICTION,
             "payrun_id": string("The PayRun to report on.", maximum=200, format="id"),
             "group_by": string("Break the cost down by this.", required=False, default="department",
                                choices=("department", "location", "none")),
             "with_variance": Argument("boolean", "Also compare with the previous calculated run.",
                                       required=False, default=False),
             "compare_to": string("PayRun id to compare against; implies variance. Omit to use the "
                                  "most recent earlier calculated regular run.",
                                  required=False, maximum=200, format="id")},
            families=("evidence",),
        ),
```

- [ ] **Step 4: Add the worker**

In `harness/payroll_agent/workers.py`:

(a) Change the import line `from . import scan_checks` to:

```python
from . import cost_report, scan_checks
```

(b) Insert immediately before the comment line `# run_payroll rewrites the slips of whichever run it reuses`:

```python
async def run_payroll_cost_report(ctx: RunContext, task: TaskSpec) -> dict[str, Any]:
    """Read-only: fetch, then let cost_report compute. Never calls a mutating tool."""
    jurisdiction, payrun_id = task.input["jurisdiction"], task.input["payrun_id"]
    group_by = task.input.get("group_by", "department")
    compare_to = task.input.get("compare_to")
    variance_wanted = bool(task.input.get("with_variance")) or bool(compare_to)
    run = await _call_tool(ctx, "PayRun.get", {"id": payrun_id}, jurisdiction)
    if run.get("error"):
        return run
    rows, problem = await _fetch_all(ctx, "PayRunEmployee.list", {"payrun_id": payrun_id}, jurisdiction)
    if problem:
        return problem
    employees: list[dict[str, Any]] = []
    prev_rows: list[dict[str, Any]] | None = None
    compared_to: str | None = None
    reason = "no earlier calculated regular run"
    # An uncalculated run reports nothing else, so it needs neither the employee list
    # nor a comparison run.
    if scan_checks.is_calculated(rows):
        if group_by != "none":
            employees, problem = await _fetch_all(ctx, "Employee.list", {}, jurisdiction)
            if problem:
                return problem
        if variance_wanted:
            compared_to, prev_rows, reason = await _comparison_rows(ctx, run, compare_to, jurisdiction)
            if prev_rows is None:
                compared_to = None
    return cost_report.build_report(run=run, rows=rows, employees=employees, group_by=group_by,
                                    prev_rows=prev_rows, compared_to=compared_to,
                                    variance_wanted=variance_wanted, variance_reason=reason)


```

(c) In the `_WORKERS` dict, add after the `"pre_payroll_scan": run_pre_payroll_scan,` line:

```python
    "payroll_cost_report": run_payroll_cost_report,
```

- [ ] **Step 5: Run the tests, then the whole suite and lint**

Run: `cd harness && .venv/bin/python -m pytest tests/test_cost_report_worker_scaffold.py -q`
Expected: 13 passed.

Run: `cd harness && .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check payroll_agent tests`
Expected: all pass, lint clean. If a pre-existing test asserts the number of capabilities, update it and say so in the commit message.

- [ ] **Step 6: Commit**

```bash
git add harness/payroll_agent/capabilities.py harness/payroll_agent/workers.py harness/tests/test_cost_report_worker_scaffold.py
git -c user.name="Hari Prasath" -c user.email="52521279+Batflash5@users.noreply.github.com" commit -m "Add read-only payroll_cost_report capability and worker

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Scaffold eval tasks, live check, docs and push

**Files:**
- Create: `harness/evals/scripts/scaffold_cost_report.json`, `harness/evals/scripts/scaffold_cost_report_uncalculated.json`
- Modify: `harness/evals/tasks/scaffold.jsonl` (append two lines)
- Modify: `harness/WORKFLOWS.md` (family D), `harness/NEXT_STEPS.md` (roadmap item 3)
- Add to git: the spec and this plan

**Interfaces:**
- Consumes: Tasks 1 and 2, the existing runner, `ScriptedLLM` scripts, and the `node_result`, `tool_called`, `tool_not_called`, `no_mutation` verifiers. Real ids from the 2026-10-03 probe of the India tenant: June `PRUN-2026-00010` = `f77b46e3-4276-4f36-b2d3-d618fa068514`; July `PRUN-2026-00011` = `dd9bf1ea-eab9-4f93-b91f-7b6721ce4d0b`; September draft `PRUN-2026-00013` = `b5b0cc7d-7aef-4738-a6e0-bfaa01c94aaa`.
- Produces: two scaffold eval tasks run against the real tenant, recorded live results, updated docs, and the pushed branch.

- [ ] **Step 1: Write the two scripts**

Create `harness/evals/scripts/scaffold_cost_report.json`:

```json
{
  "name": "scaffold_cost_report",
  "note": "Scaffold only. Look up July 2026, report its cost by department with variance against June, answer. Read-only.",
  "steps": [
    {"reply": {"add": [{"id": "lookup", "capability": "list_payruns",
                        "arguments": {"jurisdiction": "IN", "month": "2026-07"}, "depends_on": []}],
               "cancel": [], "finish": false, "reason": "find the July 2026 run"}},
    {"reply": {"add": [{"id": "cost", "capability": "payroll_cost_report",
                        "arguments": {"jurisdiction": "IN", "payrun_id": "dd9bf1ea-eab9-4f93-b91f-7b6721ce4d0b",
                                      "group_by": "department", "with_variance": true},
                        "depends_on": ["lookup"]}],
               "cancel": [], "finish": false, "reason": "report July cost with variance"}},
    {"reply": {"add": [{"id": "answer", "capability": "answer_with_evidence",
                        "arguments": {"query": "What did July 2026 payroll cost by department, and how did it change from June?"},
                        "depends_on": ["cost"]}],
               "cancel": [], "finish": false, "reason": "report finished; answer from it"}},
    {"reply": {"ready": true, "missing": [], "reason": "cost report is present"}},
    {"reply": "Scripted answer: see the payroll_cost_report evidence."}
  ]
}
```

Create `harness/evals/scripts/scaffold_cost_report_uncalculated.json`:

```json
{
  "name": "scaffold_cost_report_uncalculated",
  "note": "Scaffold only. Report on the September draft, which is not calculated. Read-only.",
  "steps": [
    {"reply": {"add": [{"id": "lookup", "capability": "list_payruns",
                        "arguments": {"jurisdiction": "IN", "month": "2026-09"}, "depends_on": []}],
               "cancel": [], "finish": false, "reason": "find the September 2026 run"}},
    {"reply": {"add": [{"id": "cost", "capability": "payroll_cost_report",
                        "arguments": {"jurisdiction": "IN", "payrun_id": "b5b0cc7d-7aef-4738-a6e0-bfaa01c94aaa"},
                        "depends_on": ["lookup"]}],
               "cancel": [], "finish": false, "reason": "report September cost"}},
    {"reply": {"add": [{"id": "answer", "capability": "answer_with_evidence",
                        "arguments": {"query": "What did September 2026 payroll cost by department?"},
                        "depends_on": ["cost"]}],
               "cancel": [], "finish": false, "reason": "report finished; answer from it"}},
    {"reply": {"ready": true, "missing": [], "reason": "the report says the run is not calculated"}},
    {"reply": "Scripted answer: the September run is not calculated, so there is no cost to report yet."}
  ]
}
```

- [ ] **Step 2: Append the two scaffold tasks**

Append these two lines to `harness/evals/tasks/scaffold.jsonl` (one JSON object per line, no blank line between):

```json
{"id":"scaffold_scripted_cost_report","family":"check","authored_by":"scaffold","goal":"What did July 2026 payroll cost by department, and how did it change from June?","jurisdiction":"IN","allow":[],"mode":"dry_run","transport":"scripted","script":"scripts/scaffold_cost_report.json","watch":[{"tool":"PayRun.list","args":{"limit":100},"key":"id","fields":["status","pay_period_start"]}],"verifiers":[{"type":"tool_called","tool":"PayRunEmployee.list","min":2},{"type":"tool_not_called","tool":"PayRun.run_payroll"},{"type":"no_mutation"},{"type":"node_result","skill":"payroll_cost_report","path":"calculated","expect":true},{"type":"node_result","skill":"payroll_cost_report","path":"totals.headcount","expect":62},{"type":"node_result","skill":"payroll_cost_report","path":"variance.compared_to","expect":"f77b46e3-4276-4f36-b2d3-d618fa068514"}]}
{"id":"scaffold_scripted_cost_report_uncalculated","family":"check","authored_by":"scaffold","goal":"What did September 2026 payroll cost by department?","jurisdiction":"IN","allow":[],"mode":"dry_run","transport":"scripted","script":"scripts/scaffold_cost_report_uncalculated.json","watch":[{"tool":"PayRun.list","args":{"limit":100},"key":"id","fields":["status","pay_period_start"]}],"verifiers":[{"type":"tool_not_called","tool":"PayRun.calculate_payroll"},{"type":"no_mutation"},{"type":"node_result","skill":"payroll_cost_report","path":"calculated","expect":false}]}
```

- [ ] **Step 3: Run both against the real tenant**

Run:
```bash
cd harness
.venv/bin/python -m evals.runner --tasks evals/tasks/scaffold.jsonl --task-id scaffold_scripted_cost_report
.venv/bin/python -m evals.runner --tasks evals/tasks/scaffold.jsonl --task-id scaffold_scripted_cost_report_uncalculated
```
Expected: `pass` for each, exit code 0. If one reports `fail`, read `evals/runs/<latest>/<id>.score.json`, find the cause and fix it. Do not loosen a verifier to make it pass. If `totals.headcount` is not 62, report the real number and ask before changing the expectation.

- [ ] **Step 4: Check the report against the raw rows (independent recomputation)**

Run:
```bash
cd harness && .venv/bin/python - <<'EOF'
import asyncio, collections, json, glob
from dotenv import load_dotenv; load_dotenv(".env")
from payroll_agent.agentswitch import AgentSwitchClient
JULY = "dd9bf1ea-eab9-4f93-b91f-7b6721ce4d0b"
rec = json.load(open(sorted(glob.glob("evals/runs/*/scaffold_scripted_cost_report.json"))[-1]))
report = next(n for n in rec["result"]["nodes"].values() if n["skill"] == "payroll_cost_report")["result"]
async def main():
    c = AgentSwitchClient()
    try:
        slips = (await c.call_tool("PayRunEmployee.list", {"payrun_id": JULY, "limit": 100}, jurisdiction="IN"))["data"]
        emps = {e["id"]: e for e in (await c.call_tool("Employee.list", {"limit": 100}, jurisdiction="IN"))["data"]}
    finally:
        await c.close()
    cost = collections.defaultdict(float)
    for s in slips:
        cost[emps[s["employee_id"]]["_department_id_display"]] += s["gross_pay"] + s["employer_contribution"]
    print("totals gross:", report["totals"]["gross_pay"], "vs raw", round(sum(s["gross_pay"] for s in slips), 2))
    print("consistency:", report["consistency"])
    mine = {g["group"]: g["total_cost"] for g in report["groups"]}
    print("groups match raw recomputation:", all(abs(mine[k] - round(v, 2)) < 0.01 for k, v in cost.items()) and set(mine) == set(cost))
    print("variance compared_to:", report["variance"]["compared_to"], "| joiners", report["variance"]["joiners"]["count"], "| leavers", report["variance"]["leavers"]["count"])
asyncio.run(main())
EOF
```
Expected: gross totals equal, groups match, and `consistency` printed. If `header_matches_slips` is false, record the real differences (the header may legitimately differ for some fields); do not change the tolerance to hide it.

- [ ] **Step 5: Live check with the real models (needs the gateway and provider setup in NEXT_STEPS.md)**

Confirm the gateway is up: `curl -s http://127.0.0.1:8111/healthz`.

```bash
cd harness
for goal in "What was payroll cost by department in July 2026?" "Why did payroll cost change from June to July 2026?" "What did July 2026 payroll cost by work location?"; do
  .venv/bin/python -m payroll_agent.run "$goal" --jurisdiction IN > /private/tmp/claude-501/cost_live.json 2>/dev/null
  .venv/bin/python - <<'EOF'
import json
r = json.load(open("/private/tmp/claude-501/cost_live.json"))
rep = next((n.get("result") for n in r["nodes"].values() if n["skill"] == "payroll_cost_report"), None)
print("skills:", [n["skill"] for n in r["nodes"].values()], "| answered:", r["answer"] is not None, "| declined:", r["declined"])
if rep and not rep.get("error"): print("   report: group_by", rep["group_by"], "| variance", rep["variance"] is not None, "| total_cost", rep["totals"]["total_cost"])
print("   answer:", (r["answer"] or "NONE")[:240].replace("\n", " "))
EOF
done
```
Expected: the model calls `payroll_cost_report` (with `with_variance` for the second question, and `group_by: location` for the third) and answers. Record what actually happened, including failures; the planner can be flaky and a failure here is not necessarily a bug in this capability. Check each answer's numbers against the report; do not tune prompts without telling the user.

- [ ] **Step 6: Update the docs**

In `harness/WORKFLOWS.md`, section D, replace its `Tools:` / `Status: todo.` line with a `Status: partial` paragraph saying `payroll_cost_report` covers cost by department or location and month-over-month variance (totals, groups, joiners and leavers, overtime), read-only; department and location are the employee's current ones; cost-centre tags, budgets, allowance-level detail and the US tenant are not built or verified. Link `docs/superpowers/specs/2026-10-03-payroll-cost-report-design.md`.

In `harness/NEXT_STEPS.md`, tick roadmap item 3 and add a short "Result" line with the real numbers from Steps 3 to 5 (July headcount and total cost, the consistency result, which live queries succeeded) and any failure seen.

- [ ] **Step 7: Final run, commit and push the branch**

Run: `cd harness && .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check payroll_agent evals tests`
Expected: all pass, lint clean.

```bash
cd ..
git add harness/evals harness/WORKFLOWS.md harness/NEXT_STEPS.md docs/superpowers/specs/2026-10-03-payroll-cost-report-design.md docs/superpowers/plans/2026-10-03-payroll-cost-report.md
git -c user.name="Hari Prasath" -c user.email="52521279+Batflash5@users.noreply.github.com" commit -m "Add scaffold eval tasks for payroll_cost_report; spec, plan and docs

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
gh api user --jq .login        # must print Batflash5
git push -u origin payroll-cost-report
git status -sb | head -1
```
Expected: the login prints `Batflash5`, the branch is pushed, and `origin/main` is unchanged. Do not push `main` and do not open a pull request.

---

## Self-review (against the spec)

- **Purpose, success criteria:** Task 3 Step 4 recomputes the totals and groups from raw rows and prints the consistency check.
- **Components:** `cost_report.py` (Task 1), capability and worker (Task 2). Matches the spec's component list.
- **Arguments table:** `jurisdiction`, `payrun_id`, `group_by` (default `department`), `with_variance` (default false), `compare_to` (implies variance): Task 2 capability and its validation tests.
- **Result shape, caps, `(unknown)` group, null percentages, not-calculated shape:** Task 1 tests.
- **Consistency check (tolerance, missing header fields):** Task 1 tests.
- **Variance (groups union, joiners and leavers, overtime):** Task 1 compare tests; Task 2 worker tests.
- **Fetch plan (Employee.list only when grouping; comparison only when variance; uncalculated needs neither):** Task 2 tests.
- **Error handling (tool error kept, `scan_incomplete`, short comparison skips variance):** Task 2 tests.
- **Testing and grading split:** every test file is labelled scaffold; the eval tasks are `authored_by: "scaffold"`; no graded task is written.
- **Limitations (current department, US unverified):** recorded in WORKFLOWS.md (Task 3 Step 6).
- **Type consistency:** `build_report` keyword arguments, `_fetch_all` returning `(rows, error | None)`, and `_comparison_rows` returning `(id | None, rows | None, reason)` are used identically in Tasks 1 and 2.
- **Placeholders:** none. Real counts and results in Task 3 come from running the steps.
- **Git:** identity, branch-only push and the `Batflash5` check are in Global Constraints and Step 7.

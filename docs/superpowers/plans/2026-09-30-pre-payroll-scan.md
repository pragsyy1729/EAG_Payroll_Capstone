# Pre-Payroll Risk Scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `pre_payroll_scan` capability that checks a calculated payroll run for four kinds of problems and returns traceable findings.

**Architecture:** One new planner capability whose worker only fetches (PayRun, PayRunEmployee, Employee) and hands plain rows to pure check functions in `payroll_agent/scan_checks.py`. The LLM plans one call and explains the result; it never reads rows or does arithmetic. A new `node_result` verifier in `evals/` lets eval tasks assert on the scan's actual output.

**Tech Stack:** Python 3, asyncio, pytest with `asyncio_mode = "auto"`, the existing `AgentSwitchClient` (MCP) and `evals/` harness. Run everything from `harness/` with `.venv/bin/python`.

**Spec:** `docs/superpowers/specs/2026-09-30-pre-payroll-scan-design.md`

## Global Constraints

- The scan is read-only: only `.get` and `.list` AgentSwitch calls. It never calls `PayRun.calculate_payroll` or any mutating tool. The capability has no `side_effect` flag.
- Checks in v1: `payee_status`, `net_pay_change`, `net_pay_sanity`, `duplicate_payees`. Bank, PAN and Aadhaar fields are redacted for this seat and are never used.
- Severity tiers are `high`, `medium`, `info`. `net_pay_change`: at least 50% is high; at least `change_threshold_pct` (default 30) is medium. A threshold of 50 or more leaves only the high tier.
- Findings are sorted by severity, then employee name, and capped at 40. `counts` always reflects the full set; `truncated` says when the cap hit.
- Not calculated means no rows, or every row has net pay at or below zero (or missing): return `calculated: false`, no findings, and all four checks under `skipped` with reason `run not calculated`.
- Comparison run: the most recent earlier `regular` run (by `pay_period_start`, not label), not `cancelled`, with calculated rows.
- A short fetch of the run's own rows or of `Employee.list` returns an error result with code `scan_incomplete`. A short comparison run skips `net_pay_change` only.
- Employees with previous-run net pay zero or missing, or appearing more than once in either run, count in `counts.not_comparable` and are not flagged by `net_pay_change`.
- Graded tests are hand-written by the team; a test written by Claude scores zero. Every test file this plan creates is implementation scaffolding, says so in its docstring, and is never presented as a graded submission. Scaffold eval tasks use `authored_by: "scaffold"`.
- Follow the existing code style: `from __future__ import annotations`, module docstring stating purpose, comments only where the why is not obvious.

## Review Focus

Inputs the spec implies but a happy-path test would miss, most likely first:

1. A payee whose `employee_id` is not in `Employee.list`: a high finding, never a `KeyError` (Task 1 tests it, Task 2 runs it through the worker).
2. A previous-run row with net pay zero or `None`: counted as `not_comparable`, never `ZeroDivisionError` (Task 1).
3. More than 40 findings: only 40 returned, `truncated` true, counts still the full number (Task 1).
4. More than one page of rows, and a server that reports a larger `total` than it returns: paging collects everything, and a short fetch becomes `scan_incomplete` (Task 2).
5. `exit_date` arriving as a full timestamp such as `2026-08-15T00:00:00`: compared on its date part (Task 1).

---

### Task 1: Pure check functions

**Files:**
- Create: `harness/payroll_agent/scan_checks.py`
- Test: `harness/tests/test_scan_checks_scaffold.py`

**Interfaces:**
- Consumes: nothing (pure Python, plain dicts shaped like `PayRunEmployee` and `Employee` rows).
- Produces (used by Task 2):
  - `CHECKS: tuple[str, ...]`, `DEFAULT_CHANGE_PCT: int = 30`, `MAX_FINDINGS: int = 40`
  - `is_calculated(rows: list[dict]) -> bool`
  - `payee_status(rows, employees_by_id: dict[str, dict], period_end: str | None) -> list[dict]`
  - `net_pay_change(rows, prev_rows, threshold_pct: int) -> tuple[list[dict], dict]` where the dict is `{"new_in_run": int, "not_comparable": int}`
  - `net_pay_sanity(rows) -> list[dict]`
  - `duplicate_payees(rows) -> list[dict]`
  - `run_checks(*, rows, prev_rows, employees, period_end, threshold_pct=30, prev_reason="no earlier calculated regular run") -> dict` with keys `calculated`, `skipped`, `counts`, `findings`, `truncated`. `prev_rows=None` means "no comparison available".

- [ ] **Step 1: Write the failing tests**

Create `harness/tests/test_scan_checks_scaffold.py`:

```python
"""Implementation scaffolding for payroll_agent/scan_checks.py.

NOT the team's graded tests. These only check that the code behaves as
designed while it is being built.
"""
from __future__ import annotations

from payroll_agent import scan_checks as sc


def row(emp, net, *, rid=None, name=None, run="R1"):
    return {"id": rid or f"row-{emp}", "employee_id": emp,
            "employee_name": name or f"Name {emp}", "net_pay": net, "payrun_id": run}


def person(emp, status="active", exit_date=None):
    return {"id": emp, "status": status, "exit_date": exit_date}


def test_payee_status_flags_each_condition_with_its_severity():
    employees = {
        "a": person("a", "left"), "b": person("b", "suspended"),
        "c": person("c", "active", "2026-08-15"), "d": person("d", "on_leave"),
        "e": person("e"), "f": person("f", "active", "2026-09-10"),
    }
    rows = [row(k, 1000) for k in "abcdef"]
    found = sc.payee_status(rows, employees, "2026-08-31")
    got = sorted((f["employee_id"], f["severity"]) for f in found)
    assert got == [("a", "high"), ("b", "high"), ("c", "high"), ("d", "info")]


def test_left_employee_with_exit_date_reports_both_conditions():
    found = sc.payee_status([row("a", 1000)], {"a": person("a", "left", "2026-08-01")}, "2026-08-31")
    assert len(found) == 2
    assert {f["evidence"].get("period_end") for f in found} == {None, "2026-08-31"}


def test_exit_date_timestamp_is_compared_on_its_date_part():
    found = sc.payee_status([row("a", 1000)], {"a": person("a", "active", "2026-08-15T00:00:00")}, "2026-08-31")
    assert [f["severity"] for f in found] == ["high"]


def test_payee_missing_from_employee_list_is_a_high_finding_not_a_crash():
    found = sc.payee_status([row("ghost", 1000)], {}, "2026-08-31")
    assert len(found) == 1
    assert found[0]["severity"] == "high"
    assert found[0]["evidence"] == {"reason": "not_in_employee_list"}


def test_net_pay_change_tiers_follow_threshold():
    prev = [row(k, 1000, run="R0") for k in "abcd"]
    cur = [row("a", 1600), row("b", 1350), row("c", 1200), row("d", 400)]
    found, info = sc.net_pay_change(cur, prev, 30)
    assert {f["employee_id"]: f["severity"] for f in found} == {"a": "high", "b": "medium", "d": "high"}
    assert info == {"new_in_run": 0, "not_comparable": 0}
    lower, _ = sc.net_pay_change(cur, prev, 10)
    assert {f["employee_id"]: f["severity"] for f in lower}["c"] == "medium"
    higher, _ = sc.net_pay_change(cur, prev, 60)
    assert {f["employee_id"] for f in higher} == {"a", "d"}


def test_net_pay_change_evidence_carries_both_values():
    found, _ = sc.net_pay_change([row("a", 500)], [row("a", 1000, run="R0")], 30)
    assert found[0]["evidence"] == {"previous_net": 1000, "current_net": 500, "change_pct": -50.0}


def test_net_pay_change_counts_new_and_uncomparable_without_dividing_by_zero():
    prev = [row("zero", 0, run="R0"), row("none", None, run="R0"),
            row("dup", 1000, run="R0"), row("dup", 1000, rid="dup-2", run="R0")]
    cur = [row("zero", 500), row("none", 500), row("dup", 500), row("fresh", 500)]
    found, info = sc.net_pay_change(cur, prev, 30)
    assert found == []
    assert info == {"new_in_run": 1, "not_comparable": 3}


def test_net_pay_sanity_flags_zero_negative_and_missing():
    found = sc.net_pay_sanity([row("a", 0), row("b", -5), row("c", None), row("d", 100)])
    assert sorted(f["employee_id"] for f in found) == ["a", "b", "c"]
    assert {f["severity"] for f in found} == {"high"}


def test_duplicate_payees_reports_row_ids():
    found = sc.duplicate_payees([row("a", 1, rid="r1"), row("a", 1, rid="r2"), row("b", 1)])
    assert len(found) == 1
    assert found[0]["evidence"] == {"occurrences": 2, "row_ids": ["r1", "r2"]}


def test_is_calculated():
    assert sc.is_calculated([row("a", 0), row("b", 10)])
    assert not sc.is_calculated([row("a", 0), row("b", None)])
    assert not sc.is_calculated([])


def test_run_checks_on_uncalculated_run_returns_nothing_to_check():
    result = sc.run_checks(rows=[row("a", 0)], prev_rows=[], employees=[person("a")], period_end="2026-09-30")
    assert result["calculated"] is False
    assert result["findings"] == []
    assert [s["check"] for s in result["skipped"]] == list(sc.CHECKS)
    assert {s["reason"] for s in result["skipped"]} == {"run not calculated"}
    assert result["counts"]["rows_checked"] == 1


def test_run_checks_without_comparison_skips_only_net_pay_change():
    result = sc.run_checks(rows=[row("a", 100)], prev_rows=None, employees=[person("a")],
                           period_end="2026-08-31", prev_reason="no earlier calculated regular run")
    assert result["calculated"] is True
    assert result["skipped"] == [{"check": "net_pay_change", "reason": "no earlier calculated regular run"}]
    assert set(result["counts"]["by_check"]) == {"payee_status", "net_pay_sanity", "duplicate_payees"}


def test_run_checks_sorts_by_severity_assigns_ids_and_counts():
    rows = [row("b", 100, name="Bea"), row("a", 100, name="Abe"), row("c", 100, name="Cy")]
    employees = [person("a", "on_leave"), person("b", "left"), person("c")]
    result = sc.run_checks(rows=rows, prev_rows=None, employees=employees, period_end="2026-08-31")
    assert [f["severity"] for f in result["findings"]] == ["high", "info"]
    assert [f["id"] for f in result["findings"]] == ["F-001", "F-002"]
    assert result["counts"]["by_severity"] == {"high": 1, "info": 1}


def test_run_checks_caps_findings_but_not_counts():
    rows = [row(f"e{i}", 100) for i in range(45)]
    employees = [person(f"e{i}", "left") for i in range(45)]
    result = sc.run_checks(rows=rows, prev_rows=None, employees=employees, period_end="2026-08-31")
    assert len(result["findings"]) == sc.MAX_FINDINGS == 40
    assert result["truncated"] is True
    assert result["counts"]["by_severity"] == {"high": 45}
    assert result["findings"][-1]["id"] == "F-040"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd harness && .venv/bin/python -m pytest tests/test_scan_checks_scaffold.py -v`
Expected: collection error / FAIL with `ImportError: cannot import name 'scan_checks'`.

- [ ] **Step 3: Write the implementation**

Create `harness/payroll_agent/scan_checks.py`:

```python
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


def is_calculated(rows: list[dict[str, Any]]) -> bool:
    """A run with no rows, or only zero/missing net pay, has not been calculated."""
    return any((row.get("net_pay") or 0) > 0 for row in rows)


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
        before, after = previous[key].get("net_pay"), current[key].get("net_pay")
        if not before or before <= 0 or after is None:
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
                              {"previous_net": before, "current_net": after, "change_pct": round(change, 1)},
                              f"Net pay {'up' if change > 0 else 'down'} {abs(round(change, 1))}% vs the comparison run"))
    return found, {"new_in_run": new_in_run, "not_comparable": not_comparable}


def net_pay_sanity(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_finding("net_pay_sanity", "high", row, {"net_pay": row.get("net_pay")},
                     "Net pay is zero, negative or missing")
            for row in rows if row.get("net_pay") is None or row["net_pay"] <= 0]


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd harness && .venv/bin/python -m pytest tests/test_scan_checks_scaffold.py -v`
Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add harness/payroll_agent/scan_checks.py harness/tests/test_scan_checks_scaffold.py
git commit -m "Add pure pre-payroll scan checks

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The capability and its worker

**Files:**
- Modify: `harness/payroll_agent/capabilities.py` (add one `Capability` to `default_registry`, after `list_payrun_employees`)
- Modify: `harness/payroll_agent/workers.py` (import, add `_fetch_all`, `_comparison_rows`, `run_pre_payroll_scan`, register in `_WORKERS`)
- Test: `harness/tests/test_scan_worker_scaffold.py`

**Interfaces:**
- Consumes (from Task 1): `scan_checks.run_checks`, `scan_checks.is_calculated`, `scan_checks.DEFAULT_CHANGE_PCT`.
- Consumes (existing): `workers._call_tool(ctx, name, arguments, jurisdiction) -> dict` (tool errors come back as `{"error": True, ...}`), `RunContext`, `TaskSpec(id, skill, input)`, `AgentSwitchToolError(tool, code, message, data=None)`.
- Produces (used by Tasks 3 and 4): capability name `pre_payroll_scan`; `workers.run_pre_payroll_scan(ctx, task) -> dict` returning the result shape in the spec (`payrun_id`, `run_status`, `calculated`, `compared_to`, `skipped`, `counts`, `findings`, `truncated`), or `{"error": True, "tool": "pre_payroll_scan", "code": "scan_incomplete", "message": ...}`.

- [ ] **Step 1: Write the failing tests**

Create `harness/tests/test_scan_worker_scaffold.py`:

```python
"""Implementation scaffolding for the pre_payroll_scan capability and worker.

NOT the team's graded tests. A fake AgentSwitch client stands in for the
platform so the worker's fetching, paging and comparison-run choice can be
checked offline while it is being built.
"""
from __future__ import annotations

import pytest

from payroll_agent import workers
from payroll_agent.agentswitch import AgentSwitchToolError
from payroll_agent.capabilities import CapabilityError, default_registry
from payroll_agent.core.live_graph import TaskSpec


class FakeAgentSwitch:
    def __init__(self, *, runs, slips, employees, page_cap=100, short_total=None):
        self.runs = {run["id"]: run for run in runs}
        self.slips, self.employees = slips, employees
        self.page_cap, self.short_total = page_cap, short_total
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name, arguments=None, *, jurisdiction):
        arguments = arguments or {}
        self.calls.append((name, arguments))
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
        raise AssertionError(f"scan must stay read-only; unexpected tool {name}")

    def _page(self, rows, arguments):
        offset, limit = arguments.get("offset", 0), min(arguments.get("limit", 10), self.page_cap)
        total = len(rows) if self.short_total is None else self.short_total
        return {"data": rows[offset:offset + limit], "limit": limit, "offset": offset, "total": total}


def run(rid, start, end, *, status="paid", run_type="regular"):
    return {"id": rid, "pay_period_start": start, "pay_period_end": end, "status": status, "run_type": run_type}


def slip(rid, emp, net, name=None):
    return {"id": f"{rid}-{emp}", "payrun_id": rid, "employee_id": emp,
            "employee_name": name or emp.upper(), "net_pay": net}


def person(emp, status="active", exit_date=None):
    return {"id": emp, "status": status, "exit_date": exit_date}


def scan(fake, **extra):
    ctx = workers.RunContext(run_id="r", store=None, llm=None, agentswitch=fake)
    task = TaskSpec("scan", "pre_payroll_scan", {"jurisdiction": "IN", "payrun_id": "R3", **extra})
    return workers.run_pre_payroll_scan(ctx, task)


def standard_fake(**overrides):
    kwargs = dict(
        runs=[run("R1", "2026-06-01", "2026-06-30"), run("R2", "2026-07-01", "2026-07-31"),
              run("R3", "2026-08-01", "2026-08-31", status="pending_approval"),
              run("RX", "2026-07-15", "2026-07-31", run_type="bonus"),
              run("RC", "2026-07-02", "2026-07-31", status="cancelled"),
              run("R4", "2026-09-01", "2026-09-30", status="draft")],
        slips={"R1": [slip("R1", "a", 1000)], "R2": [slip("R2", "a", 1000), slip("R2", "b", 1000)],
               "R3": [slip("R3", "a", 400), slip("R3", "b", 1000), slip("R3", "c", 1000)],
               "RX": [slip("RX", "a", 5)], "RC": [slip("RC", "a", 7)]},
        employees=[person("a"), person("b", "left"), person("c")])
    kwargs.update(overrides)
    return FakeAgentSwitch(**kwargs)


async def test_scan_compares_to_latest_earlier_regular_calculated_run():
    fake = standard_fake()
    result = await scan(fake)
    assert result["compared_to"] == "R2"            # not the bonus, cancelled or later runs
    assert result["calculated"] is True and result["run_status"] == "pending_approval"
    assert result["counts"]["new_in_run"] == 1      # employee c is new
    kinds = {(f["check"], f["employee_id"]) for f in result["findings"]}
    assert ("net_pay_change", "a") in kinds and ("payee_status", "b") in kinds


async def test_scan_only_reads():
    fake = standard_fake()
    await scan(fake)
    assert {name for name, _ in fake.calls} <= {"PayRun.get", "PayRun.list", "PayRunEmployee.list", "Employee.list"}


async def test_uncalculated_run_is_reported_not_calculated():
    fake = standard_fake(slips={"R3": [slip("R3", "a", 0), slip("R3", "b", 0)]})
    result = await scan(fake)
    assert result["calculated"] is False and result["findings"] == []
    assert result["compared_to"] is None
    assert [n for n, _ in fake.calls].count("PayRunEmployee.list") == 1   # no comparison fetch


async def test_no_earlier_calculated_run_skips_only_the_change_check():
    fake = standard_fake(runs=[run("R3", "2026-08-01", "2026-08-31")],
                         slips={"R3": [slip("R3", "a", 400), slip("R3", "b", 1000)]})
    result = await scan(fake)
    assert result["compared_to"] is None
    assert result["skipped"] == [{"check": "net_pay_change", "reason": "no earlier calculated regular run"}]
    assert "payee_status" in result["counts"]["by_check"]


async def test_explicit_compare_to_is_used():
    fake = standard_fake()
    result = await scan(fake, compare_to="R1")
    assert result["compared_to"] == "R1"


async def test_threshold_argument_changes_the_result():
    fake = standard_fake(slips={"R2": [slip("R2", "a", 1000)], "R3": [slip("R3", "a", 1200)]},
                         employees=[person("a")])
    default = await scan(fake)
    lower = await scan(standard_fake(slips={"R2": [slip("R2", "a", 1000)], "R3": [slip("R3", "a", 1200)]},
                                     employees=[person("a")]), change_threshold_pct=10)
    assert default["counts"]["by_check"]["net_pay_change"] == 0
    assert lower["counts"]["by_check"]["net_pay_change"] == 1


async def test_payee_missing_from_employee_list_is_a_finding():
    fake = standard_fake(employees=[person("a"), person("b")])      # c is not listed
    result = await scan(fake)
    assert any(f["employee_id"] == "c" and f["evidence"] == {"reason": "not_in_employee_list"}
               for f in result["findings"])


async def test_paging_collects_every_row():
    rows = [slip("R3", f"e{i}", 100) for i in range(5)]
    fake = standard_fake(slips={"R3": rows}, employees=[person(f"e{i}") for i in range(5)], page_cap=2)
    result = await scan(fake)
    assert result["counts"]["rows_checked"] == 5


async def test_short_fetch_of_own_rows_is_scan_incomplete():
    fake = standard_fake(short_total=50)
    result = await scan(fake)
    assert result["error"] is True and result["code"] == "scan_incomplete"


async def test_tool_error_on_payrun_get_becomes_an_error_result():
    fake = standard_fake()
    ctx = workers.RunContext(run_id="r", store=None, llm=None, agentswitch=fake)
    task = TaskSpec("scan", "pre_payroll_scan", {"jurisdiction": "IN", "payrun_id": "missing"})
    result = await workers.run_pre_payroll_scan(ctx, task)
    assert result["error"] is True and result["tool"] == "PayRun.get"


def test_capability_is_registered_read_only_with_id_provenance():
    registry = default_registry()
    capability = registry.get("pre_payroll_scan")
    assert capability.side_effect is False
    assert "pre_payroll_scan" in registry.family("evidence")
    assert "pre_payroll_scan" in workers._WORKERS
    clean = registry.validate("pre_payroll_scan", {"jurisdiction": "IN", "payrun_id": "R3"}, known_values={"R3"})
    assert clean["change_threshold_pct"] == 30
    with pytest.raises(CapabilityError):
        registry.validate("pre_payroll_scan", {"jurisdiction": "IN", "payrun_id": "R3"}, known_values=set())
    for bad in (0, 101):
        with pytest.raises(CapabilityError):
            registry.validate("pre_payroll_scan",
                              {"jurisdiction": "IN", "payrun_id": "R3", "change_threshold_pct": bad},
                              known_values={"R3"})
```

Note: `test_threshold_argument_changes_the_result` builds two fakes because each `FakeAgentSwitch` records its own calls; the first is used for the default-threshold run.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd harness && .venv/bin/python -m pytest tests/test_scan_worker_scaffold.py -v`
Expected: FAIL with `AttributeError: module 'payroll_agent.workers' has no attribute 'run_pre_payroll_scan'` (and the capability test failing on an unknown capability).

- [ ] **Step 3: Add the capability**

In `harness/payroll_agent/capabilities.py`, inside `default_registry()`, insert immediately after the `list_payrun_employees` `Capability(...)` block (the one ending `families=("evidence",),\n        ),` before `Capability(\n            "run_payroll",`):

```python
        Capability(
            "pre_payroll_scan",
            "Check one calculated PayRun for problems before it is submitted: employees paid "
            "who have left, are suspended or have an exit date on or before the period end; "
            "large month-over-month net-pay changes against the previous calculated run; zero, "
            "negative or missing net pay; and duplicate payees. Read-only. Returns findings "
            "with severity and evidence, plus counts. If the run has not been calculated it "
            "says so and finds nothing; it never calculates the run. Use a `payrun_id` taken "
            "from an earlier outcome (for example `list_payruns`).",
            {"jurisdiction": _JURISDICTION,
             "payrun_id": string("The PayRun to check.", maximum=200, format="id"),
             "compare_to": string("PayRun id to compare against; omit to use the most recent "
                                  "earlier calculated regular run.",
                                  required=False, maximum=200, format="id"),
             "change_threshold_pct": integer("Flag net-pay changes of at least this many percent "
                                             "as medium severity (50 or more is always high).",
                                             required=False, default=30, minimum=1, maximum=100)},
            families=("evidence",),
        ),
```

- [ ] **Step 4: Add the worker**

In `harness/payroll_agent/workers.py`:

(a) Change the import block: after `from .agentswitch import AgentSwitchClient, AgentSwitchToolError` add
```python
from . import scan_checks
```

(b) Insert before the `# run_payroll rewrites the slips` comment block (after `run_list_payrun_employees`):

```python
_PAGE_SIZE = 100
_MAX_PAGES = 20
_MAX_COMPARE_CANDIDATES = 3


async def _fetch_all(ctx: RunContext, tool: str, args: dict[str, Any],
                     jurisdiction: str) -> tuple[list[dict[str, Any]], str | None]:
    """Every row of a list tool, or (rows so far, reason) when it failed or came up short."""
    rows: list[dict[str, Any]] = []
    total: int | None = None
    for _ in range(_MAX_PAGES):
        page = await _call_tool(ctx, tool, {**args, "limit": _PAGE_SIZE, "offset": len(rows)}, jurisdiction)
        if page.get("error"):
            return rows, f"{tool} failed: {page.get('message')}"
        data = page.get("data")
        if not isinstance(data, list):
            return rows, f"{tool} returned no row list"
        rows.extend(data)
        total = page.get("total", total)
        if not data or total is None or len(rows) >= total:
            break
    if total is not None and len(rows) < total:
        return rows, f"{tool} incomplete: fetched {len(rows)} of {total} rows"
    return rows, None


async def _comparison_rows(ctx: RunContext, run: dict[str, Any], explicit: str | None,
                           jurisdiction: str) -> tuple[str | None, list[dict[str, Any]] | None, str]:
    """(comparison run id, its rows, reason when there are none)."""
    none_reason = "no earlier calculated regular run"
    if explicit:
        rows, problem = await _fetch_all(ctx, "PayRunEmployee.list", {"payrun_id": explicit}, jurisdiction)
        if problem:
            return explicit, None, problem
        if not scan_checks.is_calculated(rows):
            return explicit, None, "comparison run has no calculated rows"
        return explicit, rows, none_reason
    start = str(run.get("pay_period_start") or "")[:10]
    if not start:
        return None, None, "run has no pay_period_start to compare from"
    listing = await _call_tool(ctx, "PayRun.list", {"limit": _PAGE_SIZE}, jurisdiction)
    if listing.get("error"):
        return None, None, f"PayRun.list failed: {listing.get('message')}"
    data = listing.get("data", [])
    if listing.get("total", 0) > len(data):
        return None, None, "more PayRun records exist than could be checked"
    earlier = sorted((r for r in data if r.get("run_type") == "regular" and r.get("status") != "cancelled"
                      and r.get("id") != run.get("id") and str(r.get("pay_period_start") or "")[:10] < start),
                     key=lambda r: str(r["pay_period_start"]), reverse=True)
    for candidate in earlier[:_MAX_COMPARE_CANDIDATES]:
        rows, problem = await _fetch_all(ctx, "PayRunEmployee.list", {"payrun_id": candidate["id"]}, jurisdiction)
        if problem:
            return candidate["id"], None, problem
        if scan_checks.is_calculated(rows):
            return candidate["id"], rows, none_reason
    return None, None, none_reason


def _scan_incomplete(problem: str) -> dict[str, Any]:
    return {"error": True, "tool": "pre_payroll_scan", "code": "scan_incomplete", "message": problem}


async def run_pre_payroll_scan(ctx: RunContext, task: TaskSpec) -> dict[str, Any]:
    """Read-only: fetch, then let scan_checks judge. Never calls a mutating tool."""
    jurisdiction, payrun_id = task.input["jurisdiction"], task.input["payrun_id"]
    run = await _call_tool(ctx, "PayRun.get", {"id": payrun_id}, jurisdiction)
    if run.get("error"):
        return run
    rows, problem = await _fetch_all(ctx, "PayRunEmployee.list", {"payrun_id": payrun_id}, jurisdiction)
    if problem:
        return _scan_incomplete(problem)
    employees, problem = await _fetch_all(ctx, "Employee.list", {}, jurisdiction)
    if problem:
        return _scan_incomplete(problem)
    compared_to: str | None = None
    prev_rows: list[dict[str, Any]] | None = None
    prev_reason = "no earlier calculated regular run"
    if scan_checks.is_calculated(rows):
        compared_to, prev_rows, prev_reason = await _comparison_rows(
            ctx, run, task.input.get("compare_to"), jurisdiction)
        if prev_rows is None:
            compared_to = None
    result = scan_checks.run_checks(
        rows=rows, prev_rows=prev_rows, employees=employees, period_end=run.get("pay_period_end"),
        threshold_pct=task.input.get("change_threshold_pct", scan_checks.DEFAULT_CHANGE_PCT),
        prev_reason=prev_reason)
    return {"payrun_id": payrun_id, "run_status": run.get("status"), "compared_to": compared_to, **result}
```

(c) In the `_WORKERS` dict add, after the `"list_payrun_employees"` line:
```python
    "pre_payroll_scan": run_pre_payroll_scan,
```

- [ ] **Step 5: Run the tests to verify they pass, then the whole suite**

Run: `cd harness && .venv/bin/python -m pytest tests/test_scan_worker_scaffold.py -v`
Expected: 11 passed.

Run: `cd harness && .venv/bin/python -m pytest -q`
Expected: all pass (the existing 27 plus 14 from Task 1 plus these 11). If a pre-existing test asserts the capability count is 7, update that number to 8 and say so in the commit message.

- [ ] **Step 6: Commit**

```bash
git add harness/payroll_agent/capabilities.py harness/payroll_agent/workers.py harness/tests/test_scan_worker_scaffold.py
git commit -m "Add read-only pre_payroll_scan capability and worker

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `node_result` verifier

**Files:**
- Modify: `harness/evals/verifiers.py` (append one verifier)
- Test: `harness/tests/test_node_result_scaffold.py`

**Interfaces:**
- Consumes: `evals.verifiers.verifier`, `VerifyContext`, `_result`, `_matches`. The run record shape from `evals/runner.py`: `ctx.record["result"]["nodes"]` is `{node_id: {"skill", "state", "input", "result"}}`.
- Produces (used by Task 4 and by the team's graded tasks): verifier type `node_result` with params `skill` (capability name), `path` (dotted path into the node's result, list indices allowed), `expect` (bare value or `{"op", "value"}`), optional `claim`.

- [ ] **Step 1: Write the failing tests**

Create `harness/tests/test_node_result_scaffold.py`:

```python
"""Implementation scaffolding for the node_result verifier in evals/verifiers.py.

NOT the team's graded tests.
"""
from __future__ import annotations

from evals.verifiers import REGISTRY, VerifyContext


def ctx_with(nodes):
    return VerifyContext(task={}, record={"result": {"nodes": nodes}}, before=[], after=[],
                         client=None, jurisdiction="IN")


def node(skill, result, state="succeeded"):
    return {"skill": skill, "state": state, "input": {}, "result": result}


async def check(nodes, **params):
    return await REGISTRY["node_result"](ctx_with(nodes), params)


async def test_passes_on_a_nested_value_with_an_op():
    nodes = {"scan": node("pre_payroll_scan", {"counts": {"by_severity": {"high": 3}}})}
    out = await check(nodes, skill="pre_payroll_scan", path="counts.by_severity.high",
                      expect={"op": "gte", "value": 1})
    assert out["ok"] is True and out["observed"] == 3


async def test_path_can_index_into_lists():
    nodes = {"scan": node("pre_payroll_scan", {"findings": [{"check": "payee_status"}]})}
    out = await check(nodes, skill="pre_payroll_scan", path="findings.0.check", expect="payee_status")
    assert out["ok"] is True


async def test_fails_when_the_path_is_missing():
    nodes = {"scan": node("pre_payroll_scan", {"counts": {}})}
    out = await check(nodes, skill="pre_payroll_scan", path="counts.by_severity.high", expect=1)
    assert out["ok"] is False and out["observed"] is None


async def test_fails_when_no_succeeded_node_ran():
    nodes = {"scan": node("pre_payroll_scan", None, state="failed")}
    out = await check(nodes, skill="pre_payroll_scan", path="calculated", expect=True)
    assert out["ok"] is False
    assert out["observed"] == {"succeeded_nodes": 0}


async def test_uses_the_last_succeeded_node_of_that_skill():
    nodes = {"one": node("pre_payroll_scan", {"calculated": False}),
             "two": node("pre_payroll_scan", {"calculated": True})}
    out = await check(nodes, skill="pre_payroll_scan", path="calculated", expect=True)
    assert out["ok"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd harness && .venv/bin/python -m pytest tests/test_node_result_scaffold.py -v`
Expected: FAIL with `KeyError: 'node_result'`.

- [ ] **Step 3: Write the implementation**

Append to `harness/evals/verifiers.py` (after the `refusal` verifier):

```python
def _dig(value: Any, path: str) -> Any:
    """Follow a dotted path through dicts and lists; None when any step is missing."""
    for step in path.split("."):
        if isinstance(value, dict):
            value = value.get(step)
        elif isinstance(value, list) and step.isdigit() and int(step) < len(value):
            value = value[int(step)]
        else:
            return None
    return value


@verifier("node_result")
async def node_result(ctx: VerifyContext, params: dict[str, Any]) -> dict[str, Any]:
    """A value inside a capability's own result, not the final prose.

    params: ``skill`` (capability name), ``path`` (dotted, e.g. ``counts.by_severity.high``),
    ``expect`` (a value, or ``{"op", "value"}``). Uses the last succeeded node of that skill.
    """
    nodes = (ctx.record.get("result") or {}).get("nodes", {})
    matching = [n for n in nodes.values() if n.get("skill") == params["skill"] and n.get("state") == "succeeded"]
    claim = params.get("claim") or f"{params['skill']} result {params['path']} matches {params['expect']}"
    if not matching:
        return _result(claim, False, {"succeeded_nodes": 0})
    observed = _dig(matching[-1].get("result"), params["path"])
    return _result(claim, _matches(observed, params["expect"]), observed)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd harness && .venv/bin/python -m pytest tests/test_node_result_scaffold.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add harness/evals/verifiers.py harness/tests/test_node_result_scaffold.py
git commit -m "Add node_result verifier for asserting on a capability's result

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Scaffold eval tasks, live check on the real tenant, docs

**Files:**
- Create: `harness/evals/scripts/scaffold_scan_clean_pair.json`
- Create: `harness/evals/scripts/scaffold_scan_uncalculated.json`
- Modify: `harness/evals/tasks/scaffold.jsonl` (append two lines)
- Modify: `harness/WORKFLOWS.md` (family C status lines)
- Modify: `harness/NEXT_STEPS.md` (roadmap item 2, findings from the live check)

**Interfaces:**
- Consumes: Task 2's capability (`pre_payroll_scan`), Task 3's `node_result`, the existing scripted transport and runner. The real ids below come from the 2026-09-30 probe of the India tenant: July `PRUN-2026-00011` = `dd9bf1ea-eab9-4f93-b91f-7b6721ce4d0b`; September draft `PRUN-2026-00013` = `b5b0cc7d-7aef-4738-a6e0-bfaa01c94aaa`.
- Produces: two scaffold eval tasks that exercise the scan end to end with no LLM, and updated docs.

- [ ] **Step 1: Write the two scripts**

Create `harness/evals/scripts/scaffold_scan_clean_pair.json` (scaffold only; July scanned against June, both untouched by our earlier overwrite):

```json
{
  "name": "scaffold_scan_clean_pair",
  "note": "Scaffold only. Look up July 2026, scan it (auto-compared to June), answer. Read-only.",
  "steps": [
    {"reply": {"add": [{"id": "lookup", "capability": "list_payruns",
                        "arguments": {"jurisdiction": "IN", "month": "2026-07"}, "depends_on": []}],
               "cancel": [], "finish": false, "reason": "find the July 2026 run"}},
    {"reply": {"add": [{"id": "scan", "capability": "pre_payroll_scan",
                        "arguments": {"jurisdiction": "IN", "payrun_id": "dd9bf1ea-eab9-4f93-b91f-7b6721ce4d0b"},
                        "depends_on": ["lookup"]}],
               "cancel": [], "finish": false, "reason": "scan the July run"}},
    {"reply": {"add": [{"id": "answer", "capability": "answer_with_evidence",
                        "arguments": {"query": "Check the July 2026 payroll run before we submit it."},
                        "depends_on": ["scan"]}],
               "cancel": [], "finish": false, "reason": "scan finished; answer from it"}},
    {"reply": {"ready": true, "missing": [], "reason": "scan result is present"}},
    {"reply": "Scripted answer: see the pre_payroll_scan evidence."}
  ]
}
```

Create `harness/evals/scripts/scaffold_scan_uncalculated.json`:

```json
{
  "name": "scaffold_scan_uncalculated",
  "note": "Scaffold only. Scan the September draft, which has no calculated net pay. Read-only.",
  "steps": [
    {"reply": {"add": [{"id": "lookup", "capability": "list_payruns",
                        "arguments": {"jurisdiction": "IN", "month": "2026-09"}, "depends_on": []}],
               "cancel": [], "finish": false, "reason": "find the September 2026 run"}},
    {"reply": {"add": [{"id": "scan", "capability": "pre_payroll_scan",
                        "arguments": {"jurisdiction": "IN", "payrun_id": "b5b0cc7d-7aef-4738-a6e0-bfaa01c94aaa"},
                        "depends_on": ["lookup"]}],
               "cancel": [], "finish": false, "reason": "scan the September run"}},
    {"reply": {"add": [{"id": "answer", "capability": "answer_with_evidence",
                        "arguments": {"query": "Check the September 2026 payroll run before we submit it."},
                        "depends_on": ["scan"]}],
               "cancel": [], "finish": false, "reason": "scan finished; answer from it"}},
    {"reply": {"ready": true, "missing": [], "reason": "scan reports the run is not calculated"}},
    {"reply": "Scripted answer: the September run is not calculated, so there is nothing to check yet."}
  ]
}
```

- [ ] **Step 2: Append the two scaffold tasks**

Append these two lines to `harness/evals/tasks/scaffold.jsonl` (one JSON object per line, no blank line between):

```json
{"id":"scaffold_scripted_scan_clean_pair","family":"check","authored_by":"scaffold","goal":"Check the July 2026 payroll run before we submit it.","jurisdiction":"IN","allow":[],"mode":"dry_run","transport":"scripted","script":"scripts/scaffold_scan_clean_pair.json","watch":[{"tool":"PayRun.list","args":{"limit":100},"key":"id","fields":["status","pay_period_start"]}],"verifiers":[{"type":"tool_called","tool":"PayRunEmployee.list","min":2},{"type":"tool_not_called","tool":"PayRun.calculate_payroll"},{"type":"tool_not_called","tool":"PayRun.run_payroll"},{"type":"no_mutation"},{"type":"node_result","skill":"pre_payroll_scan","path":"calculated","expect":true}]}
{"id":"scaffold_scripted_scan_uncalculated","family":"check","authored_by":"scaffold","goal":"Check the September 2026 payroll run before we submit it.","jurisdiction":"IN","allow":[],"mode":"dry_run","transport":"scripted","script":"scripts/scaffold_scan_uncalculated.json","watch":[{"tool":"PayRun.list","args":{"limit":100},"key":"id","fields":["status","pay_period_start"]}],"verifiers":[{"type":"tool_not_called","tool":"PayRun.calculate_payroll"},{"type":"no_mutation"},{"type":"node_result","skill":"pre_payroll_scan","path":"calculated","expect":false},{"type":"node_result","skill":"pre_payroll_scan","path":"findings","expect":[]}]}
```

- [ ] **Step 3: Run both scaffold tasks against the real India tenant**

Run: `cd harness && .venv/bin/python -m evals.runner --tasks evals/tasks/scaffold.jsonl --task-id scaffold_scripted_scan_clean_pair`
Expected: result `pass`, exit code 0. If it reports `fail`, read `evals/runs/<latest>/scaffold_scripted_scan_clean_pair.score.json` and fix the cause; do not loosen the verifier to make it pass.

Run: `cd harness && .venv/bin/python -m evals.runner --tasks evals/tasks/scaffold.jsonl --task-id scaffold_scripted_scan_uncalculated`
Expected: `pass`, exit code 0.

- [ ] **Step 4: Inspect what the scan found on the real data**

Run:
```bash
cd harness && .venv/bin/python - <<'EOF'
import json, glob
for name in ("scaffold_scripted_scan_clean_pair", "scaffold_scripted_scan_uncalculated"):
    path = sorted(glob.glob(f"evals/runs/*/{name}.json"))[-1]
    rec = json.load(open(path))
    node = next(n for n in rec["result"]["nodes"].values() if n["skill"] == "pre_payroll_scan")["result"]
    print(name, "| calculated:", node["calculated"], "| compared_to:", node["compared_to"],
          "| counts:", node["counts"], "| skipped:", node["skipped"])
    for f in node["findings"][:5]:
        print("   ", f["id"], f["severity"], f["check"], f["employee_name"], f["evidence"])
EOF
```
Expected: the July run shows `calculated: True`, `compared_to` set to the June run's id, and real counts; the September run shows `calculated: False` with all four checks skipped. Record the actual counts (they are real data, do not guess them) for Step 5.

- [ ] **Step 5: Update the docs**

In `harness/WORKFLOWS.md`, section C, replace the line `Tools: \`Employee\`, \`PayRunEmployee\`, ... Status: todo.` with:
```
Status: **partial**. `pre_payroll_scan` covers payee status, month-over-month
net-pay change, net-pay sanity and duplicate payees, read-only. Duplicate bank
accounts and missing PAN/UAN/ESI are blocked by field redaction for this seat;
joiner/leaver proration and pending leave/attendance items are not built.
Design: `docs/superpowers/specs/2026-09-30-pre-payroll-scan-design.md`.
```

In `harness/NEXT_STEPS.md`, under "Workflow roadmap", change item 2 to `[x]` with a one-line result that includes the real numbers printed in Step 4, and add under "Near-term" a line: `- [ ] Live LLM run of "check the July/September run" once Gemini recovers (scripted runs pass).`

- [ ] **Step 6: Run the full suite and commit**

Run: `cd harness && .venv/bin/python -m pytest -q`
Expected: all pass.

```bash
git add harness/evals harness/WORKFLOWS.md harness/NEXT_STEPS.md
git commit -m "Add scaffold eval tasks for pre_payroll_scan; update docs

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-review (run against the spec)

- **Purpose / success criteria:** Task 2 worker and Task 4 live runs (calculated and uncalculated).
- **Components:** `scan_checks.py` (Task 1), capability and worker (Task 2), `node_result` (Task 3). Matches the spec's component list.
- **Arguments table:** `jurisdiction`, `payrun_id`, `compare_to`, `change_threshold_pct` with default 30 and range 1 to 100: Task 2 capability and its validation test.
- **Checks table:** status (left, suspended, exit date, on_leave, not in employee list), change tiers, sanity, duplicates: Task 1 tests, one per row; the "both conditions separately" note has its own test.
- **Result shape and cap:** Task 1 (`run_checks`, cap, truncation, ids, counts including `not_comparable`).
- **Not calculated:** Task 1 (`run_checks`), Task 2 (no comparison fetch), Task 4 (live September draft).
- **Comparison-run choice:** Task 2 test (skips bonus, cancelled and later runs; explicit override).
- **Error handling:** `scan_incomplete` and tool-error pass-through (Task 2); skipped-comparison on short comparison fetch is handled in `_comparison_rows` (a short fetch returns `problem`, which `run_pre_payroll_scan` passes as `prev_reason` with `prev_rows=None`).
- **Read-only:** Task 2 `test_scan_only_reads` (the fake raises on any other tool); Task 4 `tool_not_called` and `no_mutation` verifiers.
- **Testing and grading split:** every test file is labelled scaffold; eval tasks use `authored_by: "scaffold"`; no graded task is written.
- **Limitations:** recorded in WORKFLOWS.md (Task 4 Step 5).
- **Type consistency:** `run_checks(*, rows, prev_rows, employees, period_end, threshold_pct, prev_reason)`, `net_pay_change -> (list, dict)`, `_comparison_rows -> (id|None, rows|None, reason)` are used identically in Tasks 1, 2 and 4.
- **Placeholders:** none; the only values filled in at execution time are the real counts in Task 4 Step 5, which come from running Step 4.

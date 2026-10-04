# Statutory Dues and Lifecycle Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two read-only capabilities: `statutory_dues` (what statutory dues a payroll run creates, India and US employee-side) and `lifecycle_report` (loans, salary revisions, final settlements, investments).

**Architecture:** Two pure modules (`statutory.py`, `lifecycle.py`) compute everything from plain row lists. Two thin workers fetch (reusing `_fetch_all`) and hand over. The LLM plans one call and explains the result.

**Tech Stack:** Python 3, asyncio, pytest (`asyncio_mode = "auto"`), the existing `AgentSwitchClient`, live graph, planner and `evals/` harness. Run everything from `harness/` with `.venv/bin/python`.

**Spec:** `docs/superpowers/specs/2026-10-04-statutory-and-lifecycle-reads-design.md`

## Global Constraints

- Both capabilities are read-only: only `.get` and `.list` AgentSwitch calls, no `side_effect`.
- India statute rows (amount = slip sum, cross-checked with the header total) come from this table of `(statute, side, slip field, header field, due key)`: EPF/employee/`epf_employee`/`total_epf_employee`/epf; EPF/employer/`epf_employer`/`total_epf_employer`/epf; EPS/employer/`eps_employer`/`total_eps_employer`/epf; EDLI/employer/`edli_employer`/`total_edli_employer`/epf; EPF admin charges/employer/`epf_admin_charges`/`total_epf_admin_charges`/epf; ESI/employee/`esi_employee`/`total_esi_employee`/esi; ESI/employer/`esi_employer`/`total_esi_employer`/esi; Professional tax/employee/`professional_tax`/`total_pt`/none; TDS/employee/`tds`/`total_tds`/tds; LWF/employee/`lwf_employee`/`total_lwf_employee`/none; LWF/employer/`lwf_employer`/`total_lwf_employer`/none. A row is listed only when its slip sum or its header total is non-zero. Money tolerance 1.0.
- Due dates (India only, from the pay period end, labelled `standard India calendar, not from AgentSwitch`): TDS the 7th of the following month (30 April for a March period); EPF and ESI the 15th of the following month. Professional tax and LWF get no date. Status is `overdue`, `due_today` or `upcoming` against today, with `days_left`. The US gets no due dates.
- US rows are summed from each slip's `deductions` list by component name: contains "social security" -> Social Security; contains "medicare" -> Medicare; contains "federal" -> Federal income tax; contains "state" and "withholding" -> State income tax; anything else is non-statutory. Four employer rows (Social Security (employer match), Medicare (employer match), FUTA, SUTA) have `available: false`, `amount: null`, note `not in AgentSwitch data for this seat`. US consistency compares the sum of all deduction components with the header `total_deductions`. US rate check: Social Security 6.2% and Medicare 1.45% of total gross, within 0.5 points, labelled as a public statutory rate.
- A run that is not calculated (no rows, or no net pay above 0) returns `calculated: false` with the period and status only, and `skipped` = `[{"what": "report", "reason": "run not calculated"}]`; India configs are not fetched in that case.
- A present-but-unusable amount (including `nan`/`inf`) counts as 0 and is reported once under `skipped` as `{"what": "amounts", "reason": "<n> unusable amount(s) counted as 0"}`.
- Lists are capped (25 items or people, 10 examples, 12 months) and counts are always the full numbers.
- A tool error on a fetch returns the tool's own error result; a short fetch returns `scan_incomplete` with `tool` set to the capability name. A failing India config fetch only skips that config (a `skipped` entry), never the dues.
- Graded tests are hand-written by the team; a test written by Claude scores zero. Every test file here says it is implementation scaffolding and not a graded submission; scaffold eval tasks use `authored_by: "scaffold"`.
- **Git:** work on branch `statutory-and-lifecycle-reads`. Commit with the repo's default identity (Pragathi Kalidasan Vetrivel Murugan, from the global git config), ending each message with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Push **only that branch** as the active `gh` account `pragsyy1729` (`gh api user --jq .login` must print it first), never `main`, and open no pull request.
- Match existing style: `from __future__ import annotations`, a module docstring stating purpose, comments only where the why is not obvious.

## Review Focus

Inputs the spec implies that a happy-path test would miss, most likely first:

1. A header total that disagrees with the slip sums, or is missing: reported or `null`, never a silent "matches" (Task 1).
2. A US slip whose `deductions` is not a list, or holds non-dict items or unusable amounts: never a crash, and unusable amounts are counted (Task 1).
3. Loan rows with non-numeric, negative or missing values and implausible dates: flagged or counted, never a `TypeError` (Task 2).
4. A date that cannot be parsed (empty, year 0): treated as unusable for that check, never a `ValueError` (Task 2).
5. A failing config fetch, a denied `Employee.list`, a short fetch and an uncalculated run: each ends visibly and correctly (Task 3).

---

### Task 1: Pure statutory calculations

**Files:**
- Create: `harness/payroll_agent/statutory.py`
- Test: `harness/tests/test_statutory_scaffold.py`

**Interfaces:**
- Consumes (existing): `payroll_agent.cost_report._finite` (numeric coercion, rejects nan/inf) and `scan_checks.is_calculated(rows) -> bool`.
- Produces (used by Task 3): `due_dates(period_end: str | None, today: date) -> dict`, and `build_statutory(*, run, rows, jurisdiction, configs, today) -> dict` where `configs` is `{"EPFConfig": [rows], "ESIConfig": [...], "PTConfig": [...], "LWFConfig": [...]}` (a missing key means "not fetched").

- [ ] **Step 1: Write the failing tests**

Create `harness/tests/test_statutory_scaffold.py`:

```python
"""Implementation scaffolding for payroll_agent/statutory.py.

NOT the team's graded tests. These only check that the code behaves as
designed while it is being built.
"""
from __future__ import annotations

from datetime import date

from payroll_agent import statutory as st

TODAY = date(2026, 8, 3)


def slip(**fields):
    return {"employee_id": "a", "employee_name": "A", "gross_pay": 1000.0, "net_pay": 900.0,
            "esi_covered": 0, **fields}


ROWS_IN = [slip(epf_employee=100, epf_employer=100, esi_employee=5, esi_employer=20,
                professional_tax=200, tds=50),
           slip(employee_id="b", epf_employee=200, epf_employer=200, professional_tax=200, tds=0)]
RUN_IN = {"id": "R1", "status": "paid", "pay_period_start": "2026-07-01", "pay_period_end": "2026-07-31",
          "total_epf_employee": 300, "total_epf_employer": 300, "total_esi_employee": 5,
          "total_esi_employer": 20, "total_pt": 400, "total_tds": 50}
CONFIGS = {
    "EPFConfig": [{"employee_contribution_rate": 12.0, "employer_contribution_rate": 12.0, "eps_rate": 8.33,
                   "eps_wage_ceiling": 15000.0, "edli_rate": 0.5, "edli_wage_ceiling": 15000.0,
                   "admin_charges_rate": 0.5}],
    "ESIConfig": [{"employee_contribution_rate": 0.75, "employer_contribution_rate": 3.25, "wage_ceiling": 21000.0}],
    "PTConfig": [{"deduction_cycle": "monthly", "annual_cap": 2500.0}],
    "LWFConfig": [{"deduction_cycle": "half_yearly", "employee_contribution": 25.0,
                   "employer_contribution": 75.0, "state": "Maharashtra"}],
}


def india(rows=ROWS_IN, run=RUN_IN, configs=CONFIGS):
    return st.build_statutory(run=run, rows=rows, jurisdiction="IN", configs=configs, today=TODAY)


def test_due_dates_for_a_july_period():
    dues = st.due_dates("2026-07-31", TODAY)
    assert dues["tds"]["date"] == "2026-08-07" and dues["tds"]["status"] == "upcoming"
    assert dues["tds"]["days_left"] == 4
    assert dues["epf"]["date"] == dues["esi"]["date"] == "2026-08-15" and dues["epf"]["days_left"] == 12
    assert dues["tds"]["basis"] == "standard India calendar, not from AgentSwitch"


def test_due_dates_for_march_and_december_periods():
    assert st.due_dates("2026-03-31", date(2026, 4, 1))["tds"]["date"] == "2026-04-30"
    assert st.due_dates("2026-03-31", date(2026, 4, 1))["epf"]["date"] == "2026-04-15"
    december = st.due_dates("2026-12-31", date(2027, 1, 1))
    assert december["tds"]["date"] == "2027-01-07" and december["epf"]["date"] == "2027-01-15"


def test_due_status_overdue_and_due_today():
    late = st.due_dates("2026-07-31", date(2026, 8, 20))
    assert late["tds"]["status"] == "overdue" and late["tds"]["days_left"] == -13
    assert st.due_dates("2026-07-31", date(2026, 8, 15))["epf"]["status"] == "due_today"


def test_an_unparseable_period_end_gives_no_due_dates():
    assert st.due_dates(None, TODAY) == {} and st.due_dates("not a date", TODAY) == {}


def test_india_statutes_amounts_due_dates_and_header_check():
    out = india()
    assert [(s["statute"], s["side"], s["amount"]) for s in out["statutes"]] == [
        ("EPF", "employee", 300.0), ("EPF", "employer", 300.0), ("ESI", "employee", 5.0),
        ("ESI", "employer", 20.0), ("Professional tax", "employee", 400.0), ("TDS", "employee", 50.0)]
    tds = next(s for s in out["statutes"] if s["statute"] == "TDS")
    assert tds["due"]["date"] == "2026-08-07" and tds["due"]["days_left"] == 4
    pt = next(s for s in out["statutes"] if s["statute"] == "Professional tax")
    assert pt["due"] is None and "state" in pt["note"]
    assert out["consistency"] == {"matches": True, "compared": 6, "differences": {}}
    assert out["deposit_tracking"].startswith("not tracked in AgentSwitch")
    assert out["period"] == {"start": "2026-07-01", "end": "2026-07-31"} and out["calculated"] is True


def test_a_header_mismatch_is_reported():
    out = india(run={**RUN_IN, "total_tds": 60})
    assert out["consistency"]["matches"] is False
    assert out["consistency"]["differences"] == {"total_tds": {"header": 60.0, "slips": 50.0}}


def test_rows_with_no_amount_and_no_header_are_not_listed_and_no_header_means_null():
    out = india(run={"id": "R1", "status": "paid", "pay_period_end": "2026-07-31"})
    assert all(s["statute"] != "EPS" for s in out["statutes"])
    assert out["consistency"] == {"matches": None, "compared": 0, "differences": {}}


def test_esi_flag_counts_covered_employees_above_the_ceiling_only():
    rows = ROWS_IN + [slip(employee_id="z", gross_pay=25000, esi_covered=1),
                      slip(employee_id="y", gross_pay=20000, esi_covered=1),
                      slip(employee_id="x", gross_pay=30000, esi_covered=0)]
    flag = india(rows=rows)["flags"]["esi_covered_above_ceiling"]
    assert flag["count"] == 1 and flag["ceiling"] == 21000.0
    assert [e["employee_id"] for e in flag["employees"]] == ["z"]


def test_config_is_echoed_and_missing_configs_are_noted():
    out = india()
    assert out["config"]["ESIConfig"]["wage_ceiling"] == 21000.0
    assert out["config"]["PTConfig"] == {"deduction_cycle": "monthly", "annual_cap": 2500.0}
    assert out["config"]["LWFConfig"]["state"] == "Maharashtra"
    bare = india(configs={})
    assert {item["what"] for item in bare["skipped"]} == {"esi flag", "config"}
    assert "flags" in bare and bare["flags"] == {}


US_DEDUCTIONS = [{"component_name": "Federal Withholding", "amount": 100},
                 {"component_name": "Ohio State Withholding", "amount": 30},
                 {"component_name": "Social Security", "amount": 62},
                 {"component_name": "Medicare", "amount": 14.5},
                 {"component_name": "Health Insurance", "amount": 50}]
ROWS_US = [{"employee_id": "a", "gross_pay": 1000.0, "net_pay": 743.5, "deductions": US_DEDUCTIONS}]
RUN_US = {"id": "U1", "status": "approved", "pay_period_start": "2026-08-01", "pay_period_end": "2026-08-31",
          "total_deductions": 256.5}


def us(rows=ROWS_US, run=RUN_US):
    return st.build_statutory(run=run, rows=rows, jurisdiction="US", configs={}, today=TODAY)


def test_us_withholdings_come_from_deduction_components_and_employer_rows_are_unavailable():
    out = us()
    employee = [(s["statute"], s["amount"]) for s in out["statutes"] if s["side"] == "employee"]
    assert employee == [("Federal income tax", 100.0), ("State income tax", 30.0),
                        ("Social Security", 62.0), ("Medicare", 14.5)]
    employer = [s for s in out["statutes"] if s["side"] == "employer"]
    assert [s["statute"] for s in employer] == ["Social Security (employer match)", "Medicare (employer match)",
                                                "FUTA", "SUTA"]
    assert all(s["available"] is False and s["amount"] is None for s in employer)
    assert employer[0]["note"] == "not in AgentSwitch data for this seat"
    assert all(s["due"] is None for s in out["statutes"])
    assert out["non_statutory_deductions"] == 50.0
    assert out["consistency"] == {"matches": True, "compared": 1, "differences": {}}
    assert "flags" not in out and "config" not in out


def test_us_rate_checks_are_informational_and_can_fail():
    checks = {c["statute"]: c for c in us()["rate_checks"]}
    assert checks["Social Security"]["within_tolerance"] is True and checks["Medicare"]["observed_pct"] == 1.45
    assert checks["Social Security"]["basis"].startswith("public statutory rate")
    heavy = [{**ROWS_US[0], "deductions": [{"component_name": "Social Security", "amount": 100}]}]
    assert {c["statute"]: c for c in us(rows=heavy, run={**RUN_US, "total_deductions": 100})["rate_checks"]}[
        "Social Security"]["within_tolerance"] is False


def test_us_deductions_that_are_malformed_never_crash():
    rows = [{**ROWS_US[0], "deductions": 5}, {**ROWS_US[0], "deductions": [None, 3, {"component_name": "Medicare", "amount": "x"}]}]
    out = us(rows=rows, run={**RUN_US, "total_deductions": 0})
    assert {"what": "amounts", "reason": "1 unusable amount(s) counted as 0"} in out["skipped"]


def test_an_uncalculated_run_reports_nothing_else():
    out = india(rows=[{"net_pay": 0}])
    assert out["calculated"] is False and "statutes" not in out
    assert out["skipped"] == [{"what": "report", "reason": "run not calculated"}]


def test_unusable_india_amounts_are_counted_once():
    out = india(rows=[slip(epf_employee="abc", epf_employer=100), slip(epf_employee=float("nan"))])
    assert {"what": "amounts", "reason": "2 unusable amount(s) counted as 0"} in out["skipped"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd harness && .venv/bin/python -m pytest tests/test_statutory_scaffold.py -q`
Expected: FAIL at collection with `ImportError: cannot import name 'statutory'`.

- [ ] **Step 3: Write the implementation**

Create `harness/payroll_agent/statutory.py`:

```python
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
            "status": "overdue" if days < 0 else "due_today" if days == 0 else "upcoming", "days_left": days}


def due_dates(period_end: str | None, today: date) -> dict[str, dict[str, Any]]:
    """Standard India calendar for a period ending ``period_end`` (YYYY-MM-DD); {} if unusable."""
    try:
        end = date.fromisoformat(str(period_end)[:10])
    except ValueError:
        return {}
    year, month = (end.year + 1, 1) if end.month == 12 else (end.year, end.month + 1)
    tds = date(year, 4, 30) if end.month == 3 else date(year, month, 7)
    return {"tds": _entry(tds, "TDS: 7th of the following month (30 April for March)", today),
            "epf": _entry(date(year, month, 15), "EPF: 15th of the following month", today),
            "esi": _entry(date(year, month, 15), "ESI: 15th of the following month", today)}


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


def _us_label(name: str) -> str | None:
    text = name.lower()
    if "social security" in text:
        return "Social Security"
    if "medicare" in text:
        return "Medicare"
    if "federal" in text:
        return "Federal income tax"
    if "state" in text and "withholding" in text:
        return "State income tax"
    return None


def _us(rows: list[dict[str, Any]], run: dict[str, Any], bad: list[int]
        ) -> tuple[list[dict[str, Any]], dict[str, Any], float, dict[str, float]]:
    sums: dict[str, float] = defaultdict(float)
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
    return statutes, consistency, round(other, 2), dict(sums)


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
        statutes, consistency, other, sums = _us(rows, run, bad)
        extra = {"rate_checks": _rate_checks(rows, sums, bad), "non_statutory_deductions": other}
    if bad[0]:
        skipped.append({"what": "amounts", "reason": f"{bad[0]} unusable amount(s) counted as 0"})
    return {**head, "calculated": True, "statutes": statutes, "consistency": consistency, **extra,
            "deposit_tracking": DEPOSIT_NOTE, "skipped": skipped}
```

- [ ] **Step 4: Run the tests, then the whole suite and lint**

Run: `cd harness && .venv/bin/python -m pytest tests/test_statutory_scaffold.py -q`
Expected: 14 passed.

Run: `cd harness && .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check payroll_agent tests`
Expected: all pass (the existing 141 plus these), lint clean. If ruff reports import ordering, run `.venv/bin/python -m ruff check --fix payroll_agent/statutory.py` and re-run.

- [ ] **Step 5: Commit**

```bash
git add harness/payroll_agent/statutory.py harness/tests/test_statutory_scaffold.py
git commit -m "Add pure statutory dues calculations

India statutes from slip sums cross-checked with the run header, standard
India due dates labelled as not from AgentSwitch, an ESI ceiling flag and a
config echo; US employee withholdings from slip deduction components with
employer-side taxes reported as unavailable.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Pure lifecycle reports

**Files:**
- Create: `harness/payroll_agent/lifecycle.py`
- Test: `harness/tests/test_lifecycle_scaffold.py`

**Interfaces:**
- Consumes (existing): `payroll_agent.cost_report._finite`.
- Produces (used by Task 3): `employee_names(employees) -> dict`, and `build_lifecycle(topic, *, data, names, today, employee_id=None) -> dict` where `data` maps entity names to row lists (`"EmployeeLoan"`, `"LoanRepayment"`, `"SalaryRevision"`, `"FinalSettlement"`, `"InvestmentDeclaration"`, `"ProofOfInvestment"`). Also exported for tests: `loans_report`, `revisions_report`, `settlements_report`, `investments_report`.

- [ ] **Step 1: Write the failing tests**

Create `harness/tests/test_lifecycle_scaffold.py`:

```python
"""Implementation scaffolding for payroll_agent/lifecycle.py.

NOT the team's graded tests. These only check that the code behaves as
designed while it is being built.
"""
from __future__ import annotations

from datetime import date

import pytest

from payroll_agent import lifecycle as lc

TODAY = date(2026, 10, 4)
NAMES = {"a": "Asha A", "b": "Bhaskar B"}

LOANS = [
    {"id": "L1", "number": "LOAN-1", "employee_id": "a", "status": "partially_repaid", "loan_name": "Education Loan",
     "loan_amount": 10000.0, "emi_amount": 1000.0, "tenure_months": 12.0, "interest_rate": 8.0,
     "total_repaid": 2000.0, "disbursement_date": "2026-01-15"},
    {"id": "L2", "number": "LOAN-2", "employee_id": "b", "status": "draft", "loan_amount": 0.14,
     "emi_amount": -0.02, "tenure_months": -3.0, "interest_rate": -4.0, "total_repaid": 0.0,
     "disbursement_date": "0009-09-12"},
    {"id": "L3", "number": "LOAN-3", "employee_id": "b", "status": "cancelled", "loan_amount": -5.0,
     "emi_amount": 10.0, "tenure_months": 6.0, "interest_rate": 5.0, "disbursement_date": "2026-02-01"},
]
REPAYMENTS = [
    {"loan_id": "L1", "employee_id": "a", "date": "2026-09-30", "status": "scheduled",
     "principal_amount": 900.0, "total_amount": 1000.0},
    {"loan_id": "L1", "employee_id": "a", "date": "2026-10-31", "status": "scheduled",
     "principal_amount": 900.0, "total_amount": 1000.0},
    {"loan_id": "L1", "employee_id": "a", "date": "2026-07-31", "status": "deducted",
     "principal_amount": 900.0, "total_amount": 1000.0},
    {"loan_id": "L2", "employee_id": "b", "date": "2024-12-31", "status": "scheduled",
     "principal_amount": -1000.0, "total_amount": 10000.0},
]


def checks(report):
    return {c["check"]: c["count"] for c in report["data_quality"]["checks"]}


def test_loans_counts_totals_and_flags_the_junk():
    out = lc.loans_report(LOANS, REPAYMENTS, NAMES, TODAY)
    assert out["total"] == 3 and out["by_status"] == {"partially_repaid": 1, "draft": 1, "cancelled": 1}
    assert out["totals"] == {"loan_amount": 10000.14, "total_repaid": 2000.0, "loans_counted": 2,
                             "excluded_invalid": 1}
    assert checks(out) == {"emi_not_positive": 1, "tenure_not_positive": 1, "negative_interest_rate": 1,
                           "implausible_disbursement_date": 1, "loan_amount_not_positive": 1,
                           "repayment_principal_negative": 1}
    assert out["data_quality"]["records_with_issues"] == 2


def test_loans_repayment_schedule_by_month_and_past_scheduled():
    rep = lc.loans_report(LOANS, REPAYMENTS, NAMES, TODAY)["repayments"]
    assert rep["by_status"] == {"scheduled": 3, "deducted": 1}
    assert rep["scheduled_by_month"] == [{"month": "2024-12", "count": 1, "total": 10000.0},
                                         {"month": "2026-09", "count": 1, "total": 1000.0},
                                         {"month": "2026-10", "count": 1, "total": 1000.0}]
    assert rep["scheduled_in_the_past"] == {"count": 2, "total": 11000.0}


def test_loans_for_one_employee_list_that_employees_items():
    out = lc.loans_report(LOANS, REPAYMENTS, NAMES, TODAY, employee_id="a")
    assert out["total"] == 1 and out["repayments"]["total"] == 3
    assert out["items"] == [{"employee_id": "a", "employee_name": "Asha A", "number": "LOAN-1",
                             "status": "partially_repaid", "loan_name": "Education Loan", "loan_amount": 10000.0,
                             "emi_amount": 1000.0, "total_repaid": 2000.0, "tenure_months": 12.0}]
    assert lc.loans_report(LOANS, REPAYMENTS, NAMES, TODAY)["items"] == []


def test_loan_examples_are_capped_but_counts_are_full():
    many = [{"id": f"L{i}", "number": f"LOAN-{i}", "employee_id": "a", "loan_amount": -1.0} for i in range(15)]
    out = lc.loans_report(many, [], NAMES, TODAY)
    check = out["data_quality"]["checks"][0]
    assert check["check"] == "loan_amount_not_positive" and check["count"] == 15
    assert len(check["examples"]) == lc.MAX_EXAMPLES == 10


def test_loans_with_unusable_values_or_dates_never_crash():
    odd = [{"id": "L9", "number": "LOAN-9", "employee_id": "a", "loan_amount": "abc", "emi_amount": None,
            "tenure_months": float("nan"), "interest_rate": "x", "disbursement_date": "not a date"},
           {"id": "L10", "number": "LOAN-10", "employee_id": "a", "loan_amount": 5.0, "disbursement_date": ""}]
    out = lc.loans_report(odd, [{"loan_id": "L9", "date": "garbage", "status": "scheduled",
                                 "total_amount": "?"}], NAMES, TODAY)
    assert out["unusable_amounts"] == 4
    assert checks(out) == {"implausible_disbursement_date": 1}


REVS = [
    {"number": "REV-1", "employee_id": "a", "status": "pending_approval", "effective_date": "2026-09-01",
     "revised_ctc": 100000.0, "approval_status": "pending"},
    {"number": "REV-2", "employee_id": "b", "status": "pending_approval", "effective_date": "2026-10-20",
     "revised_ctc": "abc"},
    {"number": "REV-3", "employee_id": "a", "status": "approved", "effective_date": "2026-10-30"},
    {"number": "REV-4", "employee_id": "a", "status": "applied", "effective_date": "2026-01-01"},
    {"number": "REV-5", "employee_id": "b", "status": "draft", "effective_date": "2026-10-10"},
]


def test_revisions_pending_backdated_upcoming_and_applied():
    out = lc.revisions_report(REVS, NAMES, TODAY)
    assert out["total"] == 5 and out["pending_count"] == 2
    assert [p["number"] for p in out["pending"]] == ["REV-1", "REV-2"]          # oldest effective date first
    assert out["pending"][1]["revised_ctc"] is None and out["unusable_amounts"] == 1
    assert out["backdated_pending"]["count"] == 1
    assert out["backdated_pending"]["examples"][0]["employee_name"] == "Asha A"
    assert out["upcoming_30_days"] == 2 and out["applied_count"] == 1


def test_revisions_for_one_employee():
    assert lc.revisions_report(REVS, NAMES, TODAY, employee_id="b")["total"] == 2


SETTLEMENTS = [
    {"employee_id": "a", "status": "calculated", "approval_status": "not_required", "last_working_date": "2026-09-18",
     "bonus_payable": 100.0, "gratuity_payable": 200.0, "leave_encashment": 50.0, "epf_settlement": 10.0,
     "notice_pay_recovery": 0.0, "gross_settlement": 360.0, "net_settlement": 360.0},
    {"employee_id": "b", "status": "approved", "gross_settlement": 1000.0, "net_settlement": 1000.0},
]


def test_settlements_list_components_and_totals():
    out = lc.settlements_report(SETTLEMENTS, NAMES)
    assert out["total"] == 2 and out["by_status"] == {"calculated": 1, "approved": 1}
    assert out["total_net_settlement"] == 1360.0
    first = out["items"][0]
    assert first["employee_name"] == "Asha A" and first["gratuity_payable"] == 200.0
    assert out["items"][1]["bonus_payable"] is None


DECLS = [
    {"id": "D1", "employee_id": "a", "fiscal_year": "2026-27", "status": "approved",
     "investments": [{"section": "80C", "declared_amount": 150000}, {"section": "80D", "declared_amount": 20000}]},
    {"id": "D2", "employee_id": "b", "fiscal_year": "2026-27", "status": "submitted", "investments": []},
    {"id": "D3", "employee_id": "c", "fiscal_year": "2026-27", "status": "draft"},
    {"id": "D4", "employee_id": "d", "fiscal_year": "2025-26", "status": "approved",
     "investments": [{"section": "80C", "declared_amount": "100000"}, "junk"]},
]
PROOFS = [{"declaration_id": "D1", "approval_status": "submitted", "employee_id": "a"}]


def test_investments_status_sections_proofs_and_missing_proof():
    out = lc.investments_report(DECLS, PROOFS, NAMES)
    assert out["total"] == 4 and out["by_status"] == {"approved": 2, "submitted": 1, "draft": 1}
    assert out["by_fiscal_year"] == {"2026-27": 3, "2025-26": 1}
    assert out["approved_declared_by_section"] == {"80C": 250000.0, "80D": 20000.0}
    assert out["without_proof"]["count"] == 2                      # D2 and D4; the draft D3 is excluded
    assert {e["employee_id"] for e in out["without_proof"]["employees"]} == {"b", "d"}
    assert out["proofs"] == {"total": 1, "by_approval_status": {"submitted": 1}}


def test_build_lifecycle_dispatches_and_reports_unusable_amounts():
    data = {"SalaryRevision": REVS}
    out = lc.build_lifecycle("revisions", data=data, names=NAMES, today=TODAY)
    assert out["topic"] == "revisions" and "unusable_amounts" not in out
    assert out["skipped"] == [{"what": "amounts", "reason": "1 unusable amount(s) counted as 0"}]
    clean = lc.build_lifecycle("settlements", data={"FinalSettlement": SETTLEMENTS}, names=NAMES, today=TODAY)
    assert clean["skipped"] == []


def test_build_lifecycle_rejects_an_unknown_topic():
    with pytest.raises(ValueError):
        lc.build_lifecycle("payslips", data={}, names={}, today=TODAY)


def test_employee_names_prefer_the_full_name():
    names = lc.employee_names([{"id": "a", "first_name": "Asha", "last_name": "Patil"},
                               {"id": "b", "_party_id_display": "Bhaskar B"}, {"id": "c", "_display": "c@x.in"}, {}])
    assert names == {"a": "Asha Patil", "b": "Bhaskar B", "c": "c@x.in"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd harness && .venv/bin/python -m pytest tests/test_lifecycle_scaffold.py -q`
Expected: FAIL at collection with `ImportError: cannot import name 'lifecycle'`.

- [ ] **Step 3: Write the implementation**

Create `harness/payroll_agent/lifecycle.py`:

```python
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
```

- [ ] **Step 4: Run the tests, then the whole suite and lint**

Run: `cd harness && .venv/bin/python -m pytest tests/test_lifecycle_scaffold.py -q`
Expected: 12 passed.

Run: `cd harness && .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check payroll_agent tests`
Expected: all pass, lint clean. If ruff reports a line-length or import issue, fix it without changing behaviour and re-run the tests.

- [ ] **Step 5: Commit**

```bash
git add harness/payroll_agent/lifecycle.py harness/tests/test_lifecycle_scaffold.py
git commit -m "Add pure lifecycle reports for loans, revisions, settlements and investments

Counts by status, repayment schedule by month, pending and backdated
revisions, settlement components, declarations without proof, and
data-quality flags for the noisy loan data.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The capabilities and their workers

**Files:**
- Modify: `harness/payroll_agent/capabilities.py` (two `Capability` entries in `default_registry`)
- Modify: `harness/payroll_agent/workers.py` (imports, helper, two workers, two `_WORKERS` entries)
- Test: `harness/tests/test_statutory_lifecycle_worker_scaffold.py`

**Interfaces:**
- Consumes (Tasks 1, 2): `statutory.build_statutory`, `lifecycle.build_lifecycle`, `lifecycle.employee_names`. Existing: `workers._call_tool`, `workers._fetch_all(ctx, tool, args, jurisdiction) -> (rows, error_dict | None)`, `scan_checks.is_calculated`, `RunContext`, `TaskSpec`.
- Produces (used by Task 4): capability names `statutory_dues` and `lifecycle_report`; `workers.run_statutory_dues(ctx, task)` and `workers.run_lifecycle_report(ctx, task)` returning the shapes in the spec, or the tool's own error, or `scan_incomplete` named for the capability.

- [ ] **Step 1: Write the failing tests**

Create `harness/tests/test_statutory_lifecycle_worker_scaffold.py`:

```python
"""Implementation scaffolding for the statutory_dues and lifecycle_report workers and capabilities.

NOT the team's graded tests. A fake AgentSwitch client stands in for the platform so fetching,
filtering and error handling can be checked offline.
"""
from __future__ import annotations

import pytest

from payroll_agent import workers
from payroll_agent.agentswitch import AgentSwitchToolError
from payroll_agent.capabilities import CapabilityError, default_registry
from payroll_agent.core.live_graph import TaskSpec


class FakeAgentSwitch:
    """``tables`` maps an entity name to its rows; ``<Entity>.list`` filters on employee_id/payrun_id."""

    def __init__(self, *, runs, tables, short_total=None, denied=()):
        self.runs, self.tables = {r["id"]: r for r in runs}, tables
        self.short_total, self.denied = short_total, set(denied)
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
        entity, _, action = name.partition(".")
        if action != "list" or entity not in self.tables:
            raise AssertionError(f"these reads must stay read-only; unexpected tool {name}")
        rows = [r for r in self.tables[entity]
                if all(r.get(k) == arguments[k] for k in ("employee_id", "payrun_id") if k in arguments)]
        offset, limit = arguments.get("offset", 0), min(arguments.get("limit", 10), 100)
        total = len(rows) if self.short_total is None else self.short_total
        return {"data": rows[offset:offset + limit], "limit": limit, "offset": offset, "total": total}


def run(rid, end, **header):
    return {"id": rid, "status": "paid", "pay_period_start": end[:8] + "01", "pay_period_end": end, **header}


def slip(rid, **fields):
    return {"id": f"{rid}-{fields.get('employee_id', 'a')}", "payrun_id": rid, "employee_id": "a",
            "employee_name": "A", "gross_pay": 1000.0, "net_pay": 900.0, "esi_covered": 0, **fields}


IN_RUN = run("R1", "2026-07-31", total_epf_employee=100, total_tds=50)
IN_TABLES = {
    "PayRunEmployee": [slip("R1", epf_employee=100, tds=50)],
    "Employee": [{"id": "a", "first_name": "Asha", "last_name": "Patil"}],
    "EPFConfig": [{"employee_contribution_rate": 12.0}], "ESIConfig": [{"wage_ceiling": 21000.0}],
    "PTConfig": [{"deduction_cycle": "monthly", "annual_cap": 2500.0}], "LWFConfig": [{"deduction_cycle": "half_yearly"}],
}


def dues(fake, jurisdiction="IN", payrun_id="R1"):
    ctx = workers.RunContext(run_id="r", store=None, llm=None, agentswitch=fake)
    return workers.run_statutory_dues(ctx, TaskSpec("d", "statutory_dues", {"jurisdiction": jurisdiction, "payrun_id": payrun_id}))


def called(fake):
    return [name for name, _ in fake.calls]


async def test_india_dues_are_built_from_the_run_and_the_configs():
    fake = FakeAgentSwitch(runs=[IN_RUN], tables=IN_TABLES)
    result = await dues(fake)
    assert result["calculated"] is True and result["consistency"]["matches"] is True
    assert [s["statute"] for s in result["statutes"]] == ["EPF", "TDS"]
    assert result["config"]["ESIConfig"]["wage_ceiling"] == 21000.0
    assert set(called(fake)) == {"PayRun.get", "PayRunEmployee.list", "EPFConfig.list", "ESIConfig.list",
                                 "PTConfig.list", "LWFConfig.list"}


async def test_us_dues_fetch_no_india_configs():
    us_run = run("U1", "2026-08-31", total_deductions=100)
    rows = [slip("U1", deductions=[{"component_name": "Federal Withholding", "amount": 100}])]
    fake = FakeAgentSwitch(runs=[us_run], tables={"PayRunEmployee": rows})
    result = await dues(fake, jurisdiction="US", payrun_id="U1")
    assert [s["statute"] for s in result["statutes"] if s["available"]] == ["Federal income tax"]
    assert set(called(fake)) == {"PayRun.get", "PayRunEmployee.list"}


async def test_an_uncalculated_run_fetches_no_configs():
    fake = FakeAgentSwitch(runs=[IN_RUN], tables={**IN_TABLES, "PayRunEmployee": [slip("R1", net_pay=0)]})
    result = await dues(fake)
    assert result["calculated"] is False and "statutes" not in result
    assert not any(name.endswith("Config.list") for name in called(fake))


async def test_a_failing_config_fetch_only_skips_that_config():
    fake = FakeAgentSwitch(runs=[IN_RUN], tables=IN_TABLES, denied=("ESIConfig.list",))
    result = await dues(fake)
    assert result["calculated"] is True and result["statutes"]
    assert any(item["what"] == "ESIConfig" and "denied" in item["reason"] for item in result["skipped"])
    assert "ESIConfig" not in result["config"]


async def test_a_short_fetch_is_scan_incomplete_named_for_the_capability():
    result = await dues(FakeAgentSwitch(runs=[IN_RUN], tables=IN_TABLES, short_total=50))
    assert result["error"] is True and result["code"] == "scan_incomplete" and result["tool"] == "statutory_dues"


async def test_a_missing_run_returns_the_tools_own_error():
    result = await dues(FakeAgentSwitch(runs=[IN_RUN], tables=IN_TABLES), payrun_id="nope")
    assert result["error"] is True and result["tool"] == "PayRun.get"


LIFE_TABLES = {
    "EmployeeLoan": [{"id": "L1", "number": "LOAN-1", "employee_id": "a", "status": "draft", "loan_amount": -1.0},
                     {"id": "L2", "number": "LOAN-2", "employee_id": "b", "status": "draft", "loan_amount": 100.0}],
    "LoanRepayment": [{"loan_id": "L1", "employee_id": "a", "date": "2030-01-31", "status": "scheduled",
                       "principal_amount": 1.0, "total_amount": 2.0}],
    "Employee": [{"id": "a", "first_name": "Asha", "last_name": "Patil"}],
    "SalaryRevision": [{"number": "REV-1", "employee_id": "a", "status": "pending_approval", "effective_date": "2020-01-01"}],
}


def life(fake, topic, employee_id=None):
    ctx = workers.RunContext(run_id="r", store=None, llm=None, agentswitch=fake)
    inp = {"jurisdiction": "IN", "topic": topic, **({"employee_id": employee_id} if employee_id else {})}
    return workers.run_lifecycle_report(ctx, TaskSpec("l", "lifecycle_report", inp))


async def test_loans_report_reads_loans_repayments_and_employee_names_only():
    fake = FakeAgentSwitch(runs=[], tables=LIFE_TABLES)
    result = await life(fake, "loans")
    assert result["topic"] == "loans" and result["total"] == 2
    assert {c["check"] for c in result["data_quality"]["checks"]} == {"loan_amount_not_positive"}
    assert set(called(fake)) == {"EmployeeLoan.list", "LoanRepayment.list", "Employee.list"}


async def test_an_employee_filter_is_sent_to_the_list_tools_and_applied():
    fake = FakeAgentSwitch(runs=[], tables=LIFE_TABLES)
    result = await life(fake, "loans", employee_id="a")
    assert result["total"] == 1 and result["items"][0]["employee_name"] == "Asha Patil"
    sent = [args for name, args in fake.calls if name in ("EmployeeLoan.list", "LoanRepayment.list")]
    assert sent and all(args["employee_id"] == "a" for args in sent)


async def test_revisions_report_flags_a_backdated_pending_revision():
    result = await life(FakeAgentSwitch(runs=[], tables=LIFE_TABLES), "revisions")
    assert result["backdated_pending"]["count"] == 1 and result["pending"][0]["employee_name"] == "Asha Patil"


async def test_a_denied_employee_list_keeps_its_own_error():
    result = await life(FakeAgentSwitch(runs=[], tables=LIFE_TABLES, denied=("Employee.list",)), "loans")
    assert result["error"] is True and result["tool"] == "Employee.list" and result["code"] == -32001


async def test_a_short_lifecycle_fetch_is_named_for_the_capability():
    result = await life(FakeAgentSwitch(runs=[], tables=LIFE_TABLES, short_total=50), "loans")
    assert result["code"] == "scan_incomplete" and result["tool"] == "lifecycle_report"


def test_capabilities_are_registered_read_only_with_id_provenance():
    registry = default_registry()
    for name in ("statutory_dues", "lifecycle_report"):
        assert registry.get(name).side_effect is False
        assert name in registry.family("evidence") and name in workers._WORKERS
    registry.validate("statutory_dues", {"jurisdiction": "US", "payrun_id": "R1"}, known_values={"R1"})
    with pytest.raises(CapabilityError):
        registry.validate("statutory_dues", {"jurisdiction": "US", "payrun_id": "R1"}, known_values=set())
    clean = registry.validate("lifecycle_report", {"jurisdiction": "IN", "topic": "loans", "employee_id": "a"},
                              known_values={"a"})
    assert clean["topic"] == "loans"


@pytest.mark.parametrize("bad", [{"topic": "payslips"}, {"topic": "loans", "employee_id": "ghost"}, {}])
def test_lifecycle_rejects_bad_arguments(bad):
    with pytest.raises(CapabilityError):
        default_registry().validate("lifecycle_report", {"jurisdiction": "IN", **bad}, known_values={"a"})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd harness && .venv/bin/python -m pytest tests/test_statutory_lifecycle_worker_scaffold.py -q`
Expected: FAIL (`AttributeError: ... no attribute 'run_statutory_dues'` and `unknown capability`).

- [ ] **Step 3: Add the two capabilities**

In `harness/payroll_agent/capabilities.py`, inside `default_registry()`, insert immediately before the `Capability(` whose name is `"run_payroll"` (after the `payroll_cost_report` entry):

```python
        Capability(
            "statutory_dues",
            "Report the statutory dues one calculated PayRun creates. India: EPF, EPS, EDLI, EPF admin "
            "charges, ESI, professional tax, TDS and LWF, each with amount, side, source and a cross-check "
            "against the run header, plus the standard due date (TDS the 7th, EPF and ESI the 15th of the "
            "following month, labelled as not from AgentSwitch) and the config rates. US: the employee "
            "withholdings (federal and state income tax, Social Security, Medicare); employer-side US taxes "
            "are reported as unavailable. Read-only; it says that deposits are not tracked, and if the run "
            "is not calculated it says so and reports nothing else. Use a `payrun_id` taken from an earlier "
            "outcome (for example `list_payruns`).",
            {"jurisdiction": _JURISDICTION,
             "payrun_id": string("The PayRun to report on.", maximum=200, format="id")},
            families=("evidence",),
        ),
        Capability(
            "lifecycle_report",
            "Summarise one lifecycle topic, read-only. `loans`: status, repayment schedule by month and "
            "data-quality flags (non-positive amounts, negative rates, implausible dates). `revisions`: "
            "salary revisions by status, the pending ones, and backdated pending revisions that imply "
            "arrears. `settlements`: final settlements with their components. `investments`: declarations "
            "by status and section, declarations without proof, and proofs by approval status. Pass "
            "`employee_id` (taken from an earlier outcome) to limit it to one employee.",
            {"jurisdiction": _JURISDICTION,
             "topic": string("Which topic to report.",
                             choices=("loans", "revisions", "settlements", "investments")),
             "employee_id": string("Limit the report to this employee.", required=False, maximum=200,
                                   format="id")},
            families=("evidence",),
        ),
```

- [ ] **Step 4: Add the workers**

In `harness/payroll_agent/workers.py`:

(a) Change the import line `from . import cost_report, scan_checks` to:

```python
from . import cost_report, lifecycle, scan_checks, statutory
```

and add `from datetime import date` to the standard-library imports (keep the block sorted; place it after `import os` and before `from dataclasses import dataclass`).

(b) Insert immediately before the comment line `# run_payroll rewrites the slips of whichever run it reuses`:

```python
def _with_tool(problem: dict[str, Any], name: str) -> dict[str, Any]:
    """The shared fetch helper words a short fetch as the scan's; name the calling capability instead."""
    return {**problem, "tool": name} if problem.get("code") == "scan_incomplete" else problem


_INDIA_CONFIGS = ("EPFConfig", "ESIConfig", "PTConfig", "LWFConfig")
_LIFECYCLE_ENTITIES = {"loans": ("EmployeeLoan", "LoanRepayment"), "revisions": ("SalaryRevision",),
                       "settlements": ("FinalSettlement",),
                       "investments": ("InvestmentDeclaration", "ProofOfInvestment")}


async def run_statutory_dues(ctx: RunContext, task: TaskSpec) -> dict[str, Any]:
    """Read-only: fetch, then let statutory compute. Never calls a mutating tool."""
    jurisdiction, payrun_id = task.input["jurisdiction"], task.input["payrun_id"]
    run = await _call_tool(ctx, "PayRun.get", {"id": payrun_id}, jurisdiction)
    if run.get("error"):
        return run
    rows, problem = await _fetch_all(ctx, "PayRunEmployee.list", {"payrun_id": payrun_id}, jurisdiction)
    if problem:
        return _with_tool(problem, "statutory_dues")
    configs: dict[str, list[dict[str, Any]]] = {}
    config_problems: list[dict[str, str]] = []
    # An uncalculated run reports nothing else, so it needs no configs; the US has none to read.
    if jurisdiction == "IN" and scan_checks.is_calculated(rows):
        for name in _INDIA_CONFIGS:
            page = await _call_tool(ctx, f"{name}.list", {"limit": 1}, jurisdiction)
            if page.get("error"):
                config_problems.append({"what": name, "reason": f"{name}.list failed: {page.get('message')}"})
            else:
                configs[name] = page.get("data") or []
    result = statutory.build_statutory(run=run, rows=rows, jurisdiction=jurisdiction, configs=configs,
                                       today=date.today())
    if result.get("calculated"):
        result["skipped"].extend(config_problems)
    return result


async def run_lifecycle_report(ctx: RunContext, task: TaskSpec) -> dict[str, Any]:
    """Read-only: fetch the topic's entities and the employee names, then let lifecycle compute."""
    jurisdiction, topic = task.input["jurisdiction"], task.input["topic"]
    employee_id = task.input.get("employee_id")
    narrow = {"employee_id": employee_id} if employee_id else {}
    data: dict[str, list[dict[str, Any]]] = {}
    for entity in _LIFECYCLE_ENTITIES[topic]:
        data[entity], problem = await _fetch_all(ctx, f"{entity}.list", narrow, jurisdiction)
        if problem:
            return _with_tool(problem, "lifecycle_report")
    employees, problem = await _fetch_all(ctx, "Employee.list", {}, jurisdiction)
    if problem:
        return _with_tool(problem, "lifecycle_report")
    return lifecycle.build_lifecycle(topic, data=data, names=lifecycle.employee_names(employees),
                                     today=date.today(), employee_id=employee_id)


```

(c) In the `_WORKERS` dict, add after the `"payroll_cost_report": run_payroll_cost_report,` line:

```python
    "statutory_dues": run_statutory_dues,
    "lifecycle_report": run_lifecycle_report,
```

- [ ] **Step 5: Run the tests, then the whole suite and lint**

Run: `cd harness && .venv/bin/python -m pytest tests/test_statutory_lifecycle_worker_scaffold.py -q`
Expected: 15 passed.

Run: `cd harness && .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check payroll_agent tests`
Expected: all pass, lint clean. If ruff reports import order in `workers.py`, run `.venv/bin/python -m ruff check --fix payroll_agent/workers.py` and re-run.

- [ ] **Step 6: Commit**

```bash
git add harness/payroll_agent/capabilities.py harness/payroll_agent/workers.py harness/tests/test_statutory_lifecycle_worker_scaffold.py
git commit -m "Add read-only statutory_dues and lifecycle_report capabilities and workers

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Scaffold eval tasks, live checks, docs and push

**Files:**
- Create: `harness/evals/scripts/scaffold_statutory_india.json`, `scaffold_statutory_us.json`, `scaffold_statutory_uncalculated.json`, `scaffold_lifecycle_loans.json`
- Modify: `harness/evals/tasks/scaffold.jsonl` (append four lines)
- Modify: `harness/WORKFLOWS.md` (families E and F), `harness/NEXT_STEPS.md`
- Add to git: the spec and this plan

**Interfaces:**
- Consumes: Tasks 1 to 3, the existing runner, `ScriptedLLM` scripts and the `node_result`, `tool_called`, `tool_not_called`, `no_mutation` verifiers. Real ids: India July `PRUN-2026-00011` = `dd9bf1ea-eab9-4f93-b91f-7b6721ce4d0b`; India September draft `PRUN-2026-00013` = `b5b0cc7d-7aef-4738-a6e0-bfaa01c94aaa`; US August `PRUN-2026-00002` = `d0477238-3c08-4d87-a378-fca1a4b0dfe8`.
- Produces: four scaffold eval tasks run against the real tenants, recorded live results, updated docs, and the pushed branch.

- [ ] **Step 1: Write the four scripts**

Create `harness/evals/scripts/scaffold_statutory_india.json`:

```json
{
  "name": "scaffold_statutory_india",
  "note": "Scaffold only. Find the July 2026 run, report its statutory dues, answer. Read-only.",
  "steps": [
    {"reply": {"add": [{"id": "lookup", "capability": "list_payruns",
                        "arguments": {"jurisdiction": "IN", "month": "2026-07"}, "depends_on": []}],
               "cancel": [], "finish": false, "reason": "find the July 2026 run"}},
    {"reply": {"add": [{"id": "dues", "capability": "statutory_dues",
                        "arguments": {"jurisdiction": "IN", "payrun_id": "dd9bf1ea-eab9-4f93-b91f-7b6721ce4d0b"},
                        "depends_on": ["lookup"]}],
               "cancel": [], "finish": false, "reason": "report the statutory dues"}},
    {"reply": {"add": [{"id": "answer", "capability": "answer_with_evidence",
                        "arguments": {"query": "What statutory dues does the July 2026 payroll create, and by when?"},
                        "depends_on": ["dues"]}],
               "cancel": [], "finish": false, "reason": "report finished; answer from it"}},
    {"reply": {"ready": true, "missing": [], "reason": "the dues report is present"}},
    {"reply": "Scripted answer: see the statutory_dues evidence."}
  ]
}
```

Create `harness/evals/scripts/scaffold_statutory_us.json`:

```json
{
  "name": "scaffold_statutory_us",
  "note": "Scaffold only. Find the US August 2026 run, report its statutory withholdings, answer. Read-only.",
  "steps": [
    {"reply": {"add": [{"id": "lookup", "capability": "list_payruns",
                        "arguments": {"jurisdiction": "US", "month": "2026-08"}, "depends_on": []}],
               "cancel": [], "finish": false, "reason": "find the US August 2026 run"}},
    {"reply": {"add": [{"id": "dues", "capability": "statutory_dues",
                        "arguments": {"jurisdiction": "US", "payrun_id": "d0477238-3c08-4d87-a378-fca1a4b0dfe8"},
                        "depends_on": ["lookup"]}],
               "cancel": [], "finish": false, "reason": "report the statutory withholdings"}},
    {"reply": {"add": [{"id": "answer", "capability": "answer_with_evidence",
                        "arguments": {"query": "What statutory withholdings were in the US August 2026 payroll?"},
                        "depends_on": ["dues"]}],
               "cancel": [], "finish": false, "reason": "report finished; answer from it"}},
    {"reply": {"ready": true, "missing": [], "reason": "the dues report is present"}},
    {"reply": "Scripted answer: see the statutory_dues evidence."}
  ]
}
```

Create `harness/evals/scripts/scaffold_statutory_uncalculated.json`:

```json
{
  "name": "scaffold_statutory_uncalculated",
  "note": "Scaffold only. Report on the September draft, which is not calculated. Read-only.",
  "steps": [
    {"reply": {"add": [{"id": "lookup", "capability": "list_payruns",
                        "arguments": {"jurisdiction": "IN", "month": "2026-09"}, "depends_on": []}],
               "cancel": [], "finish": false, "reason": "find the September 2026 run"}},
    {"reply": {"add": [{"id": "dues", "capability": "statutory_dues",
                        "arguments": {"jurisdiction": "IN", "payrun_id": "b5b0cc7d-7aef-4738-a6e0-bfaa01c94aaa"},
                        "depends_on": ["lookup"]}],
               "cancel": [], "finish": false, "reason": "report the statutory dues"}},
    {"reply": {"add": [{"id": "answer", "capability": "answer_with_evidence",
                        "arguments": {"query": "What statutory dues does the September 2026 payroll create?"},
                        "depends_on": ["dues"]}],
               "cancel": [], "finish": false, "reason": "report finished; answer from it"}},
    {"reply": {"ready": true, "missing": [], "reason": "the report says the run is not calculated"}},
    {"reply": "Scripted answer: the September run is not calculated, so there are no dues to report yet."}
  ]
}
```

Create `harness/evals/scripts/scaffold_lifecycle_loans.json`:

```json
{
  "name": "scaffold_lifecycle_loans",
  "note": "Scaffold only. Summarise the loans and flag data problems. Read-only.",
  "steps": [
    {"reply": {"add": [{"id": "loans", "capability": "lifecycle_report",
                        "arguments": {"jurisdiction": "IN", "topic": "loans"}, "depends_on": []}],
               "cancel": [], "finish": false, "reason": "summarise the loans"}},
    {"reply": {"add": [{"id": "answer", "capability": "answer_with_evidence",
                        "arguments": {"query": "Summarise the employee loans and flag any data problems."},
                        "depends_on": ["loans"]}],
               "cancel": [], "finish": false, "reason": "report finished; answer from it"}},
    {"reply": {"ready": true, "missing": [], "reason": "the loans report is present"}},
    {"reply": "Scripted answer: see the lifecycle_report evidence."}
  ]
}
```

- [ ] **Step 2: Append the four scaffold tasks**

Append these four lines to `harness/evals/tasks/scaffold.jsonl` (one JSON object per line, no blank line between):

```json
{"id":"scaffold_scripted_statutory_india","family":"check","authored_by":"scaffold","goal":"What statutory dues does the July 2026 payroll create, and by when?","jurisdiction":"IN","allow":[],"mode":"dry_run","transport":"scripted","script":"scripts/scaffold_statutory_india.json","watch":[{"tool":"PayRun.list","args":{"limit":100},"key":"id","fields":["status","pay_period_start"]}],"verifiers":[{"type":"tool_not_called","tool":"PayRun.run_payroll"},{"type":"no_mutation"},{"type":"node_result","skill":"statutory_dues","path":"calculated","expect":true},{"type":"node_result","skill":"statutory_dues","path":"consistency.matches","expect":true},{"type":"node_result","skill":"statutory_dues","path":"statutes.0.amount","expect":93405.0},{"type":"node_result","skill":"statutory_dues","path":"statutes.0.due.date","expect":"2026-08-15"}]}
{"id":"scaffold_scripted_statutory_us","family":"check","authored_by":"scaffold","goal":"What statutory withholdings were in the US August 2026 payroll?","jurisdiction":"US","allow":[],"mode":"dry_run","transport":"scripted","script":"scripts/scaffold_statutory_us.json","watch":[{"tool":"PayRun.list","args":{"limit":100},"key":"id","fields":["status","pay_period_start"]}],"verifiers":[{"type":"tool_not_called","tool":"PayRun.run_payroll"},{"type":"no_mutation"},{"type":"node_result","skill":"statutory_dues","path":"calculated","expect":true},{"type":"node_result","skill":"statutory_dues","path":"consistency.matches","expect":true},{"type":"node_result","skill":"statutory_dues","path":"statutes.0.statute","expect":"Federal income tax"},{"type":"node_result","skill":"statutory_dues","path":"statutes.0.amount","expect":{"op":"gte","value":29600}},{"type":"node_result","skill":"statutory_dues","path":"statutes.4.available","expect":false}]}
{"id":"scaffold_scripted_statutory_uncalculated","family":"check","authored_by":"scaffold","goal":"What statutory dues does the September 2026 payroll create?","jurisdiction":"IN","allow":[],"mode":"dry_run","transport":"scripted","script":"scripts/scaffold_statutory_uncalculated.json","watch":[{"tool":"PayRun.list","args":{"limit":100},"key":"id","fields":["status","pay_period_start"]}],"verifiers":[{"type":"tool_not_called","tool":"PayRun.calculate_payroll"},{"type":"no_mutation"},{"type":"node_result","skill":"statutory_dues","path":"calculated","expect":false}]}
{"id":"scaffold_scripted_lifecycle_loans","family":"check","authored_by":"scaffold","goal":"Summarise the employee loans and flag any data problems.","jurisdiction":"IN","allow":[],"mode":"dry_run","transport":"scripted","script":"scripts/scaffold_lifecycle_loans.json","watch":[{"tool":"PayRun.list","args":{"limit":100},"key":"id","fields":["status","pay_period_start"]}],"verifiers":[{"type":"no_mutation"},{"type":"node_result","skill":"lifecycle_report","path":"topic","expect":"loans"},{"type":"node_result","skill":"lifecycle_report","path":"total","expect":102},{"type":"node_result","skill":"lifecycle_report","path":"data_quality.records_with_issues","expect":{"op":"gte","value":1}}]}
```

- [ ] **Step 3: Run the four against the real tenants**

Run:
```bash
cd harness
for t in scaffold_scripted_statutory_india scaffold_scripted_statutory_us scaffold_scripted_statutory_uncalculated scaffold_scripted_lifecycle_loans; do
  .venv/bin/python -m evals.runner --tasks evals/tasks/scaffold.jsonl --task-id $t 2>&1 | grep -A1 "^task" | tail -1
done
```
Expected: `pass` for each. If one reports `fail`, read `evals/runs/<latest>/<id>.score.json`, find the cause and fix it. Do not loosen a verifier to make it pass. If a value differs from the expectation (for example `total` is not 102, or the US federal amount is not at least 29,600), report the real number and ask before changing the expectation. If the loan-count check fails because the list tool returns fewer than all loans, stop: it means paging is wrong.

- [ ] **Step 4: Recompute the answers from the raw rows (independent check)**

Run:
```bash
cd harness && .venv/bin/python - <<'EOF'
import asyncio, collections, json, glob
from dotenv import load_dotenv; load_dotenv(".env")
from payroll_agent.agentswitch import AgentSwitchClient
def latest(task, skill):
    rec = json.load(open(sorted(glob.glob(f"evals/runs/*/{task}.json"))[-1]))
    return next(n for n in rec["result"]["nodes"].values() if n["skill"] == skill)["result"]
async def main():
    c = AgentSwitchClient()
    try:
        india = latest("scaffold_scripted_statutory_india", "statutory_dues")
        slips = (await c.call_tool("PayRunEmployee.list", {"payrun_id": "dd9bf1ea-eab9-4f93-b91f-7b6721ce4d0b", "limit": 100}, jurisdiction="IN"))["data"]
        raw = {f: round(sum((s.get(f) or 0) for s in slips), 2) for f in ("epf_employee", "epf_employer", "esi_employee", "esi_employer", "professional_tax", "tds")}
        rep = {(s["statute"], s["side"]): s["amount"] for s in india["statutes"]}
        print("INDIA report:", rep, "\nINDIA raw   :", raw, "\nconsistency:", india["consistency"], "| TDS due:", next(s for s in india["statutes"] if s["statute"] == "TDS")["due"]["date"])
        us = latest("scaffold_scripted_statutory_us", "statutory_dues")
        uslips = (await c.call_tool("PayRunEmployee.list", {"payrun_id": "d0477238-3c08-4d87-a378-fca1a4b0dfe8", "limit": 100}, jurisdiction="US"))["data"]
        comp = collections.defaultdict(float)
        for s in uslips:
            for d in (s.get("deductions") or []): comp[d["component_name"]] += d.get("amount", 0)
        print("\nUS report:", {s["statute"]: s["amount"] for s in us["statutes"] if s["available"]}, "\nUS raw components:", {k: round(v, 2) for k, v in comp.items()}, "\nUS rate checks:", [(r["statute"], r["observed_pct"], r["within_tolerance"]) for r in us["rate_checks"]])
        loans = latest("scaffold_scripted_lifecycle_loans", "lifecycle_report")
        raw_loans = (await c.call_tool("EmployeeLoan.list", {"limit": 100}, jurisdiction="IN"))
        print("\nLOANS report: total", loans["total"], "by_status", loans["by_status"], "| issues in", loans["data_quality"]["records_with_issues"], "loans |", {k["check"]: k["count"] for k in loans["data_quality"]["checks"]})
        print("LOANS raw: total", raw_loans["total"], "(first page", len(raw_loans["data"]), ")")
    finally:
        await c.close()
asyncio.run(main())
EOF
```
Expected: India amounts equal the raw sums; US amounts equal the raw component sums; the loan total equals the raw total. If anything differs, report it and fix the cause.

- [ ] **Step 5: Live check with the real models (needs the gateway and provider setup in NEXT_STEPS.md)**

Confirm the gateway is up: `curl -s http://127.0.0.1:8111/healthz`.

```bash
cd harness
run_one() {
  .venv/bin/python -m payroll_agent.run "$1" --jurisdiction "$2" > /private/tmp/claude-501/live6.json 2>/dev/null
  .venv/bin/python - <<'EOF'
import json
r = json.load(open("/private/tmp/claude-501/live6.json"))
print("skills:", [n["skill"] for n in r["nodes"].values()], "| answered:", r["answer"] is not None, "| declined:", r["declined"])
print("   answer:", (r["answer"] or "NONE: " + ((r["patch_events"] or [{}])[-1].get("reason") or "")[:160])[:380].replace("\n", " "))
EOF
}
run_one "What statutory dues does the July 2026 payroll create, and by when?" IN
run_one "What statutory withholdings were in the August 2026 payroll?" US
run_one "Summarise the employee loans and flag any data problems." IN
run_one "Which salary revisions are pending, and are any backdated?" IN
```
Expected: the model calls `statutory_dues` or `lifecycle_report` and answers. Record what actually happened, including failures; the planner can be flaky and a failure here is not necessarily a bug in these capabilities. Check each answer's numbers against the report; do not tune prompts without telling the user.

- [ ] **Step 6: Update the docs**

In `harness/WORKFLOWS.md`, section E, add a status paragraph: `statutory_dues` (read-only) covers India EPF, EPS, EDLI, EPF admin charges, ESI, PT, TDS and LWF with header cross-check, standard India due dates for TDS, EPF and ESI labelled as not from AgentSwitch, the config rates and an ESI-ceiling flag; and US employee withholdings from slip deduction components with employer-side taxes reported as unavailable. Not built: PT and LWF due dates, US due dates, TDS slab maths, Form 16 and Form 24Q generation (REST-only), deposit tracking (not in AgentSwitch). In section F, add a status paragraph: `lifecycle_report` (read-only) covers loans (with data-quality flags), salary revisions (pending and backdated), final settlements and investments; the actions on them (calculate settlement, submit for approval) are sub-project 6c and not built. Link `docs/superpowers/specs/2026-10-04-statutory-and-lifecycle-reads-design.md`.

In `harness/NEXT_STEPS.md`, under the roadmap item for B/E/F extras, record 6a and 6b as built with a short "Result" line holding the real numbers from Steps 3 to 5 (July dues, the US withholdings, the loan counts and the data-quality counts, which live queries succeeded and any failure seen). Add one line for the team: the loan data has negative amounts, rates and tenures and dates in year 0009, which may be a platform validation gap (a candidate AgentSwitch bug report) or class test data; the team should judge. Note 6c (guarded actions) as the remaining item.

- [ ] **Step 7: Final run, commit and push the branch**

Run: `cd harness && .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check payroll_agent evals tests`
Expected: all pass, lint clean.

```bash
cd ..
git add harness/evals harness/WORKFLOWS.md harness/NEXT_STEPS.md docs/superpowers/specs/2026-10-04-statutory-and-lifecycle-reads-design.md docs/superpowers/plans/2026-10-04-statutory-and-lifecycle-reads.md
git commit -m "Add scaffold eval tasks for statutory_dues and lifecycle_report; spec, plan and docs

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
gh api user --jq .login        # must print pragsyy1729
git push -u origin statutory-and-lifecycle-reads
git status -sb | head -1
```
Expected: the login prints `pragsyy1729`, the branch is pushed, and `origin/main` is unchanged. Do not push `main` and do not open a pull request.

---

## Self-review (against the spec)

- **Purpose and decisions:** two capabilities (Task 3), both jurisdictions for dues with US employer rows unavailable (Task 1), due dates option A, India only (Task 1).
- **6a result shape, India rows, US rows, rate checks, ESI flag, config echo, uncalculated, unusable amounts:** Task 1 tests, one per item.
- **6b topics, caps, data-quality checks, employee filter, names, unusable amounts:** Task 2 tests.
- **Error handling (tool error kept, `scan_incomplete` named for the capability, failing config only skips it):** Task 3 tests.
- **Read-only:** the fake raises on any unexpected tool; Task 4 `no_mutation` verifiers.
- **Testing and grading split:** every test file is labelled scaffold; the eval tasks are `authored_by: "scaffold"`; no graded task is written.
- **Limitations:** recorded in WORKFLOWS.md and NEXT_STEPS.md (Task 4 Step 6).
- **Type consistency:** `build_statutory` keyword arguments, `build_lifecycle(topic, *, data, names, today, employee_id)`, `_fetch_all -> (rows, error | None)` and `_with_tool(problem, name)` are used identically across Tasks 1 to 3.
- **Placeholders:** none. Counts and results in Task 4 come from running the steps.
- **Git:** identity, branch-only push and the `pragsyy1729` check are in Global Constraints and Step 7.

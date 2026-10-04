# Guarded Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add eleven guarded write capabilities (calculate, submit, cancel, apply, payslips), each behind its own `--allow`, all driven by one action table and one guard pipeline.

**Architecture:** A pure module `payroll_agent/guarded.py` holds the action table and the checks. One generic worker finds a record, re-reads it, applies the checks, calls the tool once and re-reads to verify. Capabilities are generated from the table, so authority is per action and unauthorised actions are hidden from the planner.

**Tech Stack:** Python 3, asyncio, pytest (`asyncio_mode = "auto"`), the existing `AgentSwitchClient`, live graph, planner and `evals/` harness. Run everything from `harness/` with `.venv/bin/python`.

**Spec:** `docs/superpowers/specs/2026-10-04-guarded-actions-design.md`

## Global Constraints

- **These actions change a shared tenant that has no undo. No test, check or live run in this plan may execute an allowed path against real data.** Unit tests use a fake client. Real-tenant checks (Task 4) are refusals only, aimed at records whose real state the platform would also reject. If any real-tenant run shows a mutating call was made, stop immediately and report it.
- The action table (name; entity; tool; from statuses; to status): `calculate_payrun` PayRun `PayRun.calculate_payroll` draft->review; `generate_payslips` PayRun `PayRun.generate_payslips` review/approved/pending_approval/paid->no status change (needs calculated rows); `send_payslips` PayRun `PayRun.send_payslips` same; `cancel_draft_payrun` PayRun `PayRun.cancel.draft.cancelled` draft->cancelled; `submit_loan` EmployeeLoan `EmployeeLoan.submit` draft->pending_approval (loan_amount, emi_amount, tenure_months positive); `cancel_draft_loan` EmployeeLoan `EmployeeLoan.cancel.draft.cancelled` draft->cancelled; `submit_salary_revision` SalaryRevision `SalaryRevision.submit` draft->pending_approval (revised_ctc positive); `apply_salary_revision` SalaryRevision `SalaryRevision.apply` approved->applied (revised_ctc positive and approval_status not rejected); `calculate_settlement` FinalSettlement `FinalSettlement.calculate_settlement` draft->calculated; `submit_final_settlement` FinalSettlement `FinalSettlement.submit` calculated->pending_approval (net_settlement positive); `submit_investment_declaration` InvestmentDeclaration `InvestmentDeclaration.submit` draft->submitted.
- Guard order: find by number or id (none: `record_not_found`; several: `ambiguous_reference`); re-read with `.get`; status equals target: `already_in_target_state`; status not in the action's from statuses: `not_in_required_state`; for a status move the record's `_transitions` must list from the current status to the target, else `transition_not_available`; action guard: `precondition_failed`; payslip actions need calculated rows, else `run_not_calculated`. Only then call the tool, exactly once, and re-read: a status move whose new status is not the target returns `status_unchanged`.
- A refusal is `{"error": true, "tool": <action tool>, "code", "message": "<reason>; nothing was changed", "record": {id, number, status} | null}`. A tool or list error is returned unchanged; a short list fetch returns `scan_incomplete` with `tool` set to the capability name.
- Every action capability is `side_effect=True` with arguments `jurisdiction` and `reference` (`format="id"`, max 200). The planner hides a side-effect capability unless it is in `--allow`.
- Graded tests are hand-written by the team; a test written by Claude scores zero. Every test file here says it is implementation scaffolding and not a graded submission; scaffold eval tasks use `authored_by: "scaffold"`.
- **Git:** work on branch `guarded-actions`. Commit with the repo's default identity (Pragathi Kalidasan Vetrivel Murugan, from the global git config), ending each message with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Push **only that branch** as the active `gh` account `pragsyy1729` (`gh api user --jq .login` must print it first), never `main`, and open no pull request.
- Match existing style: `from __future__ import annotations`, a module docstring stating purpose, comments only where the why is not obvious.

## Review Focus

Inputs the spec implies that a happy-path test would miss, most likely first:

1. A guard failing must never call the action tool, for every refusal code (Tasks 1 and 3 assert the tool is not called).
2. A record that changed between the list and the re-read (stale list) is judged on the re-read, not the list (Task 3).
3. A tool that returns without error but leaves the status unchanged is reported as `status_unchanged`, not as success (Tasks 1 and 3).
4. A reference that matches no record, or two records, never acts (Task 3).
5. An action called without `--allow` never reaches the worker: the planner rejects it (Task 3).

---

### Task 1: The pure action table and checks

**Files:**
- Create: `harness/payroll_agent/guarded.py`
- Test: `harness/tests/test_guarded_scaffold.py`

**Interfaces:**
- Consumes (existing): `payroll_agent.cost_report._finite`.
- Produces (used by Tasks 2 and 3): `ACTIONS: dict[str, Action]`; `Action` with fields `name, entity, tool, from_statuses, to_status, example, description, guard, needs_calculated` and method `capability_description() -> str`; `refusal_for(action, record) -> dict | None` (a dict `{"code", "message"}`); `summary(record) -> dict`; `outcome(action, before, after, tool_result) -> dict`; `digest(payload) -> dict`.

- [ ] **Step 1: Write the failing tests**

Create `harness/tests/test_guarded_scaffold.py`:

```python
"""Implementation scaffolding for payroll_agent/guarded.py.

NOT the team's graded tests. These only check that the code behaves as
designed while it is being built.
"""
from __future__ import annotations

import pytest

from payroll_agent import guarded as g

SUBMIT = {"action": "Submit", "from": "draft", "to": "pending_approval"}
LOAN = {"id": "L1", "number": "LOAN-1", "status": "draft", "loan_amount": 1000.0, "emi_amount": 100.0,
        "tenure_months": 12.0, "_transitions": [SUBMIT]}


def test_the_table_has_the_eleven_actions_with_consistent_fields():
    assert set(g.ACTIONS) == {
        "calculate_payrun", "generate_payslips", "send_payslips", "cancel_draft_payrun", "submit_loan",
        "cancel_draft_loan", "submit_salary_revision", "apply_salary_revision", "calculate_settlement",
        "submit_final_settlement", "submit_investment_declaration"}
    for name, action in g.ACTIONS.items():
        assert action.name == name and action.from_statuses and action.tool.startswith(action.entity + ".")
        assert action.example and "NOT performed" in action.capability_description()
    assert {n for n, a in g.ACTIONS.items() if a.to_status is None} == {"generate_payslips", "send_payslips"}
    assert g.ACTIONS["calculate_settlement"].from_statuses == ("draft",)


def test_a_valid_record_has_no_refusal():
    assert g.refusal_for(g.ACTIONS["submit_loan"], LOAN) is None


def test_already_in_the_target_state():
    out = g.refusal_for(g.ACTIONS["submit_loan"], {**LOAN, "status": "pending_approval"})
    assert out["code"] == "already_in_target_state" and "pending_approval" in out["message"]


def test_a_status_the_action_does_not_accept():
    out = g.refusal_for(g.ACTIONS["submit_loan"], {**LOAN, "status": "cancelled"})
    assert out["code"] == "not_in_required_state" and "draft" in out["message"]


def test_the_platform_must_list_the_move_as_legal_now():
    for transitions in ([], None, [{"action": "Cancel", "from": "draft", "to": "cancelled"}], ["junk"]):
        out = g.refusal_for(g.ACTIONS["submit_loan"], {**LOAN, "_transitions": transitions})
        assert out["code"] == "transition_not_available"


@pytest.mark.parametrize("field", ["loan_amount", "emi_amount", "tenure_months"])
def test_loan_guard_needs_positive_amounts(field):
    for bad in (0, -1.0, None, "x", float("nan")):
        out = g.refusal_for(g.ACTIONS["submit_loan"], {**LOAN, field: bad})
        assert out["code"] == "precondition_failed" and field in out["message"]


def test_apply_revision_guard_needs_ctc_and_a_non_rejected_approval():
    apply = {"id": "R1", "number": "REV-1", "status": "approved", "revised_ctc": 100.0, "approval_status": "approved",
             "_transitions": [{"action": "Apply", "from": "approved", "to": "applied"}]}
    assert g.refusal_for(g.ACTIONS["apply_salary_revision"], apply) is None
    assert g.refusal_for(g.ACTIONS["apply_salary_revision"], {**apply, "revised_ctc": 0})["code"] == "precondition_failed"
    rejected = g.refusal_for(g.ACTIONS["apply_salary_revision"], {**apply, "approval_status": "rejected"})
    assert rejected["code"] == "precondition_failed" and "rejected" in rejected["message"]


def test_payslip_actions_check_the_status_but_not_the_transition_list():
    run = {"id": "P1", "number": "PRUN-1", "status": "review", "_transitions": []}
    assert g.refusal_for(g.ACTIONS["generate_payslips"], run) is None
    assert g.refusal_for(g.ACTIONS["send_payslips"], {**run, "status": "draft"})["code"] == "not_in_required_state"


def test_summary_keeps_only_identity():
    assert g.summary(LOAN) == {"id": "L1", "number": "LOAN-1", "status": "draft"}


def test_outcome_reports_a_performed_status_move():
    out = g.outcome(g.ACTIONS["submit_loan"], LOAN, {**LOAN, "status": "pending_approval"}, {"id": "L1"})
    assert out == {"performed": True, "action": "submit_loan", "entity": "EmployeeLoan", "number": "LOAN-1",
                   "id": "L1", "before": "draft", "after": "pending_approval"}


def test_outcome_flags_a_tool_that_returned_without_changing_the_status():
    out = g.outcome(g.ACTIONS["submit_loan"], LOAN, LOAN, {"id": "L1"})
    assert out["error"] is True and out["code"] == "status_unchanged"
    assert out["record"]["status"] == "draft" and out["tool"] == "EmployeeLoan.submit"


def test_outcome_for_a_payslip_action_carries_a_digest_of_the_tool_result():
    run = {"id": "P1", "number": "PRUN-1", "status": "review"}
    out = g.outcome(g.ACTIONS["generate_payslips"], run, run, {"generated": True, "slips": [1, 2, 3], "meta": {"a": 1},
                                                                "_x": 1, "note": "n" * 300})
    assert out["performed"] is True and out["before"] == out["after"] == "review"
    assert out["result"] == {"generated": True, "slips": {"count": 3}, "meta": {"keys": 1}, "note": "n" * 200}


def test_digest_handles_a_payload_that_is_not_a_dict():
    assert g.digest("ok") == {"value": "ok"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd harness && .venv/bin/python -m pytest tests/test_guarded_scaffold.py -q`
Expected: FAIL at collection with `ImportError: cannot import name 'guarded'`.

- [ ] **Step 3: Write the implementation**

Create `harness/payroll_agent/guarded.py`:

```python
"""Guarded actions: the table of changes the agent may make, and the pure checks around them.

Every action is one platform tool that moves one record. The rules here are pure, so they can be
tested without a network: the worker in ``workers.py`` finds the record, re-reads it, applies
``refusal_for``, calls the tool once and re-reads to confirm (``outcome``). A refusal is a result,
not an exception, so the answer step can report "not performed" and why.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .cost_report import _finite as finite

Guard = Callable[[dict[str, Any]], "str | None"]

_SUFFIX = (" This changes shared data (it is not a read) and runs only when authorised. It refuses unless the "
           "record is in the required state and returns what changed; if it returns an error, report that the "
           "action was NOT performed and why, and do not retry. Refer to the record by its number (for example "
           "{example}) or id.")
_PAYSLIP_STATUSES = ("review", "approved", "pending_approval", "paid")


def _positive(*fields: str) -> Guard:
    def check(record: dict[str, Any]) -> str | None:
        bad = [name for name in fields if (finite(record.get(name)) or 0.0) <= 0]
        return f"{' and '.join(bad)} must be positive" if bad else None
    return check


def _not_rejected(record: dict[str, Any]) -> str | None:
    return "approval_status is rejected" if record.get("approval_status") == "rejected" else None


def _both(*guards: Guard) -> Guard:
    def check(record: dict[str, Any]) -> str | None:
        for guard in guards:
            message = guard(record)
            if message:
                return message
        return None
    return check


@dataclass(frozen=True)
class Action:
    name: str
    entity: str
    tool: str
    from_statuses: tuple[str, ...]
    to_status: str | None          # None: the tool changes no status, so there is nothing to verify
    example: str
    description: str
    guard: Guard | None = None
    needs_calculated: bool = False

    def capability_description(self) -> str:
        return self.description + _SUFFIX.format(example=self.example)


ACTIONS: dict[str, Action] = {action.name: action for action in (
    Action("calculate_payrun", "PayRun", "PayRun.calculate_payroll", ("draft",), "review", "PRUN-2026-00013",
           "Calculate payroll for one existing PayRun, moving it from draft to review. To open or recalculate a "
           "run by month, use run_payroll instead."),
    Action("generate_payslips", "PayRun", "PayRun.generate_payslips", _PAYSLIP_STATUSES, None, "PRUN-2026-00011",
           "Mark one calculated PayRun's payslips as generated and report what exists.", needs_calculated=True),
    Action("send_payslips", "PayRun", "PayRun.send_payslips", _PAYSLIP_STATUSES, None, "PRUN-2026-00011",
           "Report one calculated PayRun's payslip distribution list (the platform's send step; it does not "
           "email anyone).", needs_calculated=True),
    Action("cancel_draft_payrun", "PayRun", "PayRun.cancel.draft.cancelled", ("draft",), "cancelled",
           "PRUN-2026-00014", "Cancel one draft PayRun. Permanent for that run."),
    Action("submit_loan", "EmployeeLoan", "EmployeeLoan.submit", ("draft",), "pending_approval", "LOAN-2026-00101",
           "Submit one draft employee loan for approval (draft to pending approval). Refuses a loan whose amount, "
           "EMI or tenure is not positive.", guard=_positive("loan_amount", "emi_amount", "tenure_months")),
    Action("cancel_draft_loan", "EmployeeLoan", "EmployeeLoan.cancel.draft.cancelled", ("draft",), "cancelled",
           "LOAN-2026-00101", "Cancel one draft employee loan. Permanent."),
    Action("submit_salary_revision", "SalaryRevision", "SalaryRevision.submit", ("draft",), "pending_approval",
           "REV-2026-00101", "Submit one draft salary revision for approval. Refuses a revised CTC that is not "
           "positive.", guard=_positive("revised_ctc")),
    Action("apply_salary_revision", "SalaryRevision", "SalaryRevision.apply", ("approved",), "applied",
           "REV-2026-00012", "Apply one approved salary revision to the employee's pay (approved to applied). "
           "This changes an employee's actual salary. Refuses unless the revised CTC is positive and the approval "
           "status is not rejected.", guard=_both(_positive("revised_ctc"), _not_rejected)),
    Action("calculate_settlement", "FinalSettlement", "FinalSettlement.calculate_settlement", ("draft",),
           "calculated", "FFS-2026-00003", "Calculate one draft final settlement, moving it to calculated."),
    Action("submit_final_settlement", "FinalSettlement", "FinalSettlement.submit", ("calculated",),
           "pending_approval", "FFS-2026-00003", "Submit one calculated final settlement for approval. Refuses a "
           "settlement whose net amount is not positive.", guard=_positive("net_settlement")),
    Action("submit_investment_declaration", "InvestmentDeclaration", "InvestmentDeclaration.submit", ("draft",),
           "submitted", "ITD-2026-00013", "Submit one draft investment declaration."),
)}


def summary(record: dict[str, Any]) -> dict[str, Any]:
    return {"id": record.get("id"), "number": record.get("number"), "status": record.get("status")}


def _label(action: Action, record: dict[str, Any]) -> str:
    return f"{action.entity} {record.get('number') or record.get('id')}"


def refusal_for(action: Action, record: dict[str, Any]) -> dict[str, str] | None:
    """Why this action must not run on this freshly read record, or None when every check passes."""
    status = record.get("status")
    label = _label(action, record)
    if action.to_status and status == action.to_status:
        return {"code": "already_in_target_state", "message": f"{label} is already {status}"}
    if status not in action.from_statuses:
        return {"code": "not_in_required_state",
                "message": f"{label} is {status}; this action needs {' or '.join(action.from_statuses)}"}
    if action.to_status:
        moves = record.get("_transitions")
        legal = any(isinstance(move, dict) and move.get("from") == status and move.get("to") == action.to_status
                    for move in (moves if isinstance(moves, list) else []))
        if not legal:
            return {"code": "transition_not_available",
                    "message": f"the platform does not offer {status} to {action.to_status} on {label} right now"}
    message = action.guard(record) if action.guard else None
    if message:
        return {"code": "precondition_failed", "message": f"{label}: {message}"}
    return None


def digest(payload: Any) -> dict[str, Any]:
    """A short view of a tool result: scalars kept, lists and dicts reduced to a count."""
    if not isinstance(payload, dict):
        return {"value": str(payload)[:200]}
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key.startswith("_"):
            continue
        if isinstance(value, (list, tuple)):
            out[key] = {"count": len(value)}
        elif isinstance(value, dict):
            out[key] = {"keys": len(value)}
        elif isinstance(value, str):
            out[key] = value[:200]
        elif value is None or isinstance(value, (int, float, bool)):
            out[key] = value
    return out


def outcome(action: Action, before: dict[str, Any], after: dict[str, Any], tool_result: Any) -> dict[str, Any]:
    """The result of a call that returned without an error, verified against the re-read record."""
    if action.to_status and after.get("status") != action.to_status:
        return {"error": True, "tool": action.tool, "code": "status_unchanged", "record": summary(after),
                "message": (f"{action.tool} returned without an error but {_label(action, after)} is "
                            f"{after.get('status')}, not {action.to_status}; check it before retrying")}
    done = {"performed": True, "action": action.name, "entity": action.entity, "number": before.get("number"),
            "id": before.get("id"), "before": before.get("status"), "after": after.get("status")}
    if action.to_status is None:
        done["result"] = digest(tool_result)
    return done
```

- [ ] **Step 4: Run the tests, then the whole suite and lint**

Run: `cd harness && .venv/bin/python -m pytest tests/test_guarded_scaffold.py -q`
Expected: 22 passed (13 test functions; the two parametrized tests count 3 and 1 extra cases each).

Run: `cd harness && .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check payroll_agent tests`
Expected: all pass (the existing 188 plus these), lint clean. If ruff reports a line length or import order, fix it without changing behaviour. If the test count differs from 22, report the real number.

- [ ] **Step 5: Commit**

```bash
git add harness/payroll_agent/guarded.py harness/tests/test_guarded_scaffold.py
git commit -m "Add the guarded action table and its pure checks

Eleven actions with their tool, legal source statuses and guards, plus the
refusal, outcome and digest logic, all testable without a network.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Record ids and numbers in the lifecycle reports

> **SKIPPED in this branch:** `lifecycle.py` is not on `main` (it is on the unmerged 6a/6b branch `statutory-and-lifecycle-reads`). Do this task after that branch is merged. Tasks 3 and 4 do not depend on it.

**Files:**
- Modify: `harness/payroll_agent/lifecycle.py`
- Modify: `harness/tests/test_lifecycle_scaffold.py`

**Interfaces:**
- Consumes: the existing `lifecycle` rows.
- Produces (used by Task 3 and the planner): every record row in `loans_report` items, `revisions_report` rows, `settlements_report` items and `investments_report` `without_proof.employees` carries `id` and `number`, so the planner can name a record to act on.

- [ ] **Step 1: Write the failing tests**

Append to `harness/tests/test_lifecycle_scaffold.py`:

```python


def test_record_rows_carry_id_and_number_so_a_record_can_be_named():
    loans = lc.loans_report(LOANS, REPAYMENTS, NAMES, TODAY, employee_id="a")
    assert loans["items"][0]["id"] == "L1" and loans["items"][0]["number"] == "LOAN-1"
    revisions = [{"id": "R9", "number": "REV-9", "employee_id": "a", "status": "pending_approval",
                  "effective_date": "2026-09-01"}]
    row = lc.revisions_report(revisions, NAMES, TODAY)["pending"][0]
    assert row["id"] == "R9" and row["number"] == "REV-9"
    settlement = lc.settlements_report([{"id": "S1", "number": "FFS-1", "employee_id": "a", "status": "calculated"}],
                                       NAMES)["items"][0]
    assert settlement["id"] == "S1" and settlement["number"] == "FFS-1"
    declaration = lc.investments_report([{"id": "D9", "number": "ITD-9", "employee_id": "a", "status": "submitted"}],
                                        [], NAMES)["without_proof"]["employees"][0]
    assert declaration["id"] == "D9" and declaration["number"] == "ITD-9"
```

Also change the existing equality assertion in `test_loans_for_one_employee_list_that_employees_items` so it expects the new `id` key: replace

```python
    assert out["items"] == [{"employee_id": "a", "employee_name": "Asha A", "number": "LOAN-1",
```

with

```python
    assert out["items"] == [{"employee_id": "a", "employee_name": "Asha A", "id": "L1", "number": "LOAN-1",
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd harness && .venv/bin/python -m pytest tests/test_lifecycle_scaffold.py -q`
Expected: 2 FAIL (the new test and the changed equality), the rest pass.

- [ ] **Step 3: Add the fields**

In `harness/payroll_agent/lifecycle.py` make these four edits:

(a) In `loans_report`, in the `items = [...]` comprehension, change `"number": loan.get("number"), "status": loan.get("status"),` to `"id": loan.get("id"), "number": loan.get("number"), "status": loan.get("status"),`.

(b) In `revisions_report`, in the inner `row(...)` function, change `return {**_who(revision, names), "number": revision.get("number"),` to `return {**_who(revision, names), "id": revision.get("id"), "number": revision.get("number"),`.

(c) In `settlements_report`, in the `items = [...]` comprehension, change `items = [{**_who(s, names), "status": s.get("status"), "approval_status": s.get("approval_status"),` to `items = [{**_who(s, names), "id": s.get("id"), "number": s.get("number"), "status": s.get("status"), "approval_status": s.get("approval_status"),`.

(d) In `investments_report`, in the `without_proof` comprehension, change `"employees": [{**_who(d, names), "status": d.get("status"),` to `"employees": [{**_who(d, names), "id": d.get("id"), "number": d.get("number"), "status": d.get("status"),`.

- [ ] **Step 4: Run the tests, then the whole suite and lint**

Run: `cd harness && .venv/bin/python -m pytest tests/test_lifecycle_scaffold.py -q`
Expected: all pass.

Run: `cd harness && .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check payroll_agent tests`
Expected: all pass, lint clean.

- [ ] **Step 5: Commit**

```bash
git add harness/payroll_agent/lifecycle.py harness/tests/test_lifecycle_scaffold.py
git commit -m "Carry record id and number in lifecycle report rows

So the planner can name a record it found when it is asked to act on it.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The guarded worker and the capabilities

**Files:**
- Modify: `harness/payroll_agent/capabilities.py` (import, generated capabilities)
- Modify: `harness/payroll_agent/workers.py` (import, helpers, worker factory, registration)
- Test: `harness/tests/test_guarded_worker_scaffold.py`

**Interfaces:**
- Consumes (Tasks 1, 2): `guarded.ACTIONS`, `guarded.refusal_for`, `guarded.summary`, `guarded.outcome`, `Action.capability_description()`. Existing: `workers._call_tool`, `_fetch_all`, `_with_tool`, `scan_checks.is_calculated`, `RunContext`, `TaskSpec`.
- Produces (used by Task 4): eleven registered capabilities; `workers._WORKERS` entries for each action name; `workers._run_guarded(ctx, task, action)`.

- [ ] **Step 1: Write the failing tests**

Create `harness/tests/test_guarded_worker_scaffold.py`:

```python
"""Implementation scaffolding for the guarded action worker and capabilities.

NOT the team's graded tests. A fake AgentSwitch client models records, the platform's legal
transitions and the effect of each action tool, so every guard can be checked without touching
real data. The action tool is never called by any test that expects a refusal.
"""
from __future__ import annotations

import pytest

from evals.transport import ScriptedLLM
from payroll_agent import guarded, workers
from payroll_agent.agentswitch import AgentSwitchToolError
from payroll_agent.capabilities import CapabilityError, default_registry
from payroll_agent.core.live_graph import TaskSpec
from payroll_agent.run import run_goal

LEGAL = {("EmployeeLoan", "draft"): [{"action": "Submit", "from": "draft", "to": "pending_approval"},
                                     {"action": "Cancel", "from": "draft", "to": "cancelled"}],
         ("SalaryRevision", "approved"): [{"action": "Apply", "from": "approved", "to": "applied"}],
         ("PayRun", "draft"): [{"action": "Calculate Payroll", "from": "draft", "to": "review"},
                               {"action": "Cancel", "from": "draft", "to": "cancelled"}]}
EFFECTS = {"EmployeeLoan.submit": ("EmployeeLoan", "pending_approval"),
           "EmployeeLoan.cancel.draft.cancelled": ("EmployeeLoan", "cancelled"),
           "SalaryRevision.apply": ("SalaryRevision", "applied"),
           "PayRun.calculate_payroll": ("PayRun", "review"),
           "PayRun.cancel.draft.cancelled": ("PayRun", "cancelled")}


class FakeAgentSwitch:
    def __init__(self, records, *, stuck=(), failing=(), stale=None, short_total=None):
        self.records = {entity: [dict(r) for r in rows] for entity, rows in records.items()}
        self.stuck, self.failing, self.stale, self.short_total = set(stuck), set(failing), stale or {}, short_total
        self.calls: list[tuple[str, dict]] = []

    @property
    def acted(self):
        return [name for name, _ in self.calls if name.rpartition(".")[2] not in ("list", "get")]

    async def close(self):
        return None

    def _row(self, entity, rid):
        return next(r for r in self.records[entity] if r["id"] == rid)

    async def call_tool(self, name, arguments=None, *, jurisdiction):
        arguments = arguments or {}
        self.calls.append((name, arguments))
        entity, _, action = name.partition(".")
        if name in self.failing:
            raise AgentSwitchToolError(name, -32002, "platform refused")
        if action == "list":
            rows = self.records.get(entity, [])
            rows = [r for r in rows if all(r.get(k) == v for k, v in arguments.items() if k == "payrun_id")]
            offset, limit = arguments.get("offset", 0), min(arguments.get("limit", 10), 100)
            total = len(rows) if self.short_total is None else self.short_total
            return {"data": rows[offset:offset + limit], "limit": limit, "offset": offset, "total": total}
        if action == "get":
            row = {**self._row(entity, arguments["id"]), **self.stale.get((entity, arguments["id"]), {})}
            return {**row, "_transitions": LEGAL.get((entity, row["status"]), [])}
        if name in EFFECTS:
            target_entity, new_status = EFFECTS[name]
            if name not in self.stuck:
                self._row(target_entity, arguments["id"])["status"] = new_status
            return {"id": arguments["id"], "ok": True}
        if entity == "PayRun" and action in ("generate_payslips", "send_payslips"):
            return {"id": arguments["id"], "generated": True, "slips": [1, 2]}
        raise AssertionError(f"unexpected tool {name}")


def loan(**fields):
    return {"id": "L1", "number": "LOAN-1", "status": "draft", "loan_amount": 1000.0, "emi_amount": 100.0,
            "tenure_months": 12.0, **fields}


def act(fake, name, reference="LOAN-1"):
    ctx = workers.RunContext(run_id="r", store=None, llm=None, agentswitch=fake)
    return workers._WORKERS[name](ctx, TaskSpec("a", name, {"jurisdiction": "IN", "reference": reference}))


async def test_a_valid_submit_calls_the_tool_once_and_verifies_the_new_status():
    fake = FakeAgentSwitch({"EmployeeLoan": [loan()]})
    result = await act(fake, "submit_loan")
    assert result == {"performed": True, "action": "submit_loan", "entity": "EmployeeLoan", "number": "LOAN-1",
                      "id": "L1", "before": "draft", "after": "pending_approval"}
    assert fake.acted == ["EmployeeLoan.submit"]


@pytest.mark.parametrize("fields, code", [
    ({"status": "cancelled"}, "not_in_required_state"),
    ({"status": "pending_approval"}, "already_in_target_state"),
    ({"loan_amount": -1.0}, "precondition_failed"),
])
async def test_a_failed_guard_refuses_and_never_calls_the_action_tool(fields, code):
    fake = FakeAgentSwitch({"EmployeeLoan": [loan(**fields)]})
    result = await act(fake, "submit_loan")
    assert result["error"] is True and result["code"] == code and result["tool"] == "EmployeeLoan.submit"
    assert result["message"].endswith("nothing was changed") and result["record"]["number"] == "LOAN-1"
    assert fake.acted == []


async def test_a_move_the_platform_does_not_list_is_refused(monkeypatch):
    monkeypatch.setitem(LEGAL, ("EmployeeLoan", "draft"), [])
    fake = FakeAgentSwitch({"EmployeeLoan": [loan()]})
    result = await act(fake, "submit_loan")
    assert result["code"] == "transition_not_available" and fake.acted == []


async def test_a_record_that_changed_after_the_list_is_judged_on_the_reread():
    fake = FakeAgentSwitch({"EmployeeLoan": [loan()]}, stale={("EmployeeLoan", "L1"): {"status": "cancelled"}})
    result = await act(fake, "submit_loan")
    assert result["code"] == "not_in_required_state" and fake.acted == []


async def test_a_reference_with_no_match_or_two_matches_never_acts():
    missing = FakeAgentSwitch({"EmployeeLoan": [loan()]})
    assert (await act(missing, "submit_loan", "LOAN-404"))["code"] == "record_not_found" and missing.acted == []
    twice = FakeAgentSwitch({"EmployeeLoan": [loan(), loan(id="L2")]})
    result = await act(twice, "submit_loan")
    assert result["code"] == "ambiguous_reference" and twice.acted == []


async def test_a_record_can_be_named_by_id():
    fake = FakeAgentSwitch({"EmployeeLoan": [loan()]})
    assert (await act(fake, "submit_loan", "L1"))["performed"] is True


async def test_a_tool_error_is_returned_unchanged():
    fake = FakeAgentSwitch({"EmployeeLoan": [loan()]}, failing=("EmployeeLoan.submit",))
    result = await act(fake, "submit_loan")
    assert result["error"] is True and result["tool"] == "EmployeeLoan.submit" and result["code"] == -32002


async def test_a_tool_that_returns_without_changing_the_status_is_reported():
    fake = FakeAgentSwitch({"EmployeeLoan": [loan()]}, stuck=("EmployeeLoan.submit",))
    result = await act(fake, "submit_loan")
    assert result["error"] is True and result["code"] == "status_unchanged"
    assert result["record"]["status"] == "draft" and fake.acted == ["EmployeeLoan.submit"]


async def test_a_short_entity_list_is_scan_incomplete_named_for_the_capability():
    result = await act(FakeAgentSwitch({"EmployeeLoan": [loan()]}, short_total=50), "submit_loan")
    assert result["code"] == "scan_incomplete" and result["tool"] == "submit_loan"


async def test_applying_a_revision_needs_an_approved_non_rejected_record():
    base = {"id": "R1", "number": "REV-1", "status": "approved", "revised_ctc": 100.0, "approval_status": "approved"}
    ok = FakeAgentSwitch({"SalaryRevision": [dict(base)]})
    assert (await act(ok, "apply_salary_revision", "REV-1"))["after"] == "applied"
    rejected = FakeAgentSwitch({"SalaryRevision": [{**base, "approval_status": "rejected"}]})
    result = await act(rejected, "apply_salary_revision", "REV-1")
    assert result["code"] == "precondition_failed" and rejected.acted == []


async def test_payrun_actions_calculate_and_cancel_a_draft_only():
    run = {"id": "P1", "number": "PRUN-1", "status": "draft"}
    assert (await act(FakeAgentSwitch({"PayRun": [dict(run)]}), "calculate_payrun", "PRUN-1"))["after"] == "review"
    assert (await act(FakeAgentSwitch({"PayRun": [dict(run)]}), "cancel_draft_payrun", "PRUN-1"))["after"] == "cancelled"
    pending = FakeAgentSwitch({"PayRun": [{**run, "status": "pending_approval"}]})
    assert (await act(pending, "calculate_payrun", "PRUN-1"))["code"] == "not_in_required_state" and pending.acted == []


async def test_payslip_actions_need_calculated_rows_and_return_a_digest():
    run = {"id": "P1", "number": "PRUN-1", "status": "review"}
    rows = [{"id": "s1", "payrun_id": "P1", "net_pay": 900.0}]
    ok = FakeAgentSwitch({"PayRun": [dict(run)], "PayRunEmployee": rows})
    result = await act(ok, "generate_payslips", "PRUN-1")
    assert result["performed"] is True and result["result"] == {"id": "P1", "generated": True, "slips": {"count": 2}}
    empty = FakeAgentSwitch({"PayRun": [dict(run)], "PayRunEmployee": [{"id": "s1", "payrun_id": "P1", "net_pay": 0}]})
    refused = await act(empty, "send_payslips", "PRUN-1")
    assert refused["code"] == "run_not_calculated" and empty.acted == []
    draft = FakeAgentSwitch({"PayRun": [{**run, "status": "draft"}], "PayRunEmployee": rows})
    assert (await act(draft, "generate_payslips", "PRUN-1"))["code"] == "not_in_required_state"


def test_every_action_is_a_registered_side_effect_capability_with_a_worker():
    registry = default_registry()
    for name, action in guarded.ACTIONS.items():
        capability = registry.get(name)
        assert capability.side_effect is True and name in workers._WORKERS
        assert set(capability.arguments) == {"jurisdiction", "reference"}
        assert capability.description == action.capability_description()
    registry.validate("submit_loan", {"jurisdiction": "IN", "reference": "LOAN-1"}, known_values={"LOAN-1"})
    with pytest.raises(CapabilityError):
        registry.validate("submit_loan", {"jurisdiction": "IN", "reference": "LOAN-1"}, known_values=set())


def plan(capability, reference="LOAN-1"):
    return {"add": [{"id": "act", "capability": capability, "arguments": {"jurisdiction": "IN", "reference": reference},
                     "depends_on": []}], "cancel": [], "finish": False, "reason": "do it"}


DECLINE = {"add": [{"id": "decline", "capability": "decline_request",
                    "arguments": {"reason_code": "not_permitted", "explanation": "that action is not authorised"},
                    "depends_on": []}], "cancel": [], "finish": False, "reason": "not authorised"}
ANSWER = {"add": [{"id": "answer", "capability": "answer_with_evidence", "arguments": {"query": "done?"},
                   "depends_on": ["act"]}], "cancel": [], "finish": False, "reason": "answer"}


async def run_with(steps, fake, allow):
    llm = ScriptedLLM(steps)
    result = await run_goal("Submit loan LOAN-1 for approval", allowed_side_effects=allow,
                            initial_evidence={"jurisdiction": "IN"}, llm=llm, agentswitch=fake)
    return llm, result


async def test_an_action_without_authority_is_rejected_by_the_planner_and_never_runs():
    fake = FakeAgentSwitch({"EmployeeLoan": [loan()]})
    llm, result = await run_with([{"reply": plan("submit_loan")}, {"reply": DECLINE}], fake, allow=set())
    assert len(llm.calls) == 2 and fake.acted == [] and result["declined"] is True


async def test_an_authorised_action_runs_through_the_planner_once():
    fake = FakeAgentSwitch({"EmployeeLoan": [loan()]})
    steps = [{"reply": plan("submit_loan")}, {"reply": ANSWER}, {"reply": {"ready": True, "missing": [], "reason": "ok"}},
             {"reply": "The loan was submitted."}]
    _, result = await run_with(steps, fake, allow={"submit_loan"})
    assert fake.acted == ["EmployeeLoan.submit"] and result["answer"] == "The loan was submitted."
    assert result["nodes"]["act"]["result"]["performed"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd harness && .venv/bin/python -m pytest tests/test_guarded_worker_scaffold.py -q`
Expected: FAIL (`KeyError` on `workers._WORKERS["submit_loan"]` and `unknown capability`).

- [ ] **Step 3: Generate the capabilities**

In `harness/payroll_agent/capabilities.py`:

(a) Add `from . import guarded` to the imports, after the line `from typing import Any`.

(b) In `default_registry()`, find the end of the registry list, which is the closing of the `decline_request` entry followed by `    ])`:

```python
            role="answer", terminal_for=("text",), needs_evidence=False,
        ),
    ])
```

and replace it with:

```python
            role="answer", terminal_for=("text",), needs_evidence=False,
        ),
        # One capability per guarded action: authority is per action (`--allow <name>`), and the
        # planner hides a side-effect capability that is not allowed.
        *[Capability(action.name, action.capability_description(),
                     {"jurisdiction": _JURISDICTION,
                      "reference": string("The record's number (for example " + action.example + ") or id.",
                                          maximum=200, format="id")},
                     side_effect=True)
          for action in guarded.ACTIONS.values()],
    ])
```

- [ ] **Step 4: Add the worker**

In `harness/payroll_agent/workers.py`:

(a) Change the import line `from . import cost_report, lifecycle, scan_checks, statutory` to:

```python
from . import cost_report, guarded, lifecycle, scan_checks, statutory
```

(b) Insert immediately before the comment line `# run_payroll rewrites the slips of whichever run it reuses`:

```python
async def _find_record(ctx: RunContext, action: guarded.Action, reference: str,
                       jurisdiction: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The one record whose number or id is ``reference``, or an error result. The list tools
    have no number filter, so the entity is listed and matched here."""
    rows, problem = await _fetch_all(ctx, f"{action.entity}.list", {}, jurisdiction)
    if problem:
        return None, _with_tool(problem, action.name)
    hits = [row for row in rows if reference in (row.get("number"), row.get("id"))]
    if len(hits) == 1:
        return hits[0], None
    return None, {"error": True, "tool": action.tool, "record": None,
                  "code": "ambiguous_reference" if hits else "record_not_found",
                  "message": f"{len(hits)} {action.entity} records match {reference!r}; nothing was changed"}


def _refused(action: guarded.Action, refusal: dict[str, str], record: dict[str, Any]) -> dict[str, Any]:
    return {"error": True, "tool": action.tool, "code": refusal["code"],
            "message": f"{refusal['message']}; nothing was changed", "record": guarded.summary(record)}


async def _run_guarded(ctx: RunContext, task: TaskSpec, action: guarded.Action) -> dict[str, Any]:
    """Find, re-read, check, call once, re-read, verify. The action tool is reached only after
    every check passes; a refusal is a result the answer step reports as "not performed"."""
    jurisdiction, reference = task.input["jurisdiction"], task.input["reference"]
    found, error = await _find_record(ctx, action, reference, jurisdiction)
    if error:
        return error
    # The rows are shared and may have changed since the list: judge the fresh read.
    before = await _call_tool(ctx, f"{action.entity}.get", {"id": found["id"]}, jurisdiction)
    if before.get("error"):
        return before
    refusal = guarded.refusal_for(action, before)
    if refusal is None and action.needs_calculated:
        rows, problem = await _fetch_all(ctx, "PayRunEmployee.list", {"payrun_id": before["id"]}, jurisdiction)
        if problem:
            return _with_tool(problem, action.name)
        if not scan_checks.is_calculated(rows):
            refusal = {"code": "run_not_calculated", "message": f"{action.entity} {before.get('number')} "
                                                                "has no calculated payslip rows"}
    if refusal:
        return _refused(action, refusal, before)
    result = await _call_tool(ctx, action.tool, {"id": before["id"]}, jurisdiction)
    if result.get("error"):
        return result
    after = await _call_tool(ctx, f"{action.entity}.get", {"id": before["id"]}, jurisdiction)
    if after.get("error"):
        return after
    return guarded.outcome(action, before, after, result)


def _guarded_worker(action: guarded.Action) -> Callable[[RunContext, TaskSpec], Awaitable[dict[str, Any]]]:
    async def worker(ctx: RunContext, task: TaskSpec) -> dict[str, Any]:
        return await _run_guarded(ctx, task, action)
    worker.__name__ = f"run_{action.name}"
    return worker


```

(c) Insert immediately before the line `def build_skills(ctx: RunContext) -> dict[str, Skill]:` (after the `_WORKERS` dict):

```python
_WORKERS.update({name: _guarded_worker(action) for name, action in guarded.ACTIONS.items()})


```

- [ ] **Step 5: Run the tests, then the whole suite and lint**

Run: `cd harness && .venv/bin/python -m pytest tests/test_guarded_worker_scaffold.py -q`
Expected: 18 passed (16 test functions, one parametrized with 3 cases, minus overlaps: report the real number if it differs).

Run: `cd harness && .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check payroll_agent tests`
Expected: all pass, lint clean. If ruff reports an import order or line length issue, fix it without changing behaviour. If a pre-existing test asserts the number of capabilities, update it and say so in the commit message.

- [ ] **Step 6: Commit**

```bash
git add harness/payroll_agent/capabilities.py harness/payroll_agent/workers.py harness/tests/test_guarded_worker_scaffold.py
git commit -m "Add the generic guarded worker and eleven guarded action capabilities

Capabilities are generated from the action table; each runs only when
authorised, finds the record by number or id, re-reads it, checks status,
the platform's legal transitions and an action guard, calls the tool once,
and verifies the new status.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Real-tenant refusal checks, live check, docs and push

**Files:**
- Create: `harness/evals/scripts/scaffold_guarded_payrun_refusals.json`, `harness/evals/scripts/scaffold_guarded_record_refusals.json`
- Modify: `harness/evals/tasks/scaffold.jsonl` (append two lines)
- Modify: `harness/WORKFLOWS.md`, `harness/NEXT_STEPS.md`
- Add to git: the spec and this plan

**Interfaces:**
- Consumes: Tasks 1 to 3, the runner and verifiers. Real references (2026-10-04, India): `PRUN-2026-00012` (pending_approval), `PRUN-2026-00011` (paid), `LOAN-2026-00081` (cancelled), `REV-2026-00101` (draft), `FFS-2026-00001` (approved).
- Produces: two scaffold eval tasks that run **refusals** against the real tenant, recorded live results, updated docs and the pushed branch.

- [ ] **Step 1: Write the two scripts**

Create `harness/evals/scripts/scaffold_guarded_payrun_refusals.json`:

```json
{
  "name": "scaffold_guarded_payrun_refusals",
  "note": "Scaffold only. Both actions target runs whose real state the platform would also reject (pending_approval, paid), so each must be refused by the guard and nothing changes.",
  "steps": [
    {"reply": {"add": [{"id": "calc", "capability": "calculate_payrun",
                        "arguments": {"jurisdiction": "IN", "reference": "PRUN-2026-00012"}, "depends_on": []}],
               "cancel": [], "finish": false, "reason": "try to calculate the pending August run"}},
    {"reply": {"add": [{"id": "cancel", "capability": "cancel_draft_payrun",
                        "arguments": {"jurisdiction": "IN", "reference": "PRUN-2026-00011"}, "depends_on": ["calc"]}],
               "cancel": [], "finish": false, "reason": "try to cancel the paid July run"}},
    {"reply": {"add": [{"id": "answer", "capability": "answer_with_evidence",
                        "arguments": {"query": "Calculate payroll for PRUN-2026-00012, then cancel PRUN-2026-00011."},
                        "depends_on": ["cancel"]}],
               "cancel": [], "finish": false, "reason": "both were refused; report that"}},
    {"reply": {"ready": true, "missing": [], "reason": "both outcomes are refusals, which are ready evidence"}},
    {"reply": "Scripted answer: neither action was performed; see the refusals in the evidence."}
  ]
}
```

Create `harness/evals/scripts/scaffold_guarded_record_refusals.json`:

```json
{
  "name": "scaffold_guarded_record_refusals",
  "note": "Scaffold only. Each action targets a record whose real state the platform would also reject (cancelled loan, draft revision, approved settlement), so each must be refused and nothing changes.",
  "steps": [
    {"reply": {"add": [{"id": "loan", "capability": "submit_loan",
                        "arguments": {"jurisdiction": "IN", "reference": "LOAN-2026-00081"}, "depends_on": []}],
               "cancel": [], "finish": false, "reason": "try to submit the cancelled loan"}},
    {"reply": {"add": [{"id": "revision", "capability": "apply_salary_revision",
                        "arguments": {"jurisdiction": "IN", "reference": "REV-2026-00101"}, "depends_on": ["loan"]}],
               "cancel": [], "finish": false, "reason": "try to apply the draft revision"}},
    {"reply": {"add": [{"id": "settlement", "capability": "submit_final_settlement",
                        "arguments": {"jurisdiction": "IN", "reference": "FFS-2026-00001"}, "depends_on": ["revision"]}],
               "cancel": [], "finish": false, "reason": "try to submit the approved settlement"}},
    {"reply": {"add": [{"id": "answer", "capability": "answer_with_evidence",
                        "arguments": {"query": "Submit loan LOAN-2026-00081, apply salary revision REV-2026-00101, and submit settlement FFS-2026-00001."},
                        "depends_on": ["settlement"]}],
               "cancel": [], "finish": false, "reason": "all three were refused; report that"}},
    {"reply": {"ready": true, "missing": [], "reason": "all outcomes are refusals, which are ready evidence"}},
    {"reply": "Scripted answer: none of the three actions was performed; see the refusals in the evidence."}
  ]
}
```

- [ ] **Step 2: Append the two scaffold tasks**

Append these two lines to `harness/evals/tasks/scaffold.jsonl` (one JSON object per line, no blank line between). They are `mode: "live"` with an `allow` list, so they only run with `--live`:

```json
{"id":"scaffold_guarded_payrun_refusals","family":"refusal","authored_by":"scaffold","goal":"Calculate payroll for PRUN-2026-00012, then cancel PRUN-2026-00011.","jurisdiction":"IN","allow":["calculate_payrun","cancel_draft_payrun"],"mode":"live","transport":"scripted","script":"scripts/scaffold_guarded_payrun_refusals.json","watch":[{"tool":"PayRun.list","args":{"limit":100},"key":"id","fields":["status","pay_period_start"]}],"verifiers":[{"type":"no_mutation"},{"type":"tool_not_called","tool":"PayRun.calculate_payroll"},{"type":"tool_not_called","tool":"PayRun.cancel.draft.cancelled"},{"type":"node_result","skill":"calculate_payrun","path":"code","expect":"not_in_required_state"},{"type":"node_result","skill":"cancel_draft_payrun","path":"code","expect":"not_in_required_state"},{"type":"refusal"}]}
{"id":"scaffold_guarded_record_refusals","family":"refusal","authored_by":"scaffold","goal":"Submit loan LOAN-2026-00081, apply salary revision REV-2026-00101, and submit settlement FFS-2026-00001.","jurisdiction":"IN","allow":["submit_loan","apply_salary_revision","submit_final_settlement"],"mode":"live","transport":"scripted","script":"scripts/scaffold_guarded_record_refusals.json","watch":[{"tool":"EmployeeLoan.list","args":{"limit":100},"key":"id","fields":["status"]},{"tool":"SalaryRevision.list","args":{"limit":100},"key":"id","fields":["status"]},{"tool":"FinalSettlement.list","args":{"limit":100},"key":"id","fields":["status"]}],"verifiers":[{"type":"no_mutation"},{"type":"tool_not_called","tool":"EmployeeLoan.submit"},{"type":"tool_not_called","tool":"SalaryRevision.apply"},{"type":"tool_not_called","tool":"FinalSettlement.submit"},{"type":"node_result","skill":"submit_loan","path":"code","expect":"not_in_required_state"},{"type":"node_result","skill":"apply_salary_revision","path":"code","expect":"not_in_required_state"},{"type":"node_result","skill":"submit_final_settlement","path":"code","expect":"not_in_required_state"},{"type":"refusal"}]}
```

- [ ] **Step 3: Run both against the real tenant (refusals only)**

These are armed (`mode: live`), so before running, re-read each record's real status and confirm the platform would also reject the action:

```bash
cd harness && .venv/bin/python - <<'EOF'
import asyncio
from dotenv import load_dotenv; load_dotenv(".env")
from payroll_agent.agentswitch import AgentSwitchClient
CHECKS = [("PayRun", "PRUN-2026-00012", "pending_approval"), ("PayRun", "PRUN-2026-00011", "paid"),
          ("EmployeeLoan", "LOAN-2026-00081", "cancelled"), ("SalaryRevision", "REV-2026-00101", "draft"),
          ("FinalSettlement", "FFS-2026-00001", "approved")]
async def main():
    c = AgentSwitchClient()
    try:
        for entity, number, want in CHECKS:
            rows = []
            for offset in (0, 100):
                rows += (await c.call_tool(f"{entity}.list", {"limit": 100, "offset": offset}, jurisdiction="IN"))["data"]
            row = next(r for r in rows if r["number"] == number)
            print(f"{entity:16} {number:16} status={row['status']:17} expected {want}: {'OK' if row['status'] == want else 'CHANGED, STOP'}")
    finally:
        await c.close()
asyncio.run(main())
EOF
```
Expected: all five say `OK`. If any says `CHANGED, STOP`, do not run the tasks: choose another record in the same unreachable state, update the scripts and tasks, and tell the user.

Then run:
```bash
.venv/bin/python -m evals.runner --tasks evals/tasks/scaffold.jsonl --task-id scaffold_guarded_payrun_refusals --live
.venv/bin/python -m evals.runner --tasks evals/tasks/scaffold.jsonl --task-id scaffold_guarded_record_refusals --live
```
Expected: `pass` for each, exit code 0. Then confirm from the run records that no action tool was called:
```bash
.venv/bin/python - <<'EOF'
import json, glob
for task in ("scaffold_guarded_payrun_refusals", "scaffold_guarded_record_refusals"):
    rec = json.load(open(sorted(glob.glob(f"evals/runs/*/{task}.json"))[-1]))
    print(task, "mutating calls:", [c["tool"] for c in rec["tool_calls"] if c["mutating"]] or "NONE", "| mode:", rec["mode"])
EOF
```
Expected: `NONE` for both. If either lists a mutating call, stop and report it to the user immediately. If a task reports `fail`, read `evals/runs/<latest>/<id>.score.json` and fix the cause; never loosen a verifier.

- [ ] **Step 4: Live check with the real models (refusals only; needs the gateway and provider setup in NEXT_STEPS.md)**

Confirm the gateway is up: `curl -s http://127.0.0.1:8111/healthz`. Run each request with only the matching capability allowed, plus one without authority:

```bash
cd harness && cat > /private/tmp/claude-501/guarded_live.py <<'EOF'
import asyncio
from payroll_agent.run import run_goal
from evals.recorder import RecordingClient
from payroll_agent.agentswitch import AgentSwitchClient
CASES = [("Calculate payroll for PRUN-2026-00012", {"calculate_payrun"}),
         ("Cancel the payroll run PRUN-2026-00011", {"cancel_draft_payrun"}),
         ("Submit loan LOAN-2026-00081 for approval", {"submit_loan"}),
         ("Apply salary revision REV-2026-00101", {"apply_salary_revision"}),
         ("Submit final settlement FFS-2026-00001 for approval", {"submit_final_settlement"}),
         ("Submit loan LOAN-2026-00081 for approval", set())]
async def main():
    for goal, allow in CASES:
        inner = AgentSwitchClient(); rec = RecordingClient(inner)
        try:
            r = await run_goal(goal, allowed_side_effects=allow, initial_evidence={"jurisdiction": "IN"}, agentswitch=rec)
        finally:
            await inner.close()
        mutating = [c["tool"] for c in rec.calls if c["mutating"]]
        codes = [n["result"].get("code") for n in r["nodes"].values() if isinstance(n.get("result"), dict) and n["result"].get("error")]
        print(f"{goal!r} allow={sorted(allow) or 'NONE'}\n   skills={[n['skill'] for n in r['nodes'].values()]} refusal codes={codes} declined={r['declined']} MUTATING CALLS={mutating or 'none'}\n   answer: {(r['answer'] or 'NONE')[:230]!r}")
asyncio.run(main())
EOF
.venv/bin/python /private/tmp/claude-501/guarded_live.py
```
Expected: every line shows `MUTATING CALLS=none`. The five authorised requests should end in a guard refusal code (`not_in_required_state`) and an answer that says the action was not performed; the unauthorised one should be declined or answered without any action. Record what actually happened, including any planner failure; do not tune prompts without telling the user.

- [ ] **Step 5: Update the docs**

In `harness/WORKFLOWS.md`, in section B and section F, change the rows for the actions this plan builds from `todo` to `built (<capability>)`: dry-run calculate (`calculate_payrun`), generate payslips (`generate_payslips`), send payslips (`send_payslips`), cancel a draft run (`cancel_draft_payrun`), submit a loan (`submit_loan`), cancel a draft loan (`cancel_draft_loan`), submit and apply a salary revision (`submit_salary_revision`, `apply_salary_revision`), calculate and submit a settlement (`calculate_settlement`, `submit_final_settlement`), submit an investment declaration (`submit_investment_declaration`). Add one sentence under section B: these change shared data with no undo; each runs only with its own `--allow`; they were verified with a fake client and with real-tenant refusals only, and no real change has been executed.

In `harness/NEXT_STEPS.md`, record 6c as built in the B/E/F roadmap item, with a short "Result" line holding the real outcome of Steps 3 and 4 (which refusals fired, the codes, that no mutating call was made, and any planner failure) and one line stating plainly that the allowed paths have not been run against real data. Note that `calculate_settlement` accepts a draft only. List any follow-ups.

- [ ] **Step 6: Final run, commit and push the branch**

Run: `cd harness && .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check payroll_agent evals tests`
Expected: all pass, lint clean.

```bash
cd ..
git add harness/evals harness/WORKFLOWS.md harness/NEXT_STEPS.md docs/superpowers/specs/2026-10-04-guarded-actions-design.md docs/superpowers/plans/2026-10-04-guarded-actions.md
git commit -m "Add scaffold refusal tasks for the guarded actions; spec, plan and docs

Two scaffold tasks run guarded actions against the real tenant at records
whose real state the platform would also reject, and check that every one
is refused and nothing changes. The allowed paths are not executed against
real data.

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
gh api user --jq .login        # must print pragsyy1729
git push -u origin guarded-actions
git status -sb | head -1
```
Expected: the login prints `pragsyy1729`, the branch is pushed, and `origin/main` is unchanged. Do not push `main` and do not open a pull request.

---

## Self-review (against the spec)

- **Purpose, decisions, non-goals:** the table of eleven actions (Task 1) with per-action authority (Task 3 capabilities, `side_effect=True`), one record per call, no approve/bulk.
- **Guard pipeline (find, re-read, already-there, status, platform transitions, action guard, calculated rows, one call, verify):** Task 1 tests for the pure checks; Task 3 tests for find/ambiguity/stale/error/`status_unchanged`/payslips, each asserting the tool is not called on any refusal.
- **Refusal and result shapes:** Task 1 `outcome`/`digest` tests; Task 3 assertions on `code`, `message`, `record`.
- **Lifecycle ids and numbers:** Task 2.
- **Authority hidden from the planner:** Task 3 `test_an_action_without_authority_is_rejected_by_the_planner_and_never_runs` and the authorised run.
- **Testing and grading split and safety:** every test file labelled scaffold; eval tasks `authored_by: "scaffold"`; real-tenant work is refusals only, with a pre-check that each record is in a state the platform would also reject and a hard stop if any mutating call appears (Task 4 Steps 3 and 4).
- **Limitations:** recorded in WORKFLOWS.md and NEXT_STEPS.md (Task 4 Step 5).
- **Type consistency:** `Action` fields, `refusal_for -> {"code","message"} | None`, `outcome(action, before, after, tool_result)`, `_run_guarded(ctx, task, action)` and `_WORKERS[name]` are used identically across Tasks 1 to 4.
- **Placeholders:** none. Counts and live results in Task 4 come from running the steps.
- **Git:** identity, branch-only push and the `pragsyy1729` check are in Global Constraints and Step 6.

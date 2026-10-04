# Guarded Actions: Design

Date: 2026-10-04. Status: draft for review. Sub-project 6c (the last of 6a, 6b, 6c).
Families B and F (write side) in `harness/WORKFLOWS.md`.

## Purpose

Let the agent change records, safely. These actions write to a shared tenant that
this seat cannot undo, so every one goes through the same guard pipeline: explicit
authority, a fresh read, a status check, the platform's own legal-transition list,
an action-specific guard, one call, and a verified result. A guard that stops a bad
request is a result the agent reports, never an exception.

## Decisions already made

- **Scope B (user choice):** everything, including applying a salary revision and
  the cancel actions, each behind its own explicit `--allow`.
- **One capability per action**, all `side_effect: true`. The planner hides a
  side-effect capability unless it is in `--allow`, so authority is per action and
  the planner's menu stays short when nothing is allowed.
- **One generic guarded worker** driven by an action table, not eleven hand-written
  functions.
- **Live testing is refusals only.** The allowed paths are proven with a fake client.
  A real change to shared data happens only if the user names a specific record and
  approves it.
- Graded tests are written by the team, not by Claude.

## Non-goals

- Bulk actions ("submit all drafts"): one record per call.
- Approving or rejecting anything: this seat has no approve tool, and the platform
  enforces segregation of duties.
- Creating, editing or deleting records; recalculating an already-calculated
  settlement (the platform lists no such transition).
- Emailing payslips: `send_payslips` only reports a distribution list.

## What the platform provides (probed 2026-10-04)

- A single-record `.get` returns `_transitions`: the legal next moves, each with
  `action`, `from` and `to`. The September draft run lists "Calculate Payroll"
  (draft to review) and "Cancel" (draft to cancelled); a pending or paid run lists
  none. Loans, revisions, settlements and declarations list their own.
- Tool descriptions state the transitions: `PayRun.calculate_payroll` draft to
  review; `EmployeeLoan.submit` draft to pending approval; `SalaryRevision.apply`
  approved to applied; `FinalSettlement.calculate_settlement` calculates a draft
  settlement; `PayRun.generate_payslips` marks payslips generated;
  `PayRun.send_payslips` reports a distribution list.
- Cancel tools are named by source state: `PayRun.cancel.draft.cancelled`,
  `EmployeeLoan.cancel.draft.cancelled`.
- The list tools accept no `number` filter, so a record is found by listing and
  matching its `number` or `id` in code.

## The actions

All take `jurisdiction` and `reference` (the record's number, for example
`LOAN-2026-00101`, or its id).

| Capability | Tool | From to | Extra guard |
|---|---|---|---|
| `calculate_payrun` | `PayRun.calculate_payroll` | draft to review | none |
| `generate_payslips` | `PayRun.generate_payslips` | no status change | run past draft, with calculated rows |
| `send_payslips` | `PayRun.send_payslips` | no status change | same |
| `cancel_draft_payrun` | `PayRun.cancel.draft.cancelled` | draft to cancelled | none |
| `submit_loan` | `EmployeeLoan.submit` | draft to pending_approval | loan amount, EMI and tenure positive |
| `cancel_draft_loan` | `EmployeeLoan.cancel.draft.cancelled` | draft to cancelled | none |
| `submit_salary_revision` | `SalaryRevision.submit` | draft to pending_approval | revised CTC positive |
| `apply_salary_revision` | `SalaryRevision.apply` | approved to applied | revised CTC positive; approval status not rejected |
| `calculate_settlement` | `FinalSettlement.calculate_settlement` | draft to calculated | none |
| `submit_final_settlement` | `FinalSettlement.submit` | calculated to pending_approval | net settlement positive |
| `submit_investment_declaration` | `InvestmentDeclaration.submit` | draft to submitted | none |

`calculate_settlement` accepts a draft only: the platform lists no recalculation
transition for a calculated settlement, and recalculating rewrites its figures.
The existing `run_payroll` and `submit_payrun_for_approval` are unchanged.

## Components

1. **`payroll_agent/guarded.py`**, pure: the `Action` table, the guards, and
   `refusal_for(action, record)`, `outcome(action, before, after, tool_result)`,
   `summary(record)` and `digest(payload)`.
2. **`run_guarded`** worker in `workers.py`, driven by the table: find, re-read,
   check, call once, re-read, verify. One worker per action is built from it and
   registered in `_WORKERS`.
3. **Capabilities** in `capabilities.py`, generated from the table, each
   `side_effect: true` with arguments `jurisdiction` and `reference`
   (`format="id"`, so the reference must come from the goal or an earlier outcome).
4. **Deferred:** `lifecycle.py` rows gaining `id` and `number`, so the planner can name
   a record it found. `lifecycle.py` is on the 6a/6b branch, not yet on `main`, so this
   follows once that branch is merged. Until then a record is named by the user in the
   goal (by number or id), which the provenance check accepts.

## The guard pipeline

1. **Find** the record: list the entity and keep rows whose `number` or `id` equals
   the reference. None is `record_not_found`; more than one is `ambiguous_reference`.
2. **Re-read** with `.get` (the rows are shared and may have changed since the list).
3. **Already there:** if the status equals the target, `already_in_target_state`.
4. **Status:** if it is not one of the action's `from` statuses,
   `not_in_required_state`.
5. **Platform check:** for a status move, the record's `_transitions` must list
   `from` the current status `to` the target, otherwise `transition_not_available`.
6. **Action guard:** `precondition_failed` with the reason (for example "revised_ctc
   must be positive"). Payslip actions also need calculated rows, otherwise
   `run_not_calculated`.
7. **Call** the tool once with the record id. A tool error is returned as-is.
8. **Re-read and verify:** for a status move the new status must equal the target,
   otherwise `status_unchanged` with before and after.
9. **Result:** `{performed: true, action, entity, number, id, before, after}`; for
   the payslip actions also `result`, a digest of the tool payload (scalars kept,
   lists reduced to counts).

A refusal is `{"error": true, "tool", "code", "message", "record": {id, number,
status}}`, the same shape as the `run_payroll` guard, so the answer step reports
"not performed" and why.

## Error handling

- A short fetch of the entity list returns `scan_incomplete` named for the
  capability. A tool error on the list, the reads or the action is returned
  unchanged.
- No guard ever calls the action tool; the tool is called exactly once, and only
  after every check passes.

## Testing and grading

Graded tests are hand-written by the team; a test written by Claude scores zero.

**I build (mechanism, not graded):** the pure module, the worker, the capabilities;
scaffold unit tests with a fake client that models records, legal transitions and
the effect of each tool (including a tool that "succeeds" without changing status);
scaffold eval tasks (`authored_by: "scaffold"`) that run **refusals** against the
real tenant, aimed at records whose real state the platform would also reject:
calculate the pending August run, cancel the paid July run, submit the cancelled
loan LOAN-2026-00081, apply the draft revision REV-2026-00101, submit the approved
settlement FFS-2026-00001. The watched tenant state must be unchanged. A live check
with the real models runs the same refusal requests.

**The team writes (graded)**, in `evals/tasks/*.jsonl` with `authored_by: "team"`.
Natural openings: each refusal code, `no_mutation` after a refusal, the tool never
called, and `answer_grounded`.

## Rules added after the independent review

- **After the tool is called, never say "nothing was changed".** If the call raises, or the
  re-read fails, the result is `outcome_unverified` with `may_have_changed: true`; a status
  that moved to something other than the target is `unexpected_status` (also may have
  changed). `status_unchanged` (the tool returned but the status did not move) is the only
  post-call result with `may_have_changed: false`. The capability description and the answer
  step both say to report a possible change plainly, tell the user to check the record, and
  never retry. The planner treats a `may_have_changed` result as a real change, so
  `decline_request` cannot follow it.
- **Payslip actions are verified too:** a plain-text or non-record reply, `success`/`ok`
  false, or a run status that moved is `outcome_unverified` / `unexpected_status`, never
  `performed`. The calculated-rows check keeps only rows whose `payrun_id` is the run's.
- **One lock per record** serialises guarded actions on the same `(jurisdiction, entity, id)`,
  so two plan nodes (say submit and cancel) cannot both pass their re-read. The window
  between the platform's own check and the call is the platform's to close.
- **Eval tasks may declare `preconditions`** (an `agentswitch_state` check each); the runner
  re-reads those records first and does not run the task if any is not as the script
  assumes. The two armed scaffold tasks use it, because other teams can change shared rows.

## Limitations and open items

- The allowed paths (a real calculate, submit, apply, cancel) are not executed
  against shared data by this work; they are verified with a fake client only.
- `generate_payslips` and `send_payslips` have no status change to verify, so the
  result is the tool's own digest.
- A record is found by listing the whole entity; fine at current sizes (about 100
  rows per entity) and capped by the paging limit.
- There is no undo. `apply_salary_revision` and the cancels are permanent.

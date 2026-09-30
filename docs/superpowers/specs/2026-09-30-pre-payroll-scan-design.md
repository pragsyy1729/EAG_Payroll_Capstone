# Pre-Payroll Risk Scan: Design

Date: 2026-09-30. Status: draft for review. Sub-project 2 of 3 from
`2026-09-29-eval-harness-core-design.md` (check workflows), first tranche.
Family C in `harness/WORKFLOWS.md`.

## Purpose

Answer "check this payroll run before we submit it" with findings that each
trace to real AgentSwitch rows. The scan is read-only and computed in Python.
The LLM only plans the one call and explains the result; it never reads the
rows or does the arithmetic.

Success: asking the agent to check a calculated run returns findings whose
employee ids and numbers all come from fetched rows. "Nothing found" is a
valid, checkable answer. Asking it to check an uncalculated run returns a
plain "not calculated, nothing to check yet" and changes nothing.

## Decisions already made

- **Approach:** one sweep capability with pure check functions. Rejected:
  one capability per check (4 to 6 planner calls per scan, too exposed to the
  Gemini quota and to wrong-id planning) and generic reads with the LLM
  computing (the model would read 50 to 65 rows and do arithmetic; risks
  invented numbers).
- **Checks in v1 (option A, revised after probing the real data):**
  payee status, month-over-month net-pay change, net-pay sanity, duplicate
  payees. "Duplicate bank accounts" was replaced by "duplicate payees"
  because bank and PAN fields are redacted for this seat (see Limitations).
- **Uncalculated run:** report it and stop. The scan never calls
  `PayRun.calculate_payroll`. Calculating is a separate guarded action.
- **Comparison run:** the most recent earlier `regular` run that has
  calculated rows, chosen by period date, not label.
- **Graded tests are written by the team** (see Testing and grading).

## Non-goals

- Missing PAN/UAN/ESI numbers, joiner and leaver proration, pending
  leave/attendance items (options B and C). Each can be added later as one
  function in `scan_checks.py`.
- Calculating, fixing or submitting the run.
- Bank-account checks (blocked by redaction).
- Severity scoring beyond the fixed tiers below.

## Components

```
planner picks:  pre_payroll_scan(jurisdiction, payrun_id, compare_to?, change_threshold_pct?)
                          |
workers.py  run_pre_payroll_scan        I/O only, no writes
   1. PayRun.get(payrun_id)        not calculated? return the "nothing to check" result
   2. PayRunEmployee.list(payrun)  current rows, paged
   3. choose comparison run        PayRunEmployee.list(compare run), paged
   4. Employee.list                status, exit_date, paged
                          |
scan_checks.py  pure functions, no network
   payee_status, net_pay_change, net_pay_sanity, duplicate_payees
                          |
result -> answer_with_evidence -> plain-language summary
```

- **`payroll_agent/scan_checks.py`**: each check takes plain lists of rows
  (plus the period dates and threshold) and returns findings. No client, no
  async. This is the unit that is cheap to test.
- **`payroll_agent/workers.py`**: `run_pre_payroll_scan` does the fetching
  and paging, calls the checks, assembles the result. It reuses the existing
  `_call_tool` (tool errors become ordinary results) and the month/period
  date helpers already used by the `run_payroll` guard.
- **`payroll_agent/capabilities.py`**: one new read-only capability with
  `families=("evidence",)`. `payrun_id` and `compare_to` carry `format="id"`,
  so the existing provenance check rejects invented ids. No `side_effect`.
- **`evals/verifiers.py`**: a new `node_result` verifier (see Testing).

## Arguments

| Argument | Type | Notes |
|---|---|---|
| `jurisdiction` | `IN`/`US` | required, like every AgentSwitch capability |
| `payrun_id` | id | the run to check; must come from earlier evidence |
| `compare_to` | id, optional | override the automatic comparison run |
| `change_threshold_pct` | integer 1-100, optional, default 30 | medium-severity cutoff for `net_pay_change`; the high tier stays at 50 |

## Checks

| Check | Flags | Severity |
|---|---|---|
| `payee_status` | employee paid but status `left` or `suspended`; or `exit_date` on or before the period end | high |
| | employee paid but status `on_leave` | info (may be legitimate) |
| | payee's `employee_id` not found in `Employee.list` | high |
| `net_pay_change` | net pay changed by at least 50% vs the comparison run | high |
| | changed by at least `change_threshold_pct` (default 30) | medium |
| `net_pay_sanity` | net pay at or below zero, or missing | high |
| `duplicate_payees` | the same `employee_id` more than once in the run | high |

If `change_threshold_pct` is 50 or more, the medium band is empty and only the
high tier (at least 50%) is reported.

Employees present in the run but absent from the comparison run are not
findings; they are reported as `counts.new_in_run`.

Calibration on real data (August vs July, 62 employees in both): the change
threshold flags 43 at 10%, 21 at 20%, 15 at 30%, 3 at 50%. A 20% rule would
flag a third of the workforce, hence the 30/50 defaults. August is also the
run that an earlier unguarded `run_payroll` rewrote, so part of that churn is
an artifact; a clean pair (June to July) should be used alongside it.

## Result shape

```
{ "payrun_id", "run_status", "calculated": true|false,
  "compared_to": "<payrun id>" | null,
  "skipped": [{"check": "net_pay_change", "reason": "no earlier calculated regular run"}],
  "counts": {"by_check": {...}, "by_severity": {...}, "new_in_run": 3, "rows_checked": 65},
  "findings": [ {id, check, severity, employee_id, employee_name, payrun_id,
                 evidence: {...values straight from fetched rows...}, message} ],
  "truncated": false }
```

- Findings sort by severity, then employee name, and are capped at 40;
  `counts` always reflects the full set and `truncated` says when the cap hit.
- Not calculated (every row has net pay at or below zero, or there are no
  rows): `calculated: false`, no findings, `skipped` lists all four checks
  with reason "run not calculated". The September draft `PRUN-2026-00013`
  (51 rows, all net pay at or below zero) is the live example.
- Evidence values are copied from fetched rows, never computed into new
  identifiers, so the `answer_grounded` verifier can check the final answer.

## Error handling

- A tool error on any fetch returns the ordinary `{"error": true, ...}`
  result (existing `_call_tool` behaviour); the answer step reports it.
- A list that reports more rows than were fetched after paging marks the
  scan `incomplete` and the affected check is listed under `skipped`, rather
  than silently checking a partial set.
- The scan never writes. It makes only `.get` and `.list` calls, which the
  eval recorder classifies as non-mutating.

## Testing and grading

The graded tests are hand-written by the team; a test written by Claude
scores zero. So:

**I build (mechanism, not graded):**
- `scan_checks.py`, the capability and its worker.
- A `node_result` verifier in `evals/verifiers.py`: asserts on a value inside
  a named capability's result in the run record (for example
  `counts.by_severity.high`), so a task can check the scan's actual output
  and not only the final prose.
- Scaffold eval tasks, `authored_by: "scaffold"`, excluded from graded totals.
- Non-graded checks of my own work: ad-hoc runs against the real tenant and
  `tests/test_scan_checks_scaffold.py` for the pure functions, whose
  docstring states it is implementation scaffolding and not a graded
  submission.

**The team writes (graded), in `evals/tasks/*.jsonl` with
`authored_by: "team"`.** The design leaves these openings; the goals,
verifiers and expected values are theirs:
- a paid employee with status `left` is flagged, verified against
  AgentSwitch with `agentswitch_state`;
- "check the September run" states it is not calculated, never calls
  `PayRun.calculate_payroll`, and leaves watched state unchanged (a
  refusal-shaped task for the graded refusal requirement);
- every id and number in the answer appears in tool results
  (`answer_grounded`);
- a lower `change_threshold_pct` yields more findings;
- the run leaves watched PayRun state identical (`no_mutation`).

## Limitations and open items

- **Redaction:** `bank_account_number`, `ifsc_code`, `pan` and `aadhaar` are
  in `_redacted_fields` for this seat; 0 of 100 employees expose a value.
  Bank-duplicate and missing-PAN checks are impossible here.
- **Data quality:** 41 employees have an `exit_date` but only 19 have status
  `left`. The `payee_status` check reports both conditions separately so the
  mismatch is visible.
- **Live LLM runs** depend on Gemini recovering (upstream 503s on
  2026-09-30). The scripted transport exercises the full path meanwhile.
- **Unconfirmed:** whether `PayRunEmployee.employee_id` always equals
  `Employee.id`. It held for all 65 August and 62 July rows in the probe
  (every payee was found in `Employee.list`); the scan should still report
  a payee missing from `Employee.list` as a finding, not crash.
- **Employee list size:** `Employee.list` returned all 100 in one page here.
  The worker pages anyway and treats a short fetch as `incomplete`.

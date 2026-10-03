# Payroll Cost and Variance Report: Design

Date: 2026-10-03. Status: draft for review. Second of three sub-projects in the
current batch (refusals done, then this, then extra workflows). Family D in
`harness/WORKFLOWS.md`.

## Purpose

Answer "what did payroll cost for run X, by department or location" and "why did
cost change between two runs", with every number computed in Python from real
AgentSwitch rows. The LLM only plans the one call and explains the result.
Read-only; it never calculates or changes a run.

Success: the report's totals equal the PayRun header totals (a built-in check),
each group row traces to payslips, and asking about a run that has not been
calculated returns a plain "not calculated" and nothing else.

## Decisions already made

- Scope **B**: cost breakdown plus month-over-month variance (user choice).
  Rejected: A alone (no variance) and C (allowance-level detail, deferred).
- **One capability** with `group_by`, `with_variance` and an optional
  `compare_to`, not separate cost and variance capabilities.
- Computed in Python, with pure functions in their own module; same pattern as
  `pre_payroll_scan`.
- Graded tasks are written by the team, not by Claude.

## Non-goals

- Allowance-level (HRA, special allowance) detail.
- Cost centres from `PayrollReportingTag`, budgets, forecasts, currency
  conversion, or charts.
- Calculating a run, or any write.

## What the data provides (probed 2026-10-03, India tenant)

- **PayRun header:** `total_gross_pay`, `total_net_pay`, `total_deductions`,
  `total_employer_contribution`, `employee_count`, `pay_period_start/end`,
  `status`. For July (`PRUN-2026-00011`): 62 employees, gross 2,417,316,
  net 2,259,843, and both equal the sum over the slips.
- **PayRunEmployee slip:** `employee_id`, `employee_name`, `gross_pay`,
  `net_pay`, `total_deductions`, `employer_contribution`, an `earnings` list of
  `{component_name, amount}`, and `overtime_pay` / `overtime_hours` fields. No
  department or location. **Correction found while building:** on the India
  tenant `overtime_pay` and `overtime_hours` are 0 for every slip, while overtime
  is paid as an "Overtime" component in `earnings` (June 54,512, July 52,049).
  Overtime is therefore the sum of earnings components whose name contains
  "overtime" (case-insensitive), falling back to `overtime_pay` only when a slip
  has none.
- **Employee:** `department_id`, `work_location_id`, and display names
  `_department_id_display`, `_work_location_id_display`. All 100 employees have
  both. There are 12 departments and 6 work locations; July's 62 payees fall in
  8 departments.
- Total cost to company = `gross_pay + employer_contribution`.

## Components

1. **`payroll_agent/cost_report.py`**: pure functions, no network, no async.
   Takes plain lists of slip and employee rows.
   - `summarize(rows, employees, group_by) -> {totals, groups}`
   - `compare(rows, prev_rows, employees, group_by) -> variance`
   - `check_header(run, rows) -> consistency`
   - `build_report(...)` assembles the result below.
2. **Existing helpers reused** from `payroll_agent/workers.py`:
   `_fetch_all` (paged reads, tool errors kept, `scan_incomplete` on short
   fetch) and `_comparison_rows` (most recent earlier calculated regular run).
   `scan_checks.is_calculated` decides "calculated".
3. **`run_payroll_cost_report` worker** (`workers.py`): fetches `PayRun.get`,
   the run's `PayRunEmployee` rows, `Employee.list` (only when `group_by` is not
   `none`), and, when variance is requested, the comparison run's rows. Then
   calls `build_report`. Only `.get` and `.list` tools.
4. **`payroll_cost_report` capability** (`capabilities.py`), read-only,
   `families=("evidence",)`. Arguments:

   | Argument | Type | Notes |
   |---|---|---|
   | `jurisdiction` | `IN`/`US` | required |
   | `payrun_id` | id | the run to report; provenance-checked |
   | `group_by` | enum `department`/`location`/`none` | default `department` |
   | `with_variance` | boolean | default false |
   | `compare_to` | id, optional | implies variance; overrides the automatic baseline |

## Result shape

```
{ "payrun_id", "run_status", "calculated": true|false,
  "period": {"start", "end"}, "group_by",
  "totals": {"headcount", "gross_pay", "net_pay", "total_deductions",
             "employer_contribution", "total_cost", "overtime_pay"},
  "groups": [ {"group", "headcount", "gross_pay", "net_pay",
               "employer_contribution", "total_cost", "overtime_pay",
               "share_of_cost_pct"} ],            # by total_cost desc, at most 30
  "groups_truncated": false,
  "consistency": {"header_matches_slips": true,
                  "differences": {"total_gross_pay": {"header": 1, "slips": 2}}},
  "variance": null | { "compared_to": "<payrun id>",
      "totals": {"gross_pay": {"previous", "current", "change", "change_pct"}, "...": {}},
      "groups": [ {"group", "previous_cost", "current_cost", "change", "change_pct"} ],
      "joiners": {"count", "employees": [{"employee_id", "employee_name"}]},
      "leavers": {"count", "employees": [...]},
      "overtime": {"previous", "current", "change"} },
  "skipped": [ {"what": "variance", "reason": "no earlier calculated regular run"} ] }
```

- Not calculated (no rows, or every net pay at or below zero): `calculated: false`
  with the period and status only; no totals, groups, or variance.
- A group is the department (or location) display name; an employee missing from
  `Employee.list` falls into the group `(unknown)`. `group_by: none` returns no
  `groups`.
- Variance groups are the union of both runs; a group absent from one run counts
  as zero there. Sorted by absolute change, at most 30. Joiner and leaver lists
  show at most 25 each; `count` is always the full number.
- `change_pct` is `null` when the previous value is zero or missing.
- `consistency` compares the header's `total_gross_pay`, `total_net_pay`,
  `total_deductions`, `total_employer_contribution` and `employee_count`
  against the slip sums, within 1.0 for money; only mismatches are listed.

## Error handling

- A tool error on any fetch returns the ordinary `{"error": true, ...}` result.
- A short fetch of the run's own rows (or of `Employee.list` when needed)
  returns `{"error": true, "code": "scan_incomplete"}`. A short comparison run
  skips variance with the reason; the cost breakdown is still returned.
- Duplicate `employee_id` within one run: every row is counted in the totals and in
  `headcount` (as the PayRun header counts rows), so the header check still holds;
  joiner and leaver detection uses the set of distinct ids.
- Amounts may arrive as numbers or numeric strings; unusable values count as 0
  and are listed under `skipped` with the count.
- The report never writes. Only `.get` and `.list` calls are made.

## Testing and grading

Graded tests are hand-written by the team; a test written by Claude scores zero.

**I build (mechanism, not graded):**
- `cost_report.py`, the capability and the worker.
- Scaffold unit tests with a fake AgentSwitch client (labelled implementation
  scaffolding): grouping, totals, the header check, variance and joiners and
  leavers, the not-calculated and skipped paths, paging, string amounts.
- A scaffold eval task (`authored_by: "scaffold"`) run against the real tenant
  with a scripted model, using `node_result` verifiers.
- A live check with the real models: "What was payroll cost by department in
  July 2026?" and "Why did payroll cost change from June to July?", with the
  numbers checked against the raw rows.

**The team writes (graded)**, in `evals/tasks/*.jsonl` with `authored_by:
"team"`. Natural openings: the report's totals match the header, the top
department by cost, joiners and leavers between two runs, the answer's numbers
all appear in tool results (`answer_grounded`), and the run leaves state
unchanged (`no_mutation`).

## Limitations and open items

- **Department and location are the employee's current ones**, not as of the
  run's date. A transfer after the run shifts past cost between groups. This is
  the same limit as the scan's status check, and is stated, not worked around.
- US tenant is not verified (field names such as `suta_employer` exist on the
  slip but are not used in v1; employer cost uses `employer_contribution`).
- Live results depend on the provider setup in `NEXT_STEPS.md`; scripted runs do
  not.
- A very large tenant (over 100 departments or locations) is capped at 30 groups
  with `groups_truncated` set.

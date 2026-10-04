# Statutory Dues and Lifecycle Reports: Design

Date: 2026-10-04. Status: draft for review. Sub-projects 6a and 6b together
(read-only). Families E and F (read side) in `harness/WORKFLOWS.md`. Guarded
actions (6c) are a separate, later sub-project.

## Purpose

- **6a** answers "what statutory dues does this payroll run create, and by when"
  for India and, where the data allows, the US.
- **6b** answers lifecycle questions about loans, salary revisions, final
  settlements and investment declarations, and surfaces data-quality problems.

Everything is computed in Python from real AgentSwitch rows; the LLM only plans
one call and explains the result. Both are read-only: only `.get` and `.list`.

## Decisions already made

- **Two capabilities, not five**, so the planner's menu stays short (smaller
  models choose worse from a long menu): `statutory_dues` and `lifecycle_report`
  (with a `topic` argument).
- **Both jurisdictions for 6a, honestly.** India fully; the US employee-side
  withholdings only. Employer-side US taxes are reported as unavailable, not
  guessed.
- **Due dates (option A):** the standard India calendar as labelled constants,
  India only, never presented as an AgentSwitch value.
- Graded tasks are written by the team, not by Claude.

## Non-goals

- Guarded actions (calculate, payslips, submit for approval): sub-project 6c.
- Computing taxes AgentSwitch did not compute, or inventing US rates and wage
  bases (`USPayrollConfig` has no rows on the US tenant).
- Tracking whether dues were deposited (AgentSwitch has no deposit records).
- Form 16 / Form 24Q generation (REST-only for this seat), TDS slab maths, PT
  slab recomputation.

## What the data provides (probed 2026-10-04)

- **India July run (`PRUN-2026-00011`):** header totals `total_epf_employee`,
  `total_epf_employer`, `total_esi_employee`, `total_esi_employer`, `total_pt`,
  `total_tds` each equal the sum of the slip fields `epf_employee`,
  `epf_employer`, `esi_employee`, `esi_employer`, `professional_tax`, `tds`.
  `total_employer_contribution` equals employer EPF plus employer ESI. Other
  employer-side slip fields exist (`eps_employer`, `edli_employer`,
  `epf_admin_charges`, `lwf_employee`, `lwf_employer`) and are 0 in July.
- **India configs (one row each):** `EPFConfig` (12%/12%, EPS 8.33%, wage
  ceilings 15,000), `ESIConfig` (0.75%/3.25%, wage ceiling 21,000), `PTConfig`
  (monthly cycle, annual cap 2,500, slabs), `LWFConfig` (half-yearly, Maharashtra).
- **US August run (`PRUN-2026-00002`, 45 employees):** the named tax fields are
  empty, but each slip's `deductions` list holds the withholdings: Federal
  Withholding 29,650, Ohio State Withholding 6,795, Social Security 15,144,
  Medicare 3,583. Their sum equals the header `total_deductions` (55,171.56 to
  within rounding). Social Security is 6.1% and Medicare 1.45% of gross.
  Employer-side Social Security and Medicare, FUTA and SUTA are not present.
- **Lifecycle entities (India):** `EmployeeLoan` 102 (draft 87, partially
  repaid 8, cancelled 5), `LoanRepayment` 194 (scheduled 97, deducted 3),
  `SalaryRevision` 101 (draft 21, pending approval 73, approved 4, rejected 1,
  applied 1), `FinalSettlement` 3 (calculated, pending approval, approved),
  `InvestmentDeclaration` 14 (approved 7, submitted 5, draft 1, rejected 1),
  `ProofOfInvestment` 100.
- **The loan data is noisy:** negative EMI, negative interest rate, tenure of
  -3 months, a ₹0.14 loan amount, a disbursement date in year 0009, negative
  repayment principal. The report must tolerate and flag this.

## Components

1. **`payroll_agent/statutory.py`**, pure: `due_dates`, `build_statutory`, and
   helpers for India rows, US rows, the header check, the US rate sanity check,
   the ESI flag and the config echo.
2. **`payroll_agent/lifecycle.py`**, pure: one function per topic (loans,
   revisions, settlements, investments) and `build_lifecycle`.
3. **Workers** in `workers.py`: `run_statutory_dues` and `run_lifecycle_report`,
   reusing `_fetch_all`; they only read.
4. **Capabilities** in `capabilities.py`, read-only, `families=("evidence",)`:

   | Capability | Arguments |
   |---|---|
   | `statutory_dues` | `jurisdiction`, `payrun_id` (id) |
   | `lifecycle_report` | `jurisdiction`, `topic` (`loans`/`revisions`/`settlements`/`investments`), `employee_id` (optional id) |

## 6a result shape

```
{ "payrun_id", "run_status", "calculated", "jurisdiction", "period": {"start","end"},
  "statutes": [ {"statute", "side": "employee"|"employer", "amount", "source",
                 "available": true|false, "header_total", "due", "note"} ],
  "consistency": {"matches": true|false|null, "compared": n, "differences": {}},
  "rate_checks": [ {"statute", "expected_pct", "observed_pct", "within_tolerance", "basis"} ],  # US
  "flags": {"esi_covered_above_ceiling": {"count", "employees": [...]}},                          # India
  "config": { ...rates, ceilings, cycles echoed from the config rows... },                        # India
  "deposit_tracking": "not tracked in AgentSwitch; whether these dues were paid is unknown",
  "skipped": [ {"what", "reason"} ] }
```

- **India rows** (amount = slip sum, cross-checked with the header total):
  EPF employee, EPF employer, EPS, EDLI, EPF admin charges, ESI employee, ESI
  employer, Professional tax, TDS, LWF employee, LWF employer. A row is listed
  when its slip sum or header total is non-zero, so July lists EPF, ESI, PT and
  TDS.
- **US rows:** federal income tax, state income tax, Social Security and Medicare
  (employee), summed from slip `deductions` by component name (contains
  "federal"; contains "state" and "withholding"; "social security"; "medicare").
  Other components are reported as non-statutory deductions in the check, not as
  dues. Four employer rows (Social Security match, Medicare match, FUTA, SUTA)
  appear with `available: false` and the note "not in AgentSwitch data for this
  seat". The consistency check compares the sum of all deduction components with
  the header `total_deductions` (within 1.0).
- **Rate checks (US):** Social Security and Medicare as a percentage of total
  gross, against 6.2% and 1.45% within 0.5 points. Labelled "public statutory
  rate, not from AgentSwitch"; informational, since pre-tax deductions and the
  wage base make the observed rate approximate.
- **Due dates (India, option A), computed from the pay period end:**
  - TDS: the 7th of the following month; for a March period, 30 April.
  - EPF (all EPF rows) and ESI: the 15th of the following month.
  - Professional tax and LWF: no date (state schedules vary); the config's cycle
    is echoed instead.
  - Each due entry is `{"date", "rule", "basis": "standard India calendar, not
    from AgentSwitch", "status": "overdue" | "due_today" | "upcoming",
    "days_left"}`, with status measured against today.
  - The US gets no dates.
- **ESI flag:** employees with `esi_covered` true and gross above the config's
  `wage_ceiling`; severity info; count is full, list capped at 25. Employees not
  covered are not flagged (coverage rules are more nuanced than the ceiling).
- Not calculated (no rows or no net pay above 0): `calculated: false`, the
  period and status only, `skipped` = `[{"what": "report", "reason": "run not
  calculated"}]`.
- Amounts may be numbers or numeric strings; a present-but-unusable amount
  (including `nan` and `inf`) counts as 0 and is reported once under `skipped` as
  `{"what": "amounts", "reason": "<n> unusable amount(s) counted as 0"}`.

## 6b result shape

```
{ "topic", "total", "by_status": {...}, ... topic-specific ..., "items": [...], "skipped": [...] }
```

- **loans:** counts by status; totals over loans with a positive amount
  (`loan_amount`, `total_repaid`, `loans_counted`, `excluded_invalid`);
  repayments by status with totals; scheduled repayments grouped by month (at
  most 12), and scheduled repayments dated before today (count and total);
  `data_quality`: for each check with a non-zero count, `{check, count,
  examples (at most 10)}`, plus `records_with_issues` (distinct loans). Checks:
  `loan_amount_not_positive`, `emi_not_positive`, `tenure_not_positive`,
  `negative_interest_rate`, `implausible_disbursement_date` (year before 2000 or
  after 2100), `repayment_principal_negative`, `repayment_total_not_positive`.
  With `employee_id`, `items` lists that employee's loans (at most 25).
- **revisions:** counts by status; `pending` (status `pending_approval`) listed
  oldest effective date first (at most 25) with the full `pending_count`;
  `backdated_pending` (pending with an effective date before today: arrears
  likely once approved) with count and at most 10 examples; `upcoming_30_days`
  count (pending or approved, effective within the next 30 days);
  `applied_count`; and `status_conflicts` (pending revisions whose `approval_status`
  is approved or rejected, a contradiction seen on the real tenant: 14 of 73), with
  count and at most 10 examples.
- **settlements:** each settlement (at most 25): status, `approval_status`,
  employee, last working date and the components (`bonus_payable`,
  `gratuity_payable`, `leave_encashment`, `epf_settlement`,
  `notice_pay_recovery`, `gross_settlement`, `net_settlement`); counts by
  status; total net over valid amounts.
- **investments:** declarations by status and by fiscal year; declared totals
  per section over approved declarations; declarations with no matching proof
  (by `declaration_id`) excluding draft and rejected ones (at most 25 listed,
  full count); proofs by `approval_status`.
- Employee names come from `Employee.list`; an employee not found shows the id
  only. Lists are capped; counts are always the full numbers. Amounts may be
  numbers or numeric strings; unusable ones count as 0 and are listed under
  `skipped` with the count.

## Error handling

- A tool error on any fetch returns the tool's own error result. A short fetch
  returns `{"error": true, "code": "scan_incomplete"}` named for the capability.
- A missing India config row skips only the config echo and the ESI flag, with a
  reason in `skipped`; the dues are still reported.
- No path calls a mutating tool.

## Testing and grading

Graded tests are hand-written by the team; a test written by Claude scores zero.

**I build (mechanism, not graded):** the two pure modules, the workers and
capabilities; scaffold unit tests (labelled implementation scaffolding) with a
fake AgentSwitch client; scaffold eval tasks (`authored_by: "scaffold"`) run on
the real tenants with scripted models: India July dues, US August dues, the
uncalculated September run, and the loans data-quality report. Plus a live check
with the real models, with each number verified against the raw rows.

**The team writes (graded)**, in `evals/tasks/*.jsonl` with `authored_by:
"team"`. Natural openings: the dues totals equal the header, the TDS due date,
the US employer rows being unavailable, a loan with a non-positive amount being
flagged, pending revisions being backdated, and `answer_grounded`.

## Limitations and open items

- US employer-side taxes, FUTA and SUTA are not in the data; no US due dates.
- India PT and LWF due dates are not asserted; deposits are not tracked.
- The India calendar constants are standard dates; a state or a notified change
  can differ.
- Component-name matching for the US withholdings relies on the names seen on
  this tenant ("Federal Withholding", "<State> State Withholding", "Social
  Security", "Medicare"); unmatched components are counted as non-statutory.
- The loan data looks like junk, so many flags will fire; whether that is a
  platform bug or class test data is for the team to judge.
- The ESI flag is deliberately narrow and informational.

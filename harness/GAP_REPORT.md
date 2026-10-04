# Payroll Gap Report

Written by: Hari Prasath M and Pragathi K V

Week-one deliverable per the AgentSwitch integration guide's Step 3: one
page, three questions, answered with specifics. Reproduced below verbatim,
followed by a second pass applying Step 4's own framework (platform gap vs.
orchestration opportunity vs. "what can an agent do that the reference
product's UI cannot") to each item, cross-checked against `schemas.json`
and `openapi.json`.

## 1. What do they do that we do not? (original submission)

Features to incorporate in Suryodhaya/Keystone:

1. **Automated Compliance Updates** — Automatically monitors and updates
   tax brackets, labor laws, and statutory rules in the system as
   regulations change. Eliminates manual rule updates and prevents tax
   calculation errors across regions. *Reference: Rippling, Gusto.*
2. **Automated Proof-of-Investment (PoI) Verification** — Document AI
   scans and verifies uploaded tax-saving documents (rent receipts,
   investment proofs), checking landlord PAN format, rental agreement
   dates, and metro-vs-non-metro HRA rules, flagging duplicate/invalid
   uploads with clear rejection reasons. *Reference: Keka HR, greytHR.*
3. **Nightly Payroll Simulation & Risk Alerts** — A background payroll
   calculation runs every night using current attendance, leave, and
   contract data, alerting admins to problems days before payout: bank
   account changes within 72 hours of disbursement, negative pay from
   unapproved unpaid leave, missing statutory IDs (UAN, tax numbers).
   *Reference: Rippling.*
4. **Cross-Entity Cost Allocation** — Splits an employee's salary, tax,
   and statutory contributions across multiple projects, departments, or
   legal entities based on logged hours, keeping primary tax reporting
   under the main employer entity while auto-generating matching
   debit/credit journal entries for accounting. *Reference: Rippling, Deel.*
5. **Compensation Scenario Simulator** — Managers test "what-if" changes
   (tax regime shifts, variable pay adjustments) to instantly see total
   cash outflow, tax liability, and statutory cost impact without
   touching real payroll data. *Reference: Rippling, Dayforce.*
6. **Fraud Prevention & Field Lockdowns** — Two approvals required to
   change sensitive fields (salary rate, direct deposit details);
   suspicious changes (duplicate bank account numbers across employees,
   unexpected IP locations) are auto-flagged and affected payouts frozen
   before funds are sent. *Reference: Rippling, Gusto.*

## 2. Which gaps can an agent close with the tools our seat already has? (original submission)

> None of the available tools will be able to close the listed gaps above.
> New endpoints will be required to do some and new features are needed
> to do some.

## 3. What can an agent do better than the reference products? (original submission)

1. **Execution Horizon** — multi-step goal preservation across hours.
2. **Handling Failures** — diagnoses root cause, re-evaluates system
   state, retries, or reroutes tasks autonomously.
3. **API Restrictions** — restriction to native APIs and direct
   integrations is not an issue for an agent the way it is for a human
   clicking through a UI.

---

## Second pass: platform gap vs. orchestration opportunity

> **Superseded where it disagrees with the third pass below.** This second pass was
> a hypothesis; the third pass checks it against the live tool catalogue and against
> what has since been built and run on live data.

The guide's worked Ledger/Rillet example splits each gap into "belongs to
the platform team" vs. "orchestration over the current API, and it is
yours." Applying that split below, against actual schema primitives
already present in `schemas.json`/`openapi.json` — **not yet verified
against a live `tools/list` call**, so treat this as a hypothesis to check
against the real scoped catalogue before finalizing.

| # | Feature | Verdict | Why |
|---|---|---|---|
| 1 | Automated Compliance Updates | **Mostly platform gap, partial orchestration** | Legally updating `EPFConfig`/`ESIConfig`/`PTConfig`/`TaxConfig`/`USPayrollConfig` from an authoritative external source is a new capability class (no ingestion path exists). But an agent can already: watch config `effective_from`/`effective_to` staleness, `web_search` government sources for rate changes (S17Code's `web_search`/`fetch_url` capabilities already exist), diff against current config, and raise an `ApprovalRequest` proposing an update — orchestration over existing entities, with a human approving the actual write. |
| 2 | Automated PoI Verification | **Orchestration opportunity, pending one check** | `ReimbursementClaim.receipt:file` (and presumably `ProofOfInvestment`'s equivalent) already model an uploaded document. If `glc_v5`'s configured provider supports vision/document input, the agent can fetch the file, extract PAN/dates/HRA-rule fields via an LLM call through the existing gateway seam, and either auto-approve, reject with a reason, or flag for human review by writing to `ProofOfInvestment.approval_status`. Needs confirming glc_v5 has a vision-capable provider configured before treating this as buildable. |
| 3 | Nightly Payroll Simulation & Risk Alerts | **Orchestration opportunity — strong candidate** | No platform gap at all: this is a scheduled `PayRun`/`calculate` dry-run plus threshold checks over data that already exists (`PayrollBankAccount` change timestamps, `Attendance.is_lop`, statutory ID presence on `Employee`/config entities). Our harness already has the exact primitive for this — `payroll_agent/events/` (`AutonomousEventEngine`, `ScheduleLease`) — built for unattended, leased, non-overlapping periodic runs. This is the single best "agent does what the UI cannot" demonstration: a human has to click into every employee to notice a risk; the agent can walk all of them every night and only surface the ones that matter. |
| 4 | Cross-Entity Cost Allocation | **Platform gap** | Neither `PayRun`/`PayRunEmployee`/`Employee` show multi-entity split fields or a timesheet-to-project-hours entity in the extracted schema. Generating matching debit/credit journal entries also reaches into the Ledger Agent's domain. Real orchestration here needs new fields/entities first — flag as a platform-team ask, revisit once (if) they exist. |
| 5 | Compensation Scenario Simulator | **Orchestration opportunity** | `PayRun.status` already has a genuine `draft` state distinct from `approved`/`paid`. An agent can create a draft `PayRun` (or use the existing `/api/payroll/calculate` dry-run path), run the statutory engine against hypothetical inputs, read the totals, and discard/cancel it — never touching a real, submitted run. No new platform capability required, only orchestration discipline (never submit/approve the scratch run). |
| 6 | Fraud Prevention & Field Lockdowns | **Orchestration opportunity — strong candidate** | The generic `approvals` domain already has `ApprovalPolicy.routing_type`/`quorum_type`/`quorum_count` and `ApprovalGroup` — two-approval-before-sensitive-change is exactly what this was built for; we would attach a payroll `ApprovalPolicy` requiring quorum 2 on bank-detail/salary-rate changes rather than invent new approval machinery. Duplicate-bank-account and negative-pay-before-freeze detection reuse `PayrollRunControl.funding_state`/`PayrollBankAccount` queries via `/api/{entity}/aggregate`. Unexpected-IP-location detection would need `AuditEvent` to actually carry IP/geo data — unconfirmed, check before committing to that specific check. |

**Revised answer to question 2:** at least three of the six (3, 5, 6) look
buildable as pure orchestration over what AgentSwitch already exposes, with
no platform change needed — a stronger and more specific answer than "none
of the available tools will close these gaps." Worth revising before this
report is treated as final, and worth confirming against the live scoped
`tools/list` result rather than the static schema export.

## Third pass: checked against the live tool catalogue (2026-10-04)

> Drafted by Claude from live checks of the India and US tenants; for the team to
> review, edit and own before this report is treated as final.

The second pass was a hypothesis. This pass checked it against the live `tools/list`
(360 tools visible to our seat) and against what has been built and run on live data
since: `pre_payroll_scan`, `payroll_cost_report`, `statutory_dues`, `lifecycle_report`,
the guarded actions and `decline_request` (see `NEXT_STEPS.md`).

| # | Feature | Status | In one line |
|---|---|---|---|
| 3 | Nightly Payroll Simulation & Risk Alerts | **Core implemented; scheduling and alert delivery remaining** | The checks exist and run on live data; the nightly trigger and the place to send alerts are not wired up, and the ID/bank checks are impossible for our role. |
| 5 | Compensation Scenario Simulator | **Feasible as a labelled estimate; not yet built** | The platform cannot calculate hypotheticals, so we would read the real payslip and recompute the affected parts ourselves, read-only. |
| 6 | Fraud Prevention & Field Lockdowns | **Platform deficiency; the agent can only detect** | The enforcement tools are not on our tool list; the platform would need to provide the six things listed below. |

### Gap 3: Nightly Payroll Simulation & Risk Alerts

**Built and verified on live data.** `pre_payroll_scan` checks a calculated run for
employees paid after leaving or suspended, large net-pay changes against the previous
run, zero, negative or missing net pay, and duplicate payees. It refuses to scan a run
that has not been calculated. `payroll_cost_report` adds cost and variance, with a
check that the run header matches the payslips. Both pass scaffold tasks against the
real India tenant, and were also run directly against the US tenant. `Attendance.is_lop` and `lop_hours` are readable (not yet
used in a check), and company bank-account change timestamps are readable
(`PayrollBankAccount.updated_at`, two company accounts).

**Remaining.** (a) The nightly trigger: the repo's event engine (`payroll_agent/events/`)
can host it, and the platform has `AgentTask` scheduling. (b) An alert destination, for
example `AgentEscalation.create`; that is a write, so the team should decide it.

**Not possible for our role.** "Missing statutory IDs (UAN, tax numbers)" and
"duplicate bank account numbers" cannot be checked: PAN, Aadhaar, bank account number,
IFSC and bank name are in `_redacted_fields` for our role (0 of 100 employees expose a
value). "Bank account changes within 72 hours" is possible for the company's accounts
only, not employees'.

**Platform ask (small).** Expose, to payroll roles, a presence flag or a masked or
hashed fingerprint for PAN, UAN and bank accounts, and change timestamps for employee
bank details.

### Gap 5: Compensation Scenario Simulator

**Why the second pass was too optimistic.** `PayRun.calculate_payroll` computes only
from stored records; there is no hypothetical-input mode. Simulating by writing fake
revisions or runs into the shared tenant cannot be undone from our seat, so that route
is not recommended.

**Approach that works.** Read the employee's real latest payslip and salary structure,
apply the hypothetical change in code, recompute only the affected components from the
real configuration (EPF rates and ceilings, ESI rate and ceiling, professional-tax
slabs), and return the result labelled as an estimate. It only reads, so nothing is
written.

**Limits.** Income tax (TDS) depends on the year's projected income, the regime and
declared investments. `TaxConfig` holds no slabs, so the TDS effect can only be
estimated from the employee's current TDS and must be labelled as an estimate.

**How we would check accuracy.** Replay salary revisions that were already applied
(status `applied`) and compare the predicted net pay with the actual payslip after the
change.

**Platform ask (optional).** A dry-run flag on `calculate_payroll` that accepts
overrides and writes nothing.

### Gap 6: Fraud Prevention & Field Lockdowns

**The second pass's mechanism is not available to us.** `ApprovalPolicy`,
`ApprovalGroup`, `PayrollRunControl` (funding state or freeze) and `AuditEvent` are not
on our seat's tool list. Our seat can submit records for approval but has no approve
tool (the platform enforces that separation), and there is no field-level lock and no
payout freeze.

**What the agent can do: detect and report.** Examples found on live data: 14 of 73
pending salary revisions carry an `approval_status` of approved or rejected that
contradicts their `pending_approval` status; 2 of 102 loans were accepted with a
negative or zero EMI or tenure, a negative interest rate, and in one case a disbursement
date in year 0009. These may be class test data rather than platform faults.

**What the platform would have to provide to close this gap**

1. An approval policy that requires N approvers (a quorum) on changes to named
   sensitive fields (salary rate, bank details), enforced by the platform and not by the
   client.
2. Field-level locks or write permissions, so a role can be barred from editing those
   fields directly.
3. A payout hold (freeze) on a run or payment, settable by an approver or by a flag
   raised from a detection, effective before funds move.
4. An audit log readable by the payroll seat, with the actor, timestamp, before and
   after values, and the source IP or location.
5. A privacy-safe way to compare bank accounts across employees (masked or hashed
   fingerprints), so duplicates can be found without exposing account numbers.
6. Validation when records are written: positive amounts, EMI and tenure, plausible
   dates, and a status consistent with the approval status.

### Revised answer to question 2

**Partly.** Of the six gaps, with the tools our seat already has:

- **3** is implemented at its core and needs only a trigger and an alert destination;
  two of its checks are impossible for our role.
- **5** is feasible as a clearly labelled estimate, read-only.
- **6** is a platform deficiency; the agent can detect problems but not enforce
  approvals, locks or freezes. The platform asks are listed above.
- **1** (automated compliance updates) is partly orchestration: watch configuration
  `effective_from` and `effective_to` dates and propose changes for a human to approve.
  Not re-verified in this pass.
- **2** (proof-of-investment verification) depends on a vision-capable model provider,
  which has not been checked.
- **4** (cross-entity cost allocation) is a platform gap: no multi-entity split fields
  or timesheet-to-project hours.

## What we are actually building, and what's graded

Per the guide's "What we are grading" section:

1. **The gap report** (this file) — done in first draft, second pass above
   proposes a revision to question 2.
2. **The agent** — answering the seat's questions against live data other
   teams are changing underneath it (see `REQUIREMENTS.md` §14's
   concurrency rules — re-read before acting, never assume a fetched row
   is still current).
3. **The harness** — described as "the part most teams underbuild": our
   own loop, a task set with verifiers that read the database rather than
   the agent's prose, every run written to disk before anything is scored.
   This is a distinct artifact from the `harness/` directory name in this
   repo (that's just this package's inherited S17Code-lineage folder name)
   — it's an evaluation harness we still need to build. S17Code's own
   `proofs/harness.py` and `proofs/general_agent_live.py` (see
   `session_17/S17Code/proofs/`) are the reference pattern for this: a
   live HTTP proof against a running process, writing every node/edge/
   outcome to disk before any scoring happens. Worth reading in full
   before designing ours.
4. **At least one task whose correct answer is refusal** — ask the agent
   something the data cannot support, or something its seat is not
   permitted to do. It must say so. An agent that invents a confident
   answer fails that task regardless of how well it handled everything
   else. Needs a deliberately chosen example (e.g., a cross-app query that
   should hit `403`, or a jurisdiction/employee combination with no data).
5. **Tests written by hand** — 10 points per test, 100 points per real bug
   found in AgentSwitch itself. **A test written by Claude or Codex scores
   zero.** I (Claude) can build harness scaffolding, fixtures, and
   verifier infrastructure, but the graded test assertions themselves must
   be authored by the team, not generated.

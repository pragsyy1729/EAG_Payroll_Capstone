# Payroll Agent — Requirements

Team 12's capstone deliverable. This is the working requirements catalog for
the payroll domain the agent must cover, built on the generic backbone in
`payroll_agent/` (`core/live_graph`, `economics`, `events`, `ui`, `gateway.py`).

No domain wiring (skills, planner, routes) exists yet — this file is the spec
that wiring should be built against.

## 0. Minimum bar

The agent must answer, at minimum:

- **"Run August payroll"** — an action/mutation flow
- **"Why is Ramesh's net pay lower?"** — an explain/diagnostic flow

Everything below is the full domain these two entry points expand into.
Multi-country from day one: **India and US** are both in scope, not India-only.

## 1. Cross-cutting architecture requirement: jurisdiction abstraction

The single biggest structural requirement this catalog surfaces: rules differ
by **country → state/region → locality**, not just by country. A US employee's
applicable rules depend on where they *work*, which may differ from where the
company is based or where they live. India has its own single-country
complexity (state-varying Professional Tax) that follows the same shape.

Every skill below (`run_payroll`, `explain_variance`, `process_exit`, ...)
must take a resolved jurisdiction per employee and look up rules from a
**data-driven jurisdiction table** (YAML, following the existing
`pricing.yaml` / `routing.yaml` pattern already used by `glc_v5` and
`economics/tiers.py`) — never hardcoded per-country branches in skill code.
This is a required MVP building block, not a stretch feature, because e.g.
"why is net pay lower" and "process exit settlement" resolve differently by
jurisdiction.

## 2. Run-payroll scenarios (mutation family)

- Standard monthly cycle run: cutoff → gross-to-net → register review →
  approval → bank file / disbursement → payslip issue → statutory filings
- Off-cycle / supplementary run (missed employee, correction batch,
  bonus-only run)
- Partial re-run after a correction, without double-paying already-disbursed
  employees
- Multi-entity / multi-location run (different state PT slabs, different pay
  calendars)
- Multi-currency / expat payroll run
- Dry-run / simulation mode (compute without disbursing)
- Reversal of a run (bank rejects a batch, wrong amount disbursed)
- Approval-gated run — register must be signed off before disbursement
  (`ui/hitl.py` approval card)
- Budget-capped run — projected payroll cost must fit an economics ceiling
  before the run starts (`economics.BudgetedGateway`)

## 3. Explain / diagnostic scenarios (the "why" family)

- "Why is X's net pay lower/higher than last month?" — decompose into: LOP /
  unpaid leave, missed attendance, revised CTC or salary, tax
  regime/withholding change, new/removed deduction, bonus/incentive absent
  this cycle, arrears reversed, garnishment newly applied, statutory
  wage-ceiling crossed (PF, FICA Social Security cap), benefits
  open-enrollment change, PFML premium change
- "Why did my [TDS / federal withholding] jump this month?"
- "Why is my [PF / 401(k) match] lower than expected?"
- "Compare my payslip month-over-month / year-over-year"
- "Show the full gross-to-net trace for this payslip" (component-by-component
  audit trail)
- "Is this discrepancy an error or expected?" — classify variance as
  policy-driven vs. anomalous

## 4. Statutory & compliance scenarios — India

- **PF**: 12% employee + 12% employer on Basic + DA; wage-ceiling logic;
  opt-out edge cases
- **ESI**: 0.75% / 3.25% split; ₹21,000/month eligibility threshold;
  crossing-threshold mid-year handling
- **Professional Tax**: state-varying slabs; multi-state employee handling
- **TDS** (Sec 192): old vs. new regime selection, monthly projection, 7th
  of month deposit deadline, quarterly filing (Form 138, replacing Form 24Q
  from Apr 2026)
- **Gratuity**: 1-year eligibility for fixed-term employees (Nov 2025 reform)
  vs. 5-year standard
- **Code on Wages 2019**: Basic must be ≥50% of CTC — flags non-compliant
  salary structures
- Statutory Bonus Act computation
- Labour Welfare Fund (LWF) contributions
- Filing/payment deadline tracking and reminders

## 5. Statutory & compliance scenarios — US

- **FICA**: Social Security 6.2%/6.2% up to the annual wage base ($184,500 for
  2026); Medicare 1.45%/1.45% uncapped + 0.9% additional Medicare
  (employee-only) above $200,000
- **FUTA** (employer-only, 6% on first $7,000/employee, effective 0.6% with
  timely SUTA credit) + **SUTA** (state-specific rate/wage base); annual
  Form 940 reconciliation
- **Federal income tax withholding** via Form W-4; re-file on life events
- **Multi-state withholding & reciprocity**: work-state vs. residence-state
  rules, reciprocity exemption forms (e.g. PA REV-419, NJ-165),
  "convenience of the employer" rule, nexus created the moment one employee
  works remotely from a new state
- **Local taxes** (city/county, e.g. NYC, Ohio municipalities)
- **Quarterly Form 941** (must reconcile to annual W-2/W-3 totals) and
  annual **Form 940**
- **Year-end**: W-2 to employees + SSA by Jan 31; **1099-NEC** to
  contractors by Jan 31
- **Worker classification (W-2 vs. 1099)**: misclassification risk flag
  when a "contractor" looks like a de facto employee
- **I-9 / E-Verify**: Section 1 on day one, Section 2 within 3 business
  days; E-Verify mandatory in ~22 states or for federal contractors
  >$150k
- **New-hire state reporting**: federal 20-day ceiling, some states require
  within 7 days

## 6. Overtime & wage-hour scenarios (US-driven, generalize per country)

- FLSA: 1.5x regular rate for hours >40/week, computed weekly (no averaging
  across weeks)
- Exempt/non-exempt 3-part test (duties + salary basis + $684/week =
  $35,568/year floor) — "why did this employee suddenly become
  OT-eligible" diagnostic
- Misclassification scenario: job title alone doesn't confer exemption
- State overtime variations (e.g. California daily overtime over 8 hrs/day)
  — jurisdiction-dependent, same abstraction as tax

## 7. Salary structure / compensation scenarios

- India CTC breakdown: Basic, HRA (40%/50% metro-nonmetro of Basic),
  Special Allowance (catch-all), LTA, variable/performance pay, employer
  PF/ESI/gratuity accrual
- CTC/compensation restructuring what-ifs
- Salary revision effective mid-cycle, with correct proration
- Promotion/transfer between entities or cost centers

## 8. Exception & correction scenarios

- Loss of Pay (LOP) proration
- Arrears (owed-but-unpaid catch-up)
- Retroactive pay (correcting a miscalculation after the fact)
- Back pay (legal/dispute-driven, distinct from retro pay)
- **Full & Final Settlement on exit** (India-style): unpaid salary, leave
  encashment, gratuity, bonus, notice-period recovery, asset/loan clearance
- **Final paycheck on exit (US-style, jurisdiction-branching)**: timing is
  state-specific — CA/CO(on-site)/MO(on demand)/NV/MT require same-day or
  near-immediate; most others allow next regular payday; AL/FL/GA/MS have no
  state statute (falls to FLSA default). PTO/vacation payout at exit is
  mandatory only in CA, CO, MT, NE, ND — must not be assumed everywhere
- New joiner mid-month (pro-rata first payslip)
- Employee on long unpaid leave (multi-month LOP)
- Duplicate/erroneous payment recovery
- "Recurring retro pay" as a process-health signal (flag if the same
  employee needs correction 2+ cycles running)

## 9. Deductions, benefits & recovery scenarios

- **India**: employee-requested deduction changes, insurance opt-in/out
- **US pre-tax vs. post-tax**: traditional 401(k) / Section 125 health
  insurance / FSA / HSA (pre-tax, also reduces FICA) vs. Roth 401(k)
  (post-tax). 2026 limits: 401(k) $24,500 (+catch-up $8,000 / $11,250 by
  age band), HSA $4,400/$8,750, Health FSA $3,400, Dependent Care FSA
  $7,500. Open-enrollment-driven net-pay change is a distinct "why is net
  pay different" root cause
- **US state Paid Family & Medical Leave (PFML) premiums**: WA (1.13%,
  split 28.57% employer / 71.43% employee), MA, DE, ME, MN, others — each
  state has its own rate, split, and effective date; jurisdiction-table
  driven like tax
- **Wage garnishment / court orders**: priority ordering (child support >
  tax levy > other); US federal cap under CCPA — lesser of 25% of
  disposable earnings or wages above 30x federal minimum wage
- Salary advance / loan recovery installment schedule against payroll
- Multiple concurrent garnishments on one employee — conflict/priority
  resolution

## 10. Approval, audit & fraud scenarios

- Segregation of duties: preparer ≠ approver ≠ disburser (`auth.py`'s
  separate control/completion tokens + `ui` approval surface)
- Register review before disbursement — numbers must be human-approved, not
  silently executed (`AutonomyGovernor` + HITL card)
- Ghost-employee detection (payee not in active HR roster)
- Duplicate-payment detection
- Unusual variance flags (large unexplained jump, negative net pay, gap
  between hours and wage)
- Full audit trail per payslip (who changed what, when, why) — maps to the
  durable `events` journal already in the repo
- Payroll-vs-bank-statement reconciliation
- Payroll-vs-general-ledger reconciliation — the seam to the class's
  **Ledger Agent** (A2A handoff)

## 11. Data & integration scenarios

- Attendance/leave system feed (auto pro-ration on LOP, new joiner, exit)
- HRMS feed (promotions, transfers, revisions flow into payroll
  automatically)
- Expense/reimbursement system feed
- Banking integration (disbursement file generation, rejection handling)
- Tax/filing portal integration (TRACES, EPFO, ESIC for India; IRS/SSA/state
  agencies for US — likely simulated in this capstone)

## 12. Reporting & employee self-service scenarios

- Payslip generation and plain-language explanation
- Annual tax statement generation (Form 16 for India; W-2/1099 for US)
- Cost-center / department payroll cost reports
- "What will my take-home be if I switch tax regimes / benefit elections?"
- Proactive notifications ("your payslip is ready", "your withholding will
  change next month unless you submit proofs / update your W-4")

## 13. Agent-governance requirements (specific to this repo's architecture)

- Every payroll mutation is metered and budget-capped before execution —
  reuse `economics.BudgetedGateway`; disbursement math is never trusted to a
  raw LLM call
- Every irreversible action (disbursement, filing) requires an approval
  card, not autonomous execution — `ui/hitl.py`
- Explain flows are read-only / no budget risk and can run with looser
  autonomy than run/mutate flows — a natural tier split in
  `economics/tiers.py`
- Self-actor guard: payroll agent must not process an event where the actor
  is itself (`PAYROLL_SELF_ACTORS`) — relevant for auto-triggered reminders
- A2A handoffs: Ledger Agent (post journal entries), AP/Tax Agent (remit
  TDS/PF/ESI or FICA/FUTA/SUTA), Comms Agent (payslip notifications) — each
  scenario above should eventually be tagged with which sibling agent it
  delegates to, if any

## 14. AgentSwitch — the live backend

`schemas.json` (repo root) and `openapi.json` (repo root) describe a separate
platform called **AgentSwitch** ("schema-driven, agent-composed business
platform") shared across all 12 teams' apps. **Decision: AgentSwitch is the
live backend for this agent** — payroll skills call it for real data and
execution rather than payroll_agent inventing its own data model or
gross-to-net math.

### What it provides

- **448 entities across 27 domains** (`schemas.json`), including a
  **payroll domain with 36 entities already fully designed and already
  multi-jurisdiction**: `PayRun.payroll_jurisdiction` is a literal `["IN",
  "US"]` enum, and `PayRunEmployee` carries India fields
  (`epf_employee`, `esi_employee`, `professional_tax`, `tds`) and US fields
  (`social_security_employee`, `medicare_employee`, `federal_income_tax`,
  `futa_wages`, `suta_wages`, `retirement_401k_employee`) on the same
  record. This *is* the jurisdiction rules table referenced in §1 — it
  already exists server-side; we don't need to design a new one, only
  consume it.
  - India config entities: `EPFConfig`, `ESIConfig`, `PTConfig`,
    `LWFConfig`, `TaxConfig`, `Form16Record`, `Form24QRecord`
  - US config entity: `USPayrollConfig` (federal EIN, work state, SUTA
    state/rate/wage base, local taxes)
  - `PayRun.run_type`: `["regular", "off_cycle", "bonus", "correction"]`
  - `PayRun.status`: `["draft", "review", "pending_approval", "approved",
    "paid", "cancelled"]` — the approval-gated-disbursement state machine
    from §10 already modeled
  - `PayrollRunControl.funding_state`: `["not_checked", "cleared",
    "insufficient", "no_source", "unverifiable", "no_lines"]` — a
    pre-disbursement funding check, same spirit as `economics.BudgetedGateway`
  - `FinalSettlement`, `EmployeeLoan`, `SalaryRevision`,
    `ReimbursementClaim`, `InvestmentDeclaration`, `ProofOfInvestment` map
    directly onto the §8/§9 exception and deduction scenarios
  - `EmployeeDocument`/vault entities handle payslip and Form16 delivery
    plus employee self-service acknowledgement

- **1,846 REST endpoints** (`openapi.json`), two layers:
  1. **30 curated payroll business endpoints** under `/api/payroll/*`:
     `POST /run`, `POST /calculate` (run delegates to calculate — one
     statutory-engine path, not two), `/off-cycle/runs` (create, read,
     board, reschedule, verify-funding), `/final-settlement/{id}/calculate`,
     `/form16/generate`, `/form24q/generate`, `/reports/statutory`,
     `/reports/headcount`, `/reports/attendance-summary`, `/locale`,
     `/vault/*` (documents, publish, email, employee self-service +
     acknowledgement)
  2. **Generic entity CRUD** covering all 448 schema entities:
     `GET/POST /api/{entity_name}`, `GET/PUT/DELETE
     /api/{entity_name}/{entity_id}`, plus `/transition`,
     `/submit-for-approval`, `/approve`, `/reject` (gated by each entity's
     declared `behaviors`, e.g. `submittable`), `/aggregate`,
     `/validation-rules`, bulk import/export. This is how `explain_variance`
     reads e.g. two `PayRunEmployee` records to diff, and how `run_payroll`
     would read `Employee`/`SalaryStructure` rows it doesn't have a curated
     endpoint for.
  3. A separate agent-hosting layer (`/api/agent/chat`, `/api/agent/seats`,
     `/api/agent/tools`, `/api/agent/live-tools/execute`) that is
     AgentSwitch's own agent runtime — distinct from our S17Code-derived
     harness. Not yet evaluated whether/how this overlaps with our
     `live_graph` executor; treat as informational until decided.

### Auth: resolved — two separate tenants, not one multi-jurisdiction instance

`openapi.json` has no `servers` block and no `securitySchemes`, but the user
supplied the actual base URLs and login flow. The key structural point:
**India and US are two independently deployed AgentSwitch tenants**, each
with its own base URL and its own login — not one instance selected by a
`payroll_jurisdiction` request parameter:

- **Suryodaya** (India) — `https://agentswitch.theschoolofai.in`
- **Keystone** (US) — `https://class.agentswitch.theschoolofai.in`

Auth is session-token login, confirmed against `openapi.json`'s
`POST /api/auth/login` (`platform` tag; untyped `{email, password}` request
body, untyped response — matches `{"token": ...}` from the team's example):

```bash
export AS=https://agentswitch.theschoolofai.in   # or the Keystone URL
export TOKEN=$(curl -s -X POST "$AS/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"email":"teamNN@theschoolofai.in","password":"YOUR_PASSWORD"}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')
```

Confirmed (class integration guide, 2026-09-28): every later call carries
`Authorization: Bearer $TOKEN`. Anonymous is refused with `401`. Bearer
requests need no CSRF token. Identity/scope check:

```bash
curl -s "$AS/api/auth/me" -H "Authorization: Bearer $TOKEN"
```

The response's `roles` and `allowed_apps` *is* our seat, stated by the
server — not something we configure locally.

Env var scaffolding for this now lives in `harness/.env.example`
(`AGENTSWITCH_IN_BASE_URL`/`_EMAIL`/`_PASSWORD` and the `_US_` equivalents).
Real credentials go only in a local untracked `.env`, never in this file
or in chat.

### The interface to build against: MCP, not REST

The class integration guide is explicit: **"What you are actually building"
is your own harness, with your own model keys, driving AgentSwitch over
MCP — your own harness, not a wrapper around theirs.** That "own harness
with own model keys" is exactly `payroll_agent` + `glc_v5` already. What
changes is the capability layer: instead of hand-writing REST calls to
`/api/payroll/*`, payroll_agent's workers should call AgentSwitch's **MCP
tool catalogue**, which is scoped per-seat and already carries JSON
Schemas per tool — i.e. it's a ready-made source for `capabilities.py`-style
declarations, not something we invent by hand.

One login opens three doors onto the *same* data and permission rules:

1. **MCP** (`POST $AS/api/mcp`) — the primary interface; this is what our
   agent should drive
2. **The web UI** — same URL, same credentials; for a human (us) to see
   what the agent did and learn the domain, not for the agent to drive
3. **REST** (`/api/payroll/*`, `/api/{entity_name}`, from §14 above) —
   documented, works, but secondary/fallback

Recommended first step before writing any agent code: spend ~10 minutes in
the web UI looking at real records (a real `PayRun`, a real `PayRunEmployee`)
to see actual fields and workflow states — "ten minutes in the UI teaches
you more than an hour of guessing at schemas."

**MCP protocol mechanics:**
- One endpoint, JSON-RPC 2.0: `POST $AS/api/mcp`. Protocol version
  `2025-11-25`. No SSE stream (`GET /api/mcp` → `405` with `Allow: POST`
  — that means "no stream," not "wrong verb"). No batching.
- Methods: `initialize`, `notifications/initialized`, `tools/list`,
  `tools/call`.
- Handshake:
  ```bash
  curl -s -X POST "$AS/api/mcp" -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
         "params":{"protocolVersion":"2025-11-25","capabilities":{},
                    "clientInfo":{"name":"team-NN","version":"0.1"}}}'
  ```
- Discover our scoped tools: `tools/list` with `params:{}`.
- Call one: `tools/call` with `params:{"name":"WorkOrder.list","arguments":{}}`
  (payroll equivalent: `PayRun.list`, `PayRunEmployee.get`, etc.)
- **Tool naming convention**: `<Entity>.list`, `<Entity>.get`,
  `<Entity>.create`, `<Entity>.update`, one tool per workflow transition
  (matching each entity's `status`/state-machine field), plus the app's own
  curated endpoints (the `/api/payroll/*` business routes from §14 likely
  surface here too, not just generic CRUD).
- **Scoping is by omission, not refusal**: a tenant admin sees ~2,639
  tools; our seat sees a few hundred — only entities/endpoints our seat
  may use. A tool outside our authority is **absent from `tools/list`**,
  not refused when called — we cannot even name another app's tool.
- **Every tool carries a closed JSON Schema** for its arguments — an
  argument not in the schema is rejected, not ignored. This is directly
  analogous to `Argument`/`Capability.manifest()` in `s17code/capabilities.py`
  (§ from S17Code exploration) — AgentSwitch is handing us the manifest
  instead of us writing one by hand.
- **Critical correctness gotcha**: a JSON-RPC error still returns **HTTP
  200** — the failure is in the response envelope (`error` field), not the
  status code. Only authentication failures use the HTTP layer (`401`).
  **`AgentSwitchClient` must check the JSON-RPC envelope on every call, not
  just `response.status_code`, or it will read every denial as success.**
  This is the single most important implementation detail from the guide.

**Live schema/tool discovery** (prefer these over the static exported
files when building, since they reflect our actual seat's scope):
- `$AS/docs` — interactive API explorer (Swagger UI)
- `$AS/redoc` — same surface, laid out for reading
- `$AS/api/schemas` — all entity schemas: fields, types, options, required
  (the live/authoritative version of the `schemas.json` we were handed —
  note the guide says 424 entities vs. our static export's 448; re-fetch
  live rather than trusting the static count)
- `$AS/api/agent/tools` — 13 *platform-level* agent tools (the
  `/api/agent/*` layer from §14, distinct from our per-seat MCP business
  tools)

### Data visibility and concurrency rules (confirmed against live instances)

- **Our app: yes. Another team's app: no.** Cross-app data access is a
  `403` by design — verified live: `SalarySlip`, `Contract`,
  `EsignDocument` all return `403` from a seat that doesn't own them.
  This is not a bug to route around; the intended path for legitimate
  cross-app need is asking a human/admin, not finding another API shape
  that bypasses it.
- **Teams sharing a seat type share a book** — rows, not copies. If a
  sibling team edits or deletes a shared row, we see that change; nothing
  here is snapshotted per team. **Design implication for every skill that
  reads-then-acts:** re-read immediately before acting rather than trusting
  a value fetched even a moment ago, never assume a previously-seen row is
  still unchanged, and never "clean up" or delete data our own workflow
  didn't create. This is a real concurrency hazard `run_payroll` and
  `explain_payslip_variance` must be written against, not an edge case.
- **Our agent's own memory is private.** Anything payroll_agent writes to
  `AgentMemory`, `AgentMessage`, or `AgentSkill` from now on belongs to us
  and is invisible to other teams; pre-existing seed rows remain shared.

### Best-in-class reference products (guide's explicit Step 2)

The integration guide's four-step approach (Step 2 of 4, the one it warns
teams skip) directs us to find and study a real modern payroll product
doing this job — "usually better than [AgentSwitch does]," AI-native
preferred over a legacy product with a chatbot bolted on. **This is on us
to research, not something AgentSwitch provides.** Not yet done — candidates
to evaluate given our India+US scope: Gusto, Rippling, Deel, Zoho Payroll,
Keka (India-strong). Steps 3 and 4 of the guide were not captured in the
screenshots shared so far — ask the user for the rest of that section
before treating "what to build" as fully specified.

## 14a. Live probe results (2026-09-29, Suryodaya/India, seat team12@theschoolofai.in)

Confirmed via a real login + `initialize` + `tools/list` call (script kept
out of the repo; output saved to `harness/agentswitch_tools_in.json`,
gitignored-equivalent — treat as a local artifact, not a committed file).

- **Identity** (`/api/auth/me`): `team12@theschoolofai.in`, roles
  `["hr_user","payroll_user","user","agent_user","sales_viewer"]`,
  `allowed_apps: ["payroll","agent","crm"]`. Our seat also has read-only
  CRM exposure incidentally — stay scoped to payroll for the deliverable
  regardless.
- **MCP `initialize`**: protocol `2025-11-25`, server `agentswitch` v1.0.0.
  Its own `instructions` field states the scoping model plainly: *"Tools
  are scoped to the authenticated caller: you see only what your roles,
  app entitlements and row scope permit, and every call is executed
  through the same permission-checked path the REST API uses."*
- **`tools/list`**: 345 tools total visible to our seat (matches the
  guide's "a team seat sees a few hundred"), 96 payroll-relevant by name.
- **`PayRun.run_payroll`** is *exactly* the "Run August payroll" primitive:
  `{"month": "YYYY-MM"}` (required), optional `payrun_id` to recalculate
  an existing run instead. Description: *"Open (or reuse) the pay run for
  a month in your company and calculate draft salary slips."* This is
  the single call `run_payroll` (our skill) should center on.
- **Segregation of duties is enforced at tool-visibility level, not just
  documented policy**: our seat's tool list has `PayRun.submit_for_approval`
  but **no `PayRun.approve`/`PayRun.reject`** (same for `EmployeeLoan`,
  `SalaryRevision`, `ProofOfInvestment`, `FinalSettlement` — each has
  `.submit`/`.approval.submit` but no approve/reject tool). Our agent
  cannot self-approve even if it wanted to; the tool doesn't exist for
  this role. This validates the request-approval/HITL design in §10
  as necessary, not optional.
- **MCP does not have 1:1 parity with the curated REST business
  endpoints for our role — confirmed, partially resolved.** Checked
  `PayRun.create`'s full input schema (37 properties): **`run_type`
  (`["regular","off_cycle","bonus","correction"]`, default `"regular"`)
  and `off_cycle_reason` are both real arguments.** So off-cycle runs are
  fully reachable over MCP —
  `PayRun.create({..., "run_type": "off_cycle", "off_cycle_reason": "..."})`
  — no REST fallback needed there. Required fields on `PayRun.create`:
  `pay_period`, `pay_period_start`, `pay_period_end`, `pay_date`.
  **Resolved 2026-09-29 with positive evidence, not just absence.**
  AgentSwitch has a second tool convention beyond `<Entity>.<verb>`:
  app-specific curated endpoints are sometimes exposed as
  `endpoint.<app>.<name>` (e.g. `endpoint.storefront.checkout`,
  `endpoint.crm.call_notes`, `endpoint.accounting.supplier_scorecard` — 47
  such tools exist across the catalogue). **Zero `endpoint.payroll.*`
  tools exist for our role.** Combined with `Form16Record`/`Form24QRecord`
  having only `.get`/`.list`, and no vault/reports/locale tool under any
  name, this is now a confirmed finding rather than an absence-of-proof:
  **Form16/Form24Q generation, vault document operations, and
  reports/locale are REST-only for this seat.** `AgentSwitchClient` needs
  a REST fallback path specifically for these four action groups — same
  bearer token, `/api/payroll/form16/generate`,
  `/api/payroll/form24q/generate`, `/api/payroll/vault/*`,
  `/api/payroll/reports/*`, `/api/payroll/locale` (§14 has the full list).
  Everything else stays MCP-only. Not yet distinguished whether this is a
  deliberate platform choice (these skew document-generation/PII-adjacent,
  plausibly kept off the agent-callable surface on purpose) or a role gap
  a different seat would see — doesn't change what to build either way.
- Workflow-transition tools carry the transition in the name, e.g.
  `EmployeeLoan.cancel.draft.cancelled`,
  `ReimbursementClaim.revise.partially_approved.draft` — richer than the
  generic `/transition` REST route suggested; the *from* and *to* state
  are often baked into the tool name itself, not passed as arguments.

## 14a-2. Live probe results (2026-09-29, Keystone/US, same seat)

Same seat (`team12@theschoolofai.in`, identical roles/`allowed_apps`),
different `company_id` (`c1e47d8d-...` vs. IN's `5cbe5a55-...` — genuinely
separate tenant data, as expected).

**The tool catalogue is byte-identical to India's: 345/345 tool names,
confirmed by diff, including India-only entities (`EPFConfig`, `ESIConfig`,
`PTConfig`, `LWFConfig`) still present as callable tools on the US
tenant.** This means jurisdiction is purely a *routing and data* concern,
not a *tool selection* concern: the same global schema/tool set is
deployed to every tenant; a US employee's records simply won't have
meaningful `EPFConfig` rows (and a India employee's `USPayrollConfig`
rows will be empty), but the tool names and argument contracts to call
don't change. Simplifies `AgentSwitchClient`: one `call_tool(name,
arguments, jurisdiction)` interface, no per-jurisdiction tool-name
branching — jurisdiction only selects which (base_url, token) pair a call
is sent to.

## 14b. Evaluation harness — reference pattern from S17Code

Read in full: `session_17/S17Code/proofs/harness.py` (320 lines) and
`proofs/general_agent_live.py` (352 lines). This is the direct reference
for the "harness" grading deliverable (§15 below) — reuse the shape,
extend one real gap.

**`harness.py` — the reusable library** (not task-specific):
- `Args`: CLI-parsed run parameters (task, budget, principal, live/offline
  mode, base URL, ...)
- Two transport modes, same code path: `live` (real gateway, probed via
  `/healthz`) vs. `offline` (deterministic stand-in — no network, no keys,
  what makes CI possible without credentials)
- `Proof`: the checks collector. `.check(claim, ok, observed)`,
  `.fact(key, value)` (goes in the human table), `.record(key, value)`
  (JSON-only detail), `.ok` (all checks passed), and critically
  **`.finish()` writes the full JSON to `proofs/out/<name>.json` *before*
  printing anything, then exits non-zero on any failed check** — this is
  the literal implementation of "every run written to disk before
  anything is scored."

**`general_agent_live.py` — the live, task-driven proof:**
- Task facts/expectations live in JSONL (`proofs/tasks/*.jsonl`), not
  Python — replacing a task never means editing the harness
- `score_contract()`: a large, purely declarative, domain-agnostic scorer
  reading the run's actual graph (`nodes`, `events`) against a `checks`
  dict: `required_capabilities`, `required_any_capabilities`, `min_nodes`,
  `parallel_capability` (verifies genuine concurrency via
  `task_started`/`task_succeeded` event overlap, not just "both ran"),
  `succeeded_after`/`succeeded_after_any` (ordering), `answer_contains`/
  `answer_not_contains`, `must_have_failed_node`, `files_exist`/
  `files_equal`/`file_contains` (real filesystem checks), `json_file_contract`
  (structural checks: required fields, forbidden keys, distinct arrays,
  integer ranges), `surface_min_components`/`surface_values`
- `judge_quality()`: an optional, explicitly secondary LLM-judge pass —
  never the only check, always alongside `score_contract`'s hard
  assertions, and disableable (`--no-judge`)
- Runs write to disk **incrementally inside the loop** (`_save()` called
  after every task, not just at the end), so a crash mid-run doesn't
  lose already-scored results

**The one real gap to close for our version:** every check in
`score_contract()` reads *our own graph journal* — nodes and events from
`payroll_agent`'s own run. **None of them re-read AgentSwitch.** The
grading rubric specifically wants verifiers that read the database rather
than the agent's prose — for us that has to mean re-querying AgentSwitch
itself after the run (e.g. call `PayRun.get` and assert `status` actually
changed there, not just that our local node marked `succeeded`), not only
checking our own journal. Plan to add check types like
`agentswitch_entity_field_equals`/`agentswitch_entity_exists` alongside
the existing journal-based ones, backed by a read-only `AgentSwitchClient`
call inside the verifier — separate from the client the agent itself uses,
so a verifier bug can't be masked by the same code path it's checking.

## 15. Grading and deliverables (integration guide, steps 3–4)

Full detail and the team's own week-1 gap report are in `harness/GAP_REPORT.md`.
Five things are graded:

1. **The gap report** — what the best reference products do that
   AgentSwitch doesn't, which gaps an agent can close via orchestration
   today vs. which need platform (AgentSwitch team) work. Due week one.
2. **The agent** — answering the seat's payroll questions against live
   data other teams are changing underneath it concurrently.
3. **The harness** — a *separate evaluation artifact* from this
   `harness/` package directory: our own run loop, a task set with
   verifiers that read AgentSwitch's actual database state rather than
   trusting the agent's prose output, every run written to disk before
   anything is scored. Reference pattern: `S17Code/proofs/harness.py` and
   `proofs/general_agent_live.py`.
4. **At least one task whose correct answer is refusal** — something the
   data can't support or the seat isn't permitted to do. The agent must
   say so; a confident invented answer fails that task outright.
5. **Hand-written tests** — 10 points per test, 100 per real bug found in
   AgentSwitch itself. **A test written by Claude or Codex scores zero.**

**Standing constraint on how I (Claude) help with this project:** I can
design and scaffold the evaluation harness, fixtures, task-runner, and
verifier *infrastructure*, but I must not author the graded hand-written
test assertions themselves — that credit only counts if the team writes
them.

## 16. Open / next steps

Done: first live `initialize`/`tools/list` call against Suryodaya (§14a)
and Keystone (§14a-2) — tool catalogue confirmed identical across tenants;
reading `S17Code/proofs/harness.py` + `general_agent_live.py` (§14b).

- [x] **`AgentSwitchClient` built and live-verified** —
      `harness/payroll_agent/agentswitch.py`. Holds both tenants (`IN`/`US`)
      from `AGENTSWITCH_*` env vars; `call_tool()` speaks MCP JSON-RPC and
      checks the `error` envelope (verified live: invalid-argument and
      unknown-tool calls both correctly raise `AgentSwitchToolError` rather
      than reading as success); `list_tools()` caches the scoped catalogue;
      `rest()` is the fallback for the four confirmed MCP-absent action
      groups. Verified live end-to-end: successful `.list`/`.get` calls,
      the error path, an unconfigured-jurisdiction fail-loud path, and one
      real REST call (`/api/payroll/locale`, returned `₹`/`indian_lakh_crore`
      /April-fiscal-year for the IN tenant). Confirmed response shapes:
      `.list` → `{"data": [...], "limit", "offset", "total"}`; `.get` →
      the record flat, plus `_permissions`/`_readonly_fields`/`_transitions`
      (self-describing legal next state-machine moves — useful for a skill
      to introspect before attempting a write, not something the client
      normalizes away).
- [ ] Design the payroll evaluation harness per §14b: reuse `Proof` and
      the declarative-JSONL-task shape from `general_agent_live.py`, but
      add check types that re-read AgentSwitch itself (not just our own
      graph journal) via a separate read-only verifier client
- [ ] Choose the deliberate refusal-task example (grading requirement 4)
- [ ] Revisit `GAP_REPORT.md`'s question-2 answer now that the live
      catalogue is in hand — update per §14a's findings (e.g. approval
      quorum is confirmed reachable via the generic `approvals` domain;
      confirm before finalizing)
- [ ] Spend ~10 minutes in the AgentSwitch web UI on a real `PayRun` /
      `PayRunEmployee` before finalizing skill contracts
- [ ] Research a best-in-class reference payroll product (guide's Step 2)
- [ ] Design every read-then-act skill to re-read immediately before
      acting, given shared/live data across teams (see "Data visibility and
      concurrency rules" above)
- [ ] Turn each §2–§13 scenario category into named skills for
      `live_graph` (e.g. `run_payroll`, `explain_payslip_variance`,
      `process_exit_settlement`, `apply_garnishment`, `simulate_ctc_change`),
      each backed by one or more AgentSwitch calls rather than local
      computation
- [ ] Confirm how `/api/agent/*` (AgentSwitch's own agent runtime) relates
      to our `live_graph`/`capabilities.py` approach — avoid building two
      competing execution layers
- [ ] Prioritize MVP vs. stretch given the capstone deadline
- [ ] Add further countries the same way if scope expands again (the
      `payroll_jurisdiction` enum and per-jurisdiction config entities are
      the extension point)

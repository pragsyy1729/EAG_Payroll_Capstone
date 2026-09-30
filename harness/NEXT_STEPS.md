# Next Steps

Status snapshot as of 2026-09-29 (end of day). See `REQUIREMENTS.md` for the
full requirements catalog and AgentSwitch integration details, and
`GAP_REPORT.md` for the week-one deliverable.

## What exists now

- `payroll_agent/agentswitch.py` -- `AgentSwitchClient`. MCP-first, REST
  fallback for the four confirmed MCP-absent action groups (Form16/Form24Q
  generation, vault, reports, locale). Holds both jurisdiction tenants
  (`IN`/`US`). Live-verified.
- `payroll_agent/capabilities.py` -- seven capabilities: `list_employees`,
  `list_payruns`, `get_payrun`, `list_payrun_employees`, `run_payroll`,
  `submit_payrun_for_approval`, `answer_with_evidence`. Id arguments carry
  `format="id"`, which turns on the provenance check (below).
- `payroll_agent/planner.py` -- `PayrollPlanner`. Now receives today's date,
  resolves a bare month ("August") to its most recent occurrence, and
  validates every id-valued argument against evidence the run has actually
  seen (goal, initial evidence, succeeded outcomes). System prompt states
  which `*_id` fields are an employee's id and which are not.
- `payroll_agent/workers.py` -- one async function per capability.
- `payroll_agent/run.py` -- CLI runner. No `main.py`/FastAPI service yet.
- `payroll_agent/gateway.py` -- `PAYROLL_GATEWAY_MODEL` env override.
  `.env` (gitignored) currently sets `gemini-3.5-flash`.

## Done today

- **`run_payroll` guard** (`workers.py`). `PayRun.run_payroll` *reuses* an
  existing run for the month and rewrites its slips. The worker now looks up
  the month's regular runs by period dates (not labels) and refuses unless
  every match is `draft` or `review`; an explicit `payrun_id` is checked the
  same way. Refusal is returned as `run_exists_not_recalculable` with the
  existing run, and the answer step is told to report it as "not performed".
- **Date-based month lookup.** `list_payruns` takes `month` (YYYY-MM) and
  matches on `pay_period_start`. The label filter missed "Aug 2026" when
  asked for "2026-08".
- **Wrong-year fix.** The planner used 2025-08 for "August"; it now sees
  `today` and picks 2026-08.
- **Evidence critic** accepts a runtime refusal as ready evidence (before,
  a guarded run ended with no answer).
- **Provenance check** rejects invented ids before they reach AgentSwitch.

## Verified live

- **"Run August payroll" (IN)**: resolves 2026-08, finds `PRUN-2026-00012`
  (`pending_approval`), refuses to recalculate, answers that the action was
  NOT performed. No new PayRun created (count stayed 15).

## NOT verified

- **"Why is Ramesh's net pay lower this month?"** has never completed a live
  run. Best so far: three lookups, then a Gemini 503. Observed failures:
  the planner picked `company_id`, `department_id`, `created_by` and another
  employee's id as Ramesh's id, and once invented one. The real id
  (`Employee.id`, `f8e8a973-...`) was never tried. The provenance check and
  the id-precedence prompt are meant to fix this but are untested in a
  finished run.
- Whether `PayRunEmployee.employee_id` equals `Employee.id` (assumed, not
  confirmed).
- "This month" ambiguity: September's run (`PRUN-2026-00013`) is a `draft`
  with 51 employees; August (`PRUN-2026-00012`) has 65 slips.

## Blocker: Gemini quota

The shared `glc_v5` gateway (`session_17/glc_v5`, started manually with
`uv run glc serve`, log at `/private/tmp/claude-501/glc_serve.log`) has only
**two** Gemini keys (`GEMINI_API_KEY_1/2`) and no other provider keys.
After ~260 calls it returns 503 "RPM quota burned" even after 7 minutes of
quiet. Likely a daily limit mislabelled as RPM (`glc/routes/chat.py:362`
classifies any 429 containing "quota" as RPM); unconfirmed.

Ways out, in order of effort:
1. Wait for the daily reset (Google free tier: midnight Pacific), rerun.
2. Add a third key from a *separate* Google project as `GEMINI_API_KEY_3`
   (pool is built from `GEMINI_API_KEY_1..MAX_GEMINI_KEYS`), restart gateway.
3. Add a non-Gemini provider key (Groq etc.); `routing.yaml` already lists
   them as fallbacks.
4. Offline scripted transport (below).

**2026-09-30 update:** the daily limit has reset and the gateway (started
with `uv run glc serve` from `session_17/glc_v5`) answers. Live eval run of
`scaffold_august_refusal`: planner calls 1-2 succeeded (real `PayRun.list` +
`PayRun.get`, found `PRUN-2026-00012` `pending_approval`), call 3 failed twice
in a row, first with "RPM quota burned (~40s)" at 2s pacing, then with
"upstream 503" at 15s pacing (`--min-interval 15`). Probes show Gemini
latency of 8-9s for a 3-token call with retries, so the upstream is
overloaded or flaky, not out of quota. The harness scored both runs
`infra_error`, and the watched state was identical before/after.

Retry commands (from `harness/`, gateway must be running on :8111):
```bash
.venv/bin/python -m payroll_agent.run "Why is Ramesh's net pay lower this month?" --jurisdiction IN
.venv/bin/python -m payroll_agent.run "Run August payroll" --jurisdiction IN --allow run_payroll
```
The second is safe to repeat: the guard refuses while August is
`pending_approval`. Check `patch_events` in the output for planner failures.
Save output to a file: only the tail is easy to see in a terminal.

## Workflow roadmap (2026-09-30)

`WORKFLOWS.md` lists every query the agent should answer, by family (A
employee "why", B run lifecycle, C pre-payroll checks, D reporting, E
statutory, F lifecycle, G refusals), matched to the tools our seat has, with
built/partial/todo status. Proposed order:

1. [ ] **A: finish the Ramesh flow** (required query; needs the gateway).
2. [ ] **C: pre-payroll risk scan** as one sweep capability, computed in
       Python (LLM only plans and explains). Needs read capabilities over
       `SalarySlip`, `Attendance`, `LeaveApplication`, `PayrollBankAccount`.
       Design first (eval spec sub-project 2); not started.
3. [ ] **D: variance and cost reports** (month-over-month, by department).
4. [ ] **G: refusal tasks**: approve payrun, delete non-draft run, cross-app
       read, bank file. The pending-run refusal already works.
5. [ ] B/E/F extras (dry-run calculate, payslips, statutory dues, FnF).

The existing 7 capabilities cover only A (partly) and B (run/submit). Most
new work is read wrappers plus Python computation.

## Open issues on shared AgentSwitch data

- Our earlier unguarded run wrote **65 payslips** (created 2026-09-29
  15:44 UTC, `created_by: system`) under `PRUN-2026-00012`, a run already in
  `pending_approval`. Our seat has no delete/cancel for slips or non-draft
  runs, so it cannot be undone from here. Unknown whether the run header
  totals changed (`updated_at` still 2026-09-12). Tell the team / organizers.
- Candidate AgentSwitch bug report (100 pts each): `PayRun.run_payroll`
  silently rewrites slips of a `pending_approval` run.
- Cancelled our own stray draft `PRUN-2026-00015` ("August 2025").

## Near-term

- [ ] Get the Ramesh flow to complete live, then check the answer against
      the raw `PayRunEmployee` rows (net-pay difference explained by
      components, not invented).
- [x] Evidence-review LLM call in `planner.py` now fails visibly (shared
      `_call_failed` helper); verified by injecting a 503 at that call.
- [x] Offline/deterministic *scripted* LLM transport: `evals/transport.py`
      (`ScriptedLLM`, same `complete`/`close` surface as `GatewayClient`;
      replays JSON scripts from `evals/scripts/`, injects 429/502/503/timeout
      at a chosen call index). `run_goal(..., llm=, agentswitch=)` accepts it.
      Checked: happy path, 503 at planner call, exhausted script. It
      reproduced the uncaught evidence-review 503 (next item) deterministically:
      a fault at call index 2 of `scaffold_august_lookup.json` raises out of
      `run_goal`.
- [ ] Answer text had a stray `August 20%2026` (URL-encoding artifact);
      find the source.
- [x] Evaluation harness **core** built in `evals/` (runner, recorder,
      snapshot, verifiers, scoring, scripted + paced transports). Run:
      `.venv/bin/python -m evals.runner [--family F] [--task-id ID] [--live]`.
      Exit 0 pass / 1 fail / 2 infra-only. Raw record is written to
      `evals/runs/<ts>/<id>.json` before verifiers run; scores go to
      `<id>.score.json`. Checked with scaffold tasks only: pass, fail and
      infra_error paths, bad task file, gateway-down preflight. NOT yet
      checked: a live-LLM run (gateway was down), an armed `--live` mutating
      task, non-first-page snapshots, `state_changed`, `agentswitch_state`,
      `answer_grounded`, `refusal` against real data.
- [ ] **Team writes the graded tasks** (`evals/tasks/*.jsonl`, `authored_by:
      "team"`): goals, verifiers and expected values. `scaffold.jsonl` only
      shows the format and proves the mechanism.
- [ ] Sub-projects 2 and 3 of the eval spec (check workflows; guarded-action
      workflows).
- [ ] Choose the refusal-task example (something the data can't support or
      the seat isn't permitted to do). The `run_payroll` refusal on a
      pending run is a natural candidate.
- [ ] Revisit `GAP_REPORT.md` question 2: gaps 3, 5, 6 look
      orchestration-buildable.
- [ ] `payroll_agent/main.py` FastAPI service, after both flows pass.

## Deliberately not done

- Evidence summarizer for the planner (labelled `id:` blocks per record).
  Judged too much code fitted to one failure; prefer a stronger model or a
  provider with more quota first.
- Per-capability field-source mapping for ids: does not scale with ~96
  tools. Basic provenance only. Wrong-kind ids that exist in evidence are
  not caught by code; the prompt guidance covers them.

## Not urgent, but tracked

- [ ] Extend capabilities beyond the 7-capability MVP, ideally derived from
      AgentSwitch's scoped `tools/list` schemas.
- [ ] Keystone (US) live verification of the MVP flows.
- [ ] `PayRun.create` with `run_type=off_cycle` end to end.
- [x] Study a reference payroll product (integration guide Step 2). Done:
      `GAP_REPORT.md` is the result (six gaps vs. Rippling/Gusto/Keka/
      greytHR/Deel/Dayforce). Only its question-2 revision is still open.

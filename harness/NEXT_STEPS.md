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

Retry commands (from `harness/`, gateway must be running on :8111):
```bash
.venv/bin/python -m payroll_agent.run "Why is Ramesh's net pay lower this month?" --jurisdiction IN
.venv/bin/python -m payroll_agent.run "Run August payroll" --jurisdiction IN --allow run_payroll
```
The second is safe to repeat: the guard refuses while August is
`pending_approval`. Check `patch_events` in the output for planner failures.
Save output to a file: only the tail is easy to see in a terminal.

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
- [ ] Wrap the evidence-review LLM call in `planner.py` (~line 172): a
      gateway 503 there raises uncaught instead of failing visibly like the
      main planner call.
- [ ] Offline/deterministic *scripted* LLM transport (return a preset
      sequence of plans; simulate 503). The S17Code `OfflineTransport` only
      returns canned text, which a planner cannot use. Needed for repeatable
      evaluation runs and to stop burning quota.
- [ ] Answer text had a stray `August 20%2026` (URL-encoding artifact);
      find the source.
- [ ] Design and build the evaluation harness (`REQUIREMENTS.md` SS14b/SS15):
      own run loop, task file, verifiers that re-read AgentSwitch itself,
      every run written to disk before scoring. Graded test assertions must
      be hand-written by the team, not generated.
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

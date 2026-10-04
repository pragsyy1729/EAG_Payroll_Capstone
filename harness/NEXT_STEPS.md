# Next Steps

Status snapshot as of 2026-10-03. See `REQUIREMENTS.md` for the
full requirements catalog and AgentSwitch integration details, and
`GAP_REPORT.md` for the week-one deliverable.

## What exists now

- `payroll_agent/agentswitch.py` -- `AgentSwitchClient`. MCP-first, REST
  fallback for the four confirmed MCP-absent action groups (Form16/Form24Q
  generation, vault, reports, locale). Holds both jurisdiction tenants
  (`IN`/`US`). Live-verified.
- `payroll_agent/capabilities.py` -- eight capabilities: `list_employees`,
  `list_payruns`, `get_payrun`, `list_payrun_employees`, `pre_payroll_scan`,
  `run_payroll`, `submit_payrun_for_approval`, `answer_with_evidence`. Id
  arguments carry `format="id"`, which turns on the provenance check (below).
- `payroll_agent/planner.py` -- `PayrollPlanner`. Receives today's date,
  resolves a bare month ("August") to its most recent occurrence, and
  validates every id-valued argument against evidence the run has actually
  seen (goal, initial evidence, succeeded outcomes). System prompt states
  which `*_id` fields are an employee's id and which are not. `_clip` now
  keeps a record's identifying fields (`id`, `number`, `employee_id`,
  `payrun_id`) ahead of the 30-field cap (see "Wrong employee id" below).
- `payroll_agent/workers.py` -- one async function per capability.
  `pre_payroll_scan` is read-only; its checks are pure functions in
  `payroll_agent/scan_checks.py`.
- `payroll_agent/run.py` -- CLI runner. No `main.py`/FastAPI service yet.
  `run_goal(..., llm=, agentswitch=)` accepts a replacement model or client.
- `payroll_agent/gateway.py` -- `PAYROLL_GATEWAY_PROVIDER` /
  `PAYROLL_GATEWAY_MODEL` / `PAYROLL_GATEWAY_FALLBACK_PROVIDERS` env settings.
  `workers.py` also reads `PAYROLL_ANSWER_PROVIDER` / `PAYROLL_ANSWER_MODEL`
  to route the final answer call separately from planning (see "LLM
  providers"). All are documented in `.env.example`; `.env` is gitignored.
- `evals/` -- evaluation harness core (see Near-term).

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
- **"Why is Ramesh's net pay lower in July?" (IN)**: completes end to end
  with planning on NVIDIA `gpt-oss-20b` and the answer on Groq
  `gpt-oss-120b` (47 s, 14 model calls). Finds the real `Employee.id`,
  fetches the June and July payslips, and correctly attributes the drop to
  Overtime 2,538 -> 2,192 (-346) with EPF +2, checked against the raw
  `PayRunEmployee` rows. One end-to-end run on this setup; repeat-run
  reliability is not measured.

## NOT verified

- Repeat-run reliability of the Ramesh flow. Earlier end-to-end runs on
  weaker or mixed models completed 1 of 3; the routed setup has one run.
- **"This month" with no run.** Asked on 2026-10-03, "this month" resolves
  to October, which has no payrun; September (`PRUN-2026-00013`) is an
  uncalculated `draft` (51 rows, all net pay <= 0). The explicit "in July"
  phrasing works. What the agent should say for a month with no run is still
  undefined.
- `pre_payroll_scan` with a live LLM (scripted runs on the real tenant pass).
  Twice a live model tried it for the Ramesh question with a placeholder
  payrun id; the provenance check rejected it.
- US tenant (Keystone) for any of the flows.

## Wrong employee id: root cause (fixed 2026-10-03)

For several runs the planner passed `company_id`, an email, an employee
number or a payrun id as the employee id. The cause was `_clip` in
`planner.py`, which kept only the first 30 fields of each record while
AgentSwitch returns fields alphabetically: an Employee row has 67 fields and
`id` is number 41, so the model was never shown it, only `company_id`,
`email`, `employee_id_number` and similar. No model could get it right. The
provenance check could not catch it because it is built from the unclipped
results. Fixed by putting identifying fields first. The stricter "id must
appear as a record's own `id`" rule considered earlier is not needed unless
the mistake comes back.

Note `_clip` still shows only 30 of 67 fields, so planner prompts lack e.g.
`gross_pay`, `net_pay` and `lop_days` on payslip rows. The answer step reads
full rows, so answers are unaffected.

## LLM providers

Current setup (`harness/.env` and the gateway's `.env`, both gitignored):

| Use | Provider / model |
|---|---|
| Planning (many small calls) | NVIDIA `openai/gpt-oss-20b` |
| Final answer (one call) | Groq `openai/gpt-oss-120b` via `PAYROLL_ANSWER_*` |
| Fallbacks | `groq`, then `gemini` (`PAYROLL_GATEWAY_FALLBACK_PROVIDERS`) |

The gateway now running is this repo's own `glc_v5/` (the shared
`session_17/glc_v5` copy was stopped), started with `uv run glc serve`,
listening on :8111. Provider keys live in `glc_v5/.env`. The fallback client
does not send a model name, so each fallback provider's model must be set in
the gateway `.env` (`NVIDIA_MODEL`, `GEMINI_MODEL`); otherwise a rollover hits
a deprecated or unavailable default.

What we learned:
- **Gemini**: daily free-tier quota exhausted (HTTP 429 "exceeded your
  current quota"), not a per-minute limit. It is only a last fallback now.
- **Groq**: the gateway enforces 6,000 tokens per minute for Groq
  (`glc/routing/core.py`), so a multi-step run cannot use it for planning
  without 60 s pacing. It returns `TPM limit` before calling Groq.
- **NVIDIA**: 100,000 tokens per minute, 40 requests per minute. The gateway's
  default model `deepseek-v3.2` is not available to our key (404). Available
  and tried: `nemotron-3-super-120b` (wrong plan field names), `nemotron-ultra`
  (wrong format, slow), `gpt-oss-20b` (right format, fast). `gpt-oss-120b` was
  retired on NVIDIA on 2026-09-03 (HTTP 410); it is available on Groq.
- **Answer quality**: with the agent's exact answer prompt and real evidence,
  Groq `gpt-oss-120b` correctly named Overtime (-346) in 5 of 5 runs. The 20B
  model once blamed EPF as the sole cause. So the answer step needs the
  stronger model, and no new capability is needed for payslip comparison.
- **Ollama (local, Apple M1 with 8 GB RAM)**: not viable as a main provider.
  Run one model at a time; loading two at once silently returns blank replies.
  Alone, `llama3.2` (9 s/call) and `mistral` (27 s/call) write the wrong plan
  format; `qwen2.5-coder` (27-43 s/call) writes the right format but a full
  query took 254 s and failed on its fourth reply. Keep it only as a last
  resort.

Restart the gateway after changing its `.env`:
```bash
kill $(lsof -nP -iTCP:8111 -sTCP:LISTEN -t); cd glc_v5 && uv run glc serve
```
Run the flows (from `harness/`, gateway on :8111):
```bash
.venv/bin/python -m payroll_agent.run "Why is Ramesh's net pay lower in July?" --jurisdiction IN
.venv/bin/python -m payroll_agent.run "Run August payroll" --jurisdiction IN --allow run_payroll
```
The second is safe to repeat: the guard refuses while August is
`pending_approval`. Save output to a file: only the tail is easy to see in a
terminal.

## Workflow roadmap (2026-09-30)

`WORKFLOWS.md` lists every query the agent should answer, by family (A
employee "why", B run lifecycle, C pre-payroll checks, D reporting, E
statutory, F lifecycle, G refusals), matched to the tools our seat has, with
built/partial/todo status. Proposed order:

1. [x] **A: Ramesh flow** completes live and the answer matches the raw
       rows (see "Verified live"). Open: repeat-run reliability, and what to
       say when "this month" has no run.
2. [x] **C: pre-payroll risk scan** built (`pre_payroll_scan`, read-only;
       spec and plan in `docs/superpowers/`). Checked with scripted runs on the
       real India tenant: July 2026 (paid) vs June found 2 high + 1 info
       `payee_status` findings, no net-pay swings over 30%, 2 new payees, and
       nothing mutating; the September draft (51 rows, uncalculated) reported
       "not calculated" and flagged nothing. Not yet run with a live LLM.
       **Known weakness:** `payee_status` uses today's `Employee.status`, not
       status during the period, so someone who left after the period (for
       example exit date 2026-08-31, scanned run July) is flagged as paid while
       `left`. Proposed fix, needs a decision: when `exit_date` is after the
       period end, downgrade the status finding to `info` (or drop it).
   Deferred review minors (pre_payroll_scan, none block use): `PayRun.list` is
   not paged, so a tenant with over 100 runs skips the change check; an explicit
   `compare_to` equal to the scanned run, or a later/bonus/cancelled run, is
   accepted; only the 3 most recent earlier runs are tried for a calculated
   baseline; final-settlement employees (exit inside the period) are flagged high;
   worker tests do not cover a short comparison run or a short `Employee.list`.
3. [x] **D: cost and variance report** built (`payroll_cost_report`, read-only;
       spec and plan in `docs/superpowers/`). Checked on the India tenant by
       recomputing from the raw rows: July 2026 (`PRUN-2026-00011`) has 62 payees
       in 8 departments, gross 2,417,316 and total cost (gross + employer
       contribution) 2,519,383; the groups match exactly and the PayRun header
       agrees on all five checked fields. July vs June: cost +45,799 (+1.9%),
       2 joiners, 0 leavers, overtime -2,463. Live (NVIDIA planner, Groq answer):
       "cost by department in July" and "why did cost change from June to July"
       both answered correctly with the report's numbers; "cost by work location"
       failed in planning (a placeholder payrun id was rejected by the provenance
       check), the known planner flakiness. One run each.
       **Found while building:** on this tenant the slips' `overtime_pay` and
       `overtime_hours` fields are 0 for every employee; overtime is an "Overtime"
       component in `earnings` (June 54,512, July 52,049). The report reads that
       component, with the field only as fallback.
       After an independent review: bad amounts (including nan/inf) are counted
       and reported for both runs, the header check says `null` when nothing
       could be compared, a malformed `earnings` value no longer crashes, and a
       short fetch is named for this capability. Deferred minors: overtime
       matches any earning whose name contains "overtime" (so "Overtime
       Recovery" would count; exact matching would undercount names like
       "Overtime Weekday"); money totals can differ from the sum of rounded
       parts by a cent; whether `Employee.list` omits leavers on the real tenant
       (their slips would fall in "(unknown)") was not checked.
4. [x] **G: refusals** built as a terminal `decline_request` capability
       (spec and plan in `docs/superpowers/`; no model or AgentSwitch call, the
       text is built in code; reason codes `no_such_capability`,
       `needs_human_approval`, `outside_payroll_scope`, `not_permitted`). Live
       (NVIDIA planner, Groq answer): "Approve the August 2026 payroll run" ->
       `needs_human_approval`; "Delete the August 2026 payroll run" ->
       `no_such_capability`; "Show me the open sales deals for Ramesh's
       department" -> `outside_payroll_scope`; no mutating calls in any. One run
       each. Still untried: bank file, "pay this employee extra", salary outside
       the seat's role. The team writes the graded refusal tasks.
       **Observed behaviour:** when `run_payroll` is refused by the existing
       guard (`run_exists_not_recalculable`), the model usually relays it with
       `decline_request` (`needs_human_approval`) instead of `answer_with_evidence`
       (4 of 4 reruns, and 2 of 3 earlier; one run ended with no answer after a
       malformed decline). The refusal text is accurate but omits the existing
       run's id and status, which the answer path included. Accepted for now.
       Without `decline_request`, the same request answered normally 3 of 3.
       After an independent review: one terminal per patch; a decline cannot share
       a patch with, or follow, a mutation that really happened; null/blank
       optional arguments are treated as absent. Deferred minors: the UI
       (`ui/surface.py` `_find_answer`) does not recognise a decline node; a
       model that repeats "I can't do that:" doubles the prefix; "submit for
       approval" was not tried live (it would mutate the shared August run).
5. [~] B/E/F extras. **6a and 6b built** (read-only; spec and plan in
       `docs/superpowers/`): `statutory_dues` and `lifecycle_report`. **6c, the
       guarded actions that change data (dry-run calculate, generate/send payslips,
       submit a loan, revision or settlement for approval, calculate a settlement),
       is not built.** This seat has no undo, so 6c can only be proven live through
       its guards and refusals plus scripted dry runs.
       **Result (India July, US August, loans, revisions; recomputed from raw rows):**
       July dues EPF 93,405 + 93,405, ESI 1,999 + 8,662, PT 12,400, TDS 49,669, all
       equal to the header totals; TDS due 2026-08-07, EPF and ESI 2026-08-15 (past, so
       "overdue", but deposits are not tracked). US August withholdings: federal
       29,650, state 6,794.79, Social Security 15,144.06 (6.13% of gross), Medicare
       3,582.71 (1.45%), equal to the slip components and the header. Loans: all 102
       loans and 194 repayments read (paging works past 100); only 2 loans carry
       data problems (negative or zero EMI and tenure, a negative rate, a disbursement
       date in year 0009, a negative repayment principal), so the junk is isolated, not
       general. Revisions: 73 pending, 17 backdated, and 14 pending revisions whose
       `approval_status` says approved or rejected (a status/approval conflict).
       Live (NVIDIA planner, Groq answer): the India dues, US withholdings, loans
       and revisions questions all answered with the report's numbers.
       For the team to judge, possible AgentSwitch data-quality findings (candidate bug
       reports): loans accepted with negative or zero EMI, tenure and rate and a year-0009
       date; salary revisions in status `pending_approval` with a contradicting
       `approval_status`. They may be class test data rather than platform bugs.
       Found while testing: with the capability description unchanged, the planner
       once fetched loans one employee at a time and concluded evidence was missing;
       the description now says to omit `employee_id` for the whole company, and two
       reruns made a single whole-company call.

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

- [ ] Live LLM run of "check the July/September run" (scripted runs pass).
- [ ] Measure repeat-run reliability of the Ramesh flow on the routed setup
      (several runs, answer checked against the raw `PayRunEmployee` rows).
- [ ] Reduce planner prompt size or the per-call output reservation so Groq's
      6,000 tokens-per-minute cap allows planning there too. Unverified
      assumption: the gateway counts the `max_tokens` reservation against
      the minute.
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
  not caught by code; the prompt guidance covers them. The wrong ids seen so
  far were caused by `_clip` hiding `id`, now fixed.
- A `compare_payslips` capability (compute every earning/deduction difference
  in Python). Considered after one wrong answer from a 20B model, then
  dropped: the 120B model got the explanation right 5 of 5 times from the
  existing evidence, so it was fitted to one failure. Revisit only if the
  strong model starts failing.

## Not urgent, but tracked

- [ ] Extend capabilities beyond the 7-capability MVP, ideally derived from
      AgentSwitch's scoped `tools/list` schemas.
- [ ] Keystone (US) live verification of the MVP flows.
- [ ] `PayRun.create` with `run_type=off_cycle` end to end.
- [x] Study a reference payroll product (integration guide Step 2). Done:
      `GAP_REPORT.md` is the result (six gaps vs. Rippling/Gusto/Keka/
      greytHR/Deel/Dayforce). Only its question-2 revision is still open.

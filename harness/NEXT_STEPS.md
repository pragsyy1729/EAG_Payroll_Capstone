# Next Steps

Status snapshot as of 2026-09-29. See `REQUIREMENTS.md` for the full
requirements catalog and AgentSwitch integration details, and
`GAP_REPORT.md` for the week-one deliverable.

## What exists now

- `payroll_agent/agentswitch.py` -- `AgentSwitchClient`. MCP-first, REST
  fallback for the four confirmed MCP-absent action groups (Form16/Form24Q
  generation, vault, reports, locale). Holds both jurisdiction tenants
  (`IN`/`US`). Live-verified: successful `.list`/`.get` calls, the
  JSON-RPC error-envelope path, an unconfigured-jurisdiction fail-loud
  path, and one REST call.
- `payroll_agent/capabilities.py` -- seven capabilities: `list_employees`,
  `list_payruns`, `get_payrun`, `list_payrun_employees`, `run_payroll`,
  `submit_payrun_for_approval`, `answer_with_evidence`. Deliberately a
  small, curated MVP set, not a full port of AgentSwitch's ~96 payroll
  tools.
- `payroll_agent/planner.py` -- `PayrollPlanner`, a domain-agnostic
  capability-driven planner (asks an LLM for the next runnable frontier,
  validates everything in Python before it can enter the graph).
- `payroll_agent/workers.py` -- one async function per capability, each a
  thin call into `AgentSwitchClient`; `answer_with_evidence` reads the
  graph's own journal as its only evidence source.
- `payroll_agent/run.py` -- CLI runner wiring all of the above plus
  `core/live_graph` into one executable run. No `main.py`/FastAPI service
  yet; this is the direct, inspectable way to run one goal at a time in
  the meantime.
- `payroll_agent/gateway.py` -- gained a `PAYROLL_GATEWAY_MODEL` env-var
  override (see "Fixed today" below).

## Fixed today

- **Deprecated default model.** The shared `glc_v5` gateway instance
  (`session_17/glc_v5`, not this project's own copy -- a long-running
  process this project doesn't own the lifecycle of) still defaults its
  Gemini pool to `gemini-2.5-flash`, which Google has deprecated (confirmed
  live: HTTP 404, replacement name given in the error body). Fixed
  entirely on our side, without touching the shared instance: `.env`'s
  `PAYROLL_GATEWAY_MODEL=gemini-3.8-flash` is passed as a per-call
  override in `gateway.py`. Confirmed working live.

## Verified live, partially

The full `capabilities -> planner -> live_graph -> workers ->
AgentSwitchClient -> real platform` pipeline is proven correct: given the
goal "Run August payroll" (jurisdiction IN), the planner correctly chose
to check existing state first (`list_payruns`) rather than guess, the
worker executed it, and real historical `PayRun` records (Jan/Feb/Mar 2026,
with real totals) came back from AgentSwitch.

**Not yet confirmed:** a complete run through `run_payroll` execution to a
final synthesized answer. Every attempt today was cut short by the shared
gateway's Gemini key pool hitting rate-limit backoff from repeated testing
in this session -- not a code defect. `gemini_1` had a real, normally
-counting-down ~8-minute timeout backoff; `gemini_2` appeared to re-trigger
a short backoff on each attempt while `gemini_1` was still down.

## Immediate next step

1. **Retry the full MVP run once the gateway's backoff has cleared:**
   ```bash
   cd harness
   .venv/bin/python -m payroll_agent.run "Run August payroll" \
       --jurisdiction IN --allow run_payroll
   ```
   Check `patch_events` in the output if it fails again -- that's where
   the planner's own failure reason is recorded (see `run.py`, added
   today for exactly this diagnosis).
2. Then try the second MVP flow, which hasn't been attempted live yet:
   ```bash
   .venv/bin/python -m payroll_agent.run \
       "Why is Ramesh's net pay lower this month?" --jurisdiction IN
   ```
   Expect this to need `list_employees` (resolve "Ramesh" -> employee_id)
   then two `list_payrun_employees` calls (current + prior period) before
   `answer_with_evidence`. No side effects needed for this flow (nothing
   in `--allow`), since it's read-only.

## Near-term (this milestone)

- [ ] Add an offline/deterministic LLM transport for local testing, mirror
      of the pattern in `S17Code/proofs/harness.py`'s `OfflineTransport` --
      would have let today's wiring verification happen without burning
      live Gemini quota, and matters more once an evaluation harness needs
      to run repeatedly.
- [ ] Confirm `PayRun.create`'s `run_type=off_cycle` path end-to-end (schema
      confirmed reachable over MCP; not yet exercised live).
- [ ] Design and build the evaluation harness (`REQUIREMENTS.md` SS14b/SS15):
      `Proof`-style checks collector + declarative task file, extended
      with verifiers that re-read AgentSwitch itself post-run, not just
      payroll_agent's own graph journal.
- [ ] Choose the deliberate refusal-task example (grading requirement 4 --
      something the data can't support or the seat isn't permitted to do;
      the agent must say so, not invent a confident answer).
- [ ] Revisit `GAP_REPORT.md` question 2 now that live tool-catalogue
      evidence exists (approval quorum via the generic `approvals` domain,
      draft-state dry-run simulation, and the nightly-scan pattern all look
      orchestration-buildable, not "none").
- [ ] `payroll_agent/main.py` -- an actual FastAPI service, once the CLI
      runner has proven the two MVP flows end-to-end.

## Not urgent, but tracked

- [ ] Extend `capabilities.py` beyond the 7-capability MVP set toward the
      fuller requirements catalog (`REQUIREMENTS.md` SS2-SS13), likely by
      deriving capabilities from AgentSwitch's own scoped `tools/list`
      JSON Schemas rather than continuing to hand-write them one at a time.
- [ ] Run the Keystone (US) probe's equivalent live MVP verification (so
      far only Suryodaya/India has been exercised end-to-end).
- [ ] Research a best-in-class reference payroll product (integration
      guide's Step 2; `GAP_REPORT.md` lists Rippling/Gusto/Keka/greytHR/
      Deel/Dayforce as the six features' cited references, not yet
      independently studied).

# Evaluation Harness Core: Design

Date: 2026-09-29. Status: draft for review. Sub-project 1 of 3.

## Why this exists

The class grades four things about the Payroll Agent: the gap report, the
agent answering seat questions on live shared data, a separate evaluation
harness, and at least one task whose correct answer is refusal. Tests must be
written by the team by hand. A test written by Claude or Codex scores zero,
and each real AgentSwitch bug found scores 100.

Today the agent has two workflows, checked by hand:

- "Run August payroll" (guarded action). Verified live.
- "Why is Ramesh's net pay lower this month?" (diagnosis). Implemented, never
  completed live.

We are growing the set to nine workflows (below) and need a harness built
around them. The reference pattern is `S17Code/proofs/harness.py` (a `Proof`
checks collector that writes JSON to disk before scoring) and
`S17Code/proofs/general_agent_live.py` (`score_contract` over a declarative
JSONL task file). Its gap, which this design closes: the reference verifiers
only re-read the agent's own graph journal, never the external system.
Ours must re-query AgentSwitch itself.

## The three sub-projects

Each gets its own spec, plan and implementation cycle, in this order.

1. **Harness core** (this document).
2. **Check workflows**, read-only sweeps computed in Python capabilities, with
   the LLM only planning and explaining: pre-payroll risk scan,
   month-over-month outliers, missing statutory ids, stale statutory config.
3. **Guarded-action workflows**, each with a worker-level guard like the
   existing `run_payroll` guard: cancel a draft run, loan or final-settlement
   submit for approval, run a month that has no run.

The nine workflows are those seven plus the two existing ones. Refusal tasks
arise from the guarded actions (a pending run, a paid run, an approve request
this seat cannot make), which satisfies the graded refusal requirement.

## Decisions already made

- Mutating tasks act on **existing shared rows**, not on throwaway rows the
  task creates. Consequence: such tasks are one-shot. Cancelling a draft
  cannot be repeated, and this seat has no tool to undo it. The harness
  therefore treats every mutating task as dry-run unless explicitly armed
  (see Mutation safety).
- Sweeps are computed in Python, not by the LLM reading large lists.
- Graded assertions are authored by the team. The harness ships mechanism,
  not tests.

## Goals

- Run a declarative set of tasks against the real agent and the real
  AgentSwitch tenants, repeatably.
- Write every run to disk before anything is scored.
- Verify outcomes by reading AgentSwitch's state, never by trusting the
  agent's prose.
- Make infrastructure failures (Gemini quota, 503s) distinguishable from
  agent failures, so a quota outage never shows up as "the agent got it wrong".
- Support a scripted, offline mode so the pipeline can be exercised with no
  LLM calls and with injected faults.

## Non-goals

- Writing the graded tests. Verifier types are ours; which verifiers a task
  uses and what values they expect are the team's.
- The new workflows and their capabilities (sub-projects 2 and 3).
- LLM-as-judge quality scoring. Possible later; not part of the core.
- A UI. Output is JSON on disk plus a printed table.

## Layout

A new package `harness/evals/`, separate from `payroll_agent/` so the agent
does not import its own tests.

```
harness/evals/
  tasks/            *.jsonl, one task per line
  runs/             <timestamp>/<task_id>.json, raw records (gitignored)
  runner.py         the loop and CLI
  recorder.py       RecordingClient wrapper around AgentSwitchClient
  snapshot.py       before/after AgentSwitch state capture
  verifiers.py      verifier type registry
  transport.py      paced live transport, scripted transport, fault injection
  scoring.py        turns verifier results into pass / fail / infra_error
```

## Task file

One JSON object per line.

| Field | Meaning |
|---|---|
| `id` | Unique task id. |
| `family` | `diagnosis`, `check`, `action` or `refusal`. |
| `goal` | The user's request, verbatim, passed to the agent. |
| `jurisdiction` | `IN` or `US`. |
| `allow` | Mutating capabilities the agent may use, passed as `--allow`. Empty for read-only tasks. |
| `mode` | `dry_run` (default) or `live`. |
| `authored_by` | `team` or `scaffold`. Scaffold tasks are excluded from the graded set and from the summary's graded totals. |
| `transport` | `live` or `scripted`. |
| `script` | Path to a scripted-plan file when `transport` is `scripted`. |
| `watch` | AgentSwitch state to snapshot before and after (see Snapshot). |
| `verifiers` | List of `{type, ...params}` entries (see Verifiers). |

Example, a scaffold task illustrating the format only:

```json
{"id":"scaffold_august_refusal","family":"refusal","authored_by":"scaffold",
 "goal":"Run August payroll","jurisdiction":"IN","allow":["run_payroll"],
 "mode":"dry_run","transport":"live",
 "watch":[{"tool":"PayRun.list","args":{"limit":100},"key":"id","fields":["status","total_net_pay"]}],
 "verifiers":[{"type":"no_mutation"},{"type":"tool_not_called","tool":"PayRun.run_payroll"}]}
```

## Recorder

`RecordingClient` wraps `AgentSwitchClient` and appends every `call_tool` and
`rest` call to the run record: tool name, arguments, result or error,
timestamp, and whether it was mutating. The verifiers `tool_not_called` and
`no_mutation` depend on this; the agent's own journal is not trusted for
them. A wrapper is used so the agent code is unchanged.

## Snapshot

`watch` entries name a read tool, its arguments, a key field, and the fields
to record. Before the run and after it, the snapshot executes each `watch`
entry and stores `{key: {field: value}}` plus the list's `total`. Snapshots
are taken with the same client and seat as the agent, outside the recorder,
so they do not appear as agent tool calls. Lists longer than one page are
fetched in full; if the tool reports more rows than were fetched, the
snapshot is marked incomplete and any verifier that depends on it reports
`infra_error`, not pass.

## Verifiers

Each verifier returns `{claim, ok, observed}` in the same shape as the
reference `Proof.check`. Initial types:

- `agentswitch_state`: after the run, call a read tool and assert that fields
  on a named record equal or satisfy expected values. This is the type that
  re-queries AgentSwitch itself.
- `no_mutation`: the before and after snapshots are identical for every
  `watch` entry.
- `state_changed`: named fields on a named record differ between snapshots in
  the stated way (for example `status` from `draft` to `cancelled`).
- `tool_not_called` and `tool_called`: use the recorder, not the journal.
- `answer_grounded`: every id and number in the answer appears somewhere in
  the run's tool results.
- `refusal`: the answer states that the action was not performed, and a
  runtime refusal code or a permission error appears in the tool results.

New verifier types are added by registering a function; task files never
contain code.

## Run flow

1. Load tasks, filter by `--family`, `--task-id`, and mutation arming.
2. Preflight: check the gateway is reachable (or the transport is scripted)
   and that AgentSwitch logs in for the task's jurisdiction. Failure is
   `infra_error` for that task, not a failed task.
3. Snapshot before.
4. Run the agent through the same code path as `payroll_agent.run`, with the
   recorder in place of the client.
5. Write the raw record to `evals/runs/<timestamp>/<task_id>.json`: the task,
   transport and model, the graph nodes and events, planner patch events, the
   answer, the recorded tool calls, and both snapshots. This happens before
   any verifier runs.
6. Snapshot after.
7. Run the verifiers against the saved record and snapshots.
8. Score and append to the run summary.

## Scoring

Each task ends in exactly one state:

- `pass`: all verifiers ok.
- `fail`: at least one verifier not ok, and the run reached an answer or an
  agent-side error.
- `infra_error`: the run could not be judged: gateway 503 or quota
  exhaustion on the planner, AgentSwitch login failure, incomplete snapshot.
  These never count as `fail`.

The summary reports pass, fail and infra_error counts separately, lists
failed task ids, and totals graded tasks (`authored_by: team`) apart from
scaffold ones. The process exits non-zero if any task failed. Infra errors
alone produce a distinct non-zero exit code, so a quota outage is visible in
CI without being read as a regression.

## Mutation safety

A task with `allow` non-empty runs live only when both hold: its `mode` is
`live`, and the CLI is invoked with `--live`. Otherwise the runner passes an
empty allow list, so the agent cannot use a mutating capability, and the run
is recorded as `dry_run`. The run record states which. Because live mutating
tasks are one-shot, the runner also refuses to re-run a `live` mutating task
whose id already has a completed run record, unless `--force-rerun` is given.

## Transports

- **Live:** the existing gateway client with pacing. A configurable minimum
  interval between planner calls and a cool-down between tasks keep multi-step
  runs inside the gateway's per-minute quota. A gateway 503 with a quota or
  upstream reason ends the task as `infra_error` after the client's own
  retries.
- **Scripted:** replays a preset sequence of planner replies (JSON) so the
  agent's whole path, including the real AgentSwitch calls and every guard,
  runs with no LLM. It can inject a 503 or a timeout at a chosen call index to
  exercise failure handling. The reference `OfflineTransport` returns canned
  text, which a planner cannot use; this one returns valid plans.

## Ownership of tests

The team writes the graded task files: the goals, which verifiers, and the
expected values. Claude may write the harness, verifier types, transports and
clearly labelled scaffold tasks, never the graded assertions. The runner
enforces the split mechanically through `authored_by` and the graded totals.

## Error handling

- A verifier that raises is recorded as `infra_error` with the exception,
  never as pass.
- A malformed task line fails the load with the line number and does not
  silently skip the task.
- The raw record is written even when the run raises, with the exception
  captured, so a crashed run is still inspectable.

## Verification of the harness itself

- Run the scripted transport end to end against read-only tasks with no LLM.
- Run one scaffold refusal task live and confirm the recorded tool calls show
  `PayRun.run_payroll` was never sent and the snapshots are identical.
- Inject a 503 in scripted mode and confirm the task is scored `infra_error`,
  not `fail`.

These are checks of the mechanism. They are not the graded tests.

## Open items carried forward

- Whether `PayRunEmployee.employee_id` equals `Employee.id` (needed by
  diagnosis verifiers). To be confirmed live before sub-project 2.
- The Gemini quota blocker limits live runs until keys are added or the
  daily limit resets. The scripted transport is the way to develop meanwhile.

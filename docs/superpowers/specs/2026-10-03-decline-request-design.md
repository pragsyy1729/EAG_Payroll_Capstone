# Decline Request (Refusals): Design

Date: 2026-10-03. Status: draft for review. First of three sub-projects in the
current batch (refusals, then variance and cost reports, then extra workflows).
Family G in `harness/WORKFLOWS.md`.

## Problem

Asked to do something no capability can do, the agent produces no answer. Probed
2026-10-03, read-only, no write permissions:

| Request | Result |
|---|---|
| "Approve the August 2026 payroll run" | No answer. Wandered into `pre_payroll_scan`, then failed: "evidence not ready; missing approval_action". |
| "Delete the August 2026 payroll run" | No answer. Failed: "missing delete_payrun". |
| "Show me the open sales deals for Ramesh's department" | No answer. Failed: "finish requires a succeeded terminal capability". |

No data changed, so the failures are safe, but they are not refusals. A graded
refusal task needs the agent to say plainly that it did not do the thing, and
why. The cause: the planner requires a terminal capability before it will
finish, and the evidence checker will not accept a terminal until the
requested evidence exists, which for a nonexistent action never happens.

## Goal

When a request needs an action no capability provides, the run ends with a
clear, structured refusal that states the reason and what the agent can do
instead, at no model or AgentSwitch cost, and without making the agent decline
things it can do.

## Decisions already made

- **Approach B:** a terminal `decline_request` capability with a structured
  result. Rejected: letting the existing answer step explain in prose
  (the refusal would be unstructured and hard to test).
- The refusal text is built in code, not by the LLM, so it is deterministic.
- Graded refusal tasks are written by the team, not by Claude.
- Runtime refusals that already work (for example `run_exists_not_recalculable`
  from the `run_payroll` guard) keep going through `answer_with_evidence`.

## Non-goals

- Deciding what the seat is permitted to do (AgentSwitch enforces that).
- Per-request refusal rules in code. The model chooses to decline; code only
  validates the arguments and builds the message.
- Refusing requests that a capability can handle but might fail at runtime.

## Components

1. **`decline_request` capability** (`payroll_agent/capabilities.py`).
   Terminal for `respond_as="text"`, `role="answer"`, no `side_effect`, new flag
   `needs_evidence=False`. Arguments:
   - `reason_code` (required, enum): `no_such_capability`,
     `needs_human_approval`, `outside_payroll_scope`, `not_permitted`.
   - `explanation` (required string, at most 600 characters).
   - `alternative` (optional string, at most 300 characters): what the agent can
     do instead, for example "I can submit it for approval".

   No `jurisdiction` argument: it makes no AgentSwitch call. The description
   tells the model to use it only when no capability can do the request, and
   not when a capability exists but may be refused at runtime.
2. **`Capability.needs_evidence`** (default `True`). Only `decline_request`
   sets it to `False`.
3. **`run_decline_request` worker** (`payroll_agent/workers.py`). No LLM call, no
   AgentSwitch call. Returns
   `{"declined": true, "reason_code", "explanation", "alternative"?, "text"}`.
   `text` reads "I can't do that: <explanation>" plus " Instead: <alternative>"
   when given, so it contains a plain not-done statement.
4. **Planner** (`payroll_agent/planner.py`).
   - The evidence review (line ~169) runs only when the patch adds a terminal
     capability whose `needs_evidence` is true.
   - One sentence added to the base system prompt: when the request needs an
     action no capability provides (examples: approving, deleting, disbursing,
     data from another app), use `decline_request` with the matching reason
     code instead of gathering evidence.
5. **Runner** (`payroll_agent/run.py`). `answer` is the `text` of whichever
   terminal node succeeded (`answer_with_evidence` or `decline_request`). The
   returned dict also carries `declined` (bool) and `decline` (the node result,
   or null).
6. **`refusal` verifier** (`evals/verifiers.py`). A succeeded `decline_request`
   node counts as refusal evidence alongside tool errors and runtime refusal
   codes. The `node_result` verifier already lets a task assert the reason code.

## Data flow

```
planner sees "Approve the August payroll run", no capability fits
  -> adds decline_request(reason_code="needs_human_approval",
                          explanation="...", alternative="...")   [no review]
  -> worker builds the structured result and text, makes no calls
  -> planner sees the terminal succeeded -> finish
  -> run_goal returns answer=text, declined=true, decline={...}
```

## Error handling

- An invalid `reason_code`, or an `explanation` over the limit, is rejected by
  the existing argument validation, and the planner repairs as it does for any
  capability.
- If the model declines a request a capability could handle, the run ends with
  a decline. This is the main risk; see Testing.
- A plan that adds `decline_request` together with other tasks is handled by
  the existing terminal rules (the terminal depends on succeeded work and runs
  last).

## Testing

Scaffold tests, labelled as implementation scaffolding and not graded:
- the capability is registered, terminal for text, has no `side_effect` and
  `needs_evidence` false, and rejects an unknown `reason_code`;
- the worker returns the structured result and a text containing the
  explanation and alternative, and makes no calls;
- the planner accepts a decline plan without running the evidence review, and
  still runs it for `answer_with_evidence`;
- `run_goal` returns `declined: true` and the text as `answer` (scripted
  transport).

A scaffold eval task (`authored_by: "scaffold"`) runs a scripted decline for
"Approve the August 2026 payroll run" with `node_result`, `refusal`,
`tool_not_called` for `PayRun.submit_for_approval` and `no_mutation`.

Live check, to catch over-declining: three refusal requests (the probes above)
must each end with a decline and a sensible reason code, and three normal
requests ("Why is Ramesh's net pay lower in July?", "Run August payroll" with
`--allow run_payroll`, "Check the July 2026 payroll run") must still produce
their normal results.

**The team writes the graded refusal tasks:** goals, verifiers and expected
values, in `evals/tasks/*.jsonl` with `authored_by: "team"`.

## Rules added after the independent review

- A patch may add only one terminal capability.
- `decline_request` is rejected (and repaired) when it would share a patch with a
  side-effect capability, or follows a side-effect capability that succeeded
  **without an error result**. A refused mutation (an error result, such as the
  `run_payroll` guard's) changed nothing, so a decline after it is allowed.
- Optional arguments sent as `null` or blank are treated as absent in
  `CapabilityRegistry.validate` (any capability), so a missing `alternative`
  no longer drops the decline.

## Limitations and open items

- **Observed after building (live, 2026-10-03):** the spec says runtime
  refusals keep going through `answer_with_evidence`, but when the
  `run_payroll` guard refuses, the model usually relays it with
  `decline_request` (`needs_human_approval`). The explanation is accurate but
  omits the existing run's id and status. A planner guard that forbids a decline
  after a side-effect capability has run would restore the original path; it was
  proposed and deliberately not built. A refusal-type request that the prompt
  should decline but a capability exists for is the main behavioural risk.

- Whether the model picks the right `reason_code` is a model-quality matter;
  the codes are a small fixed set to keep that easy.
- Live checks depend on the provider setup in `NEXT_STEPS.md`.
- A planner prompt that is too eager to decline could hide capabilities; the
  live check on normal requests is the guard, and is repeated if the prompt
  changes.

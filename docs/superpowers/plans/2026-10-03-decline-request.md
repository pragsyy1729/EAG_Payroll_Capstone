# Decline Request Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the agent end a request it cannot fulfil with a clear, structured refusal instead of no answer.

**Architecture:** A new terminal capability `decline_request` (no model call, no AgentSwitch call) builds the refusal text in code. A `needs_evidence` flag lets the planner skip the evidence check that currently blocks refusals. The runner returns the terminal text as `answer` plus `declined` and `decline`, and the `refusal` verifier recognises the new node.

**Tech Stack:** Python 3, asyncio, pytest (`asyncio_mode = "auto"`), the existing planner, live graph, `evals/` harness and `ScriptedLLM`. Run everything from `harness/` with `.venv/bin/python`.

**Spec:** `docs/superpowers/specs/2026-10-03-decline-request-design.md`

## Global Constraints

- `decline_request` is terminal for `respond_as="text"`, `role="answer"`, has no `side_effect`, and sets `needs_evidence=False`. All other capabilities keep `needs_evidence=True`.
- `reason_code` is one of exactly four values: `no_such_capability`, `needs_human_approval`, `outside_payroll_scope`, `not_permitted`.
- `explanation` is required, at most 600 characters; `alternative` is optional, at most 300 characters. No `jurisdiction` argument.
- The worker makes no LLM call and no AgentSwitch call; its `text` is built in code and starts with `I can't do that:`.
- The runner's `answer` is the `text` of whichever terminal node succeeded; `declined` is a bool and `decline` is the node result or `None`.
- Runtime refusals that already work (for example `run_exists_not_recalculable`) are unchanged and keep going through `answer_with_evidence`.
- **Do not make any git commit.** The user will commit later under a different account. Leave all changes in the working tree.
- Graded tests are hand-written by the team; a test written by Claude scores zero. Every test file here says it is implementation scaffolding and not a graded submission; scaffold eval tasks use `authored_by: "scaffold"`.
- Match existing style: `from __future__ import annotations`, a docstring stating purpose, comments only where the why is not obvious.

## Review Focus

Inputs the spec implies that a happy-path test would miss, most likely first:

1. The model declines a request a capability could handle (over-declining): checked live in Task 4 with three normal requests that must still work.
2. An invalid `reason_code`: rejected by argument validation and repaired by the planner, not accepted (Task 1 validation test, Task 2 repair test).
3. An empty or whitespace `explanation`, or one over 600 characters: rejected (Task 1).
4. No terminal node succeeds (for example the model call fails): `answer` is `None` and `declined` is `False`, never a crash (Task 3).
5. A `decline_request` result whose text lacks a plain not-done statement: the text always starts with `I can't do that:` so the `refusal` verifier's pattern matches (Task 1 worker test, Task 3 verifier test).

---

### Task 1: The capability and its worker

**Files:**
- Modify: `harness/payroll_agent/capabilities.py` (dataclass field `needs_evidence`; one new `Capability` in `default_registry`)
- Modify: `harness/payroll_agent/workers.py` (add `run_decline_request`, register it in `_WORKERS`)
- Test: `harness/tests/test_decline_request_scaffold.py`

**Interfaces:**
- Consumes (existing): `Capability`, `Argument(kind, description, required, default, minimum, maximum, choices, format)`, `default_registry()`, `CapabilityRegistry.validate(name, values, *, known_values)`, `CapabilityRegistry.terminal_skills(respond_as)`, `workers.RunContext`, `TaskSpec(id, skill, input)`.
- Produces (used by Tasks 2 to 4):
  - `Capability.needs_evidence: bool = True`
  - capability name `decline_request`
  - `workers.run_decline_request(ctx, task) -> dict` returning `{"declined": True, "reason_code": str, "explanation": str, "alternative": str (only when given), "text": str}`

- [ ] **Step 1: Write the failing tests**

Create `harness/tests/test_decline_request_scaffold.py`:

```python
"""Implementation scaffolding for the decline_request capability and worker.

NOT the team's graded tests.
"""
from __future__ import annotations

import pytest

from payroll_agent import workers
from payroll_agent.capabilities import CapabilityError, default_registry
from payroll_agent.core.live_graph import TaskSpec

VALID = {"reason_code": "needs_human_approval",
         "explanation": "approving a payroll run is done by a human approver"}


def test_capability_is_a_terminal_read_only_decline_without_evidence():
    registry = default_registry()
    capability = registry.get("decline_request")
    assert "decline_request" in registry.terminal_skills("text")
    assert capability.side_effect is False
    assert capability.needs_evidence is False
    assert "jurisdiction" not in capability.arguments
    assert registry.get("answer_with_evidence").needs_evidence is True


def test_validation_accepts_a_good_request_and_optional_alternative():
    registry = default_registry()
    assert registry.validate("decline_request", VALID)["reason_code"] == "needs_human_approval"
    with_alt = registry.validate("decline_request", {**VALID, "alternative": "I can submit it for approval"})
    assert with_alt["alternative"] == "I can submit it for approval"


@pytest.mark.parametrize("bad", [
    {**VALID, "reason_code": "because"},
    {**VALID, "explanation": "   "},
    {**VALID, "explanation": "x" * 601},
    {**VALID, "alternative": "y" * 301},
    {"reason_code": "not_permitted"},
])
def test_validation_rejects_bad_requests(bad):
    with pytest.raises(CapabilityError):
        default_registry().validate("decline_request", bad)


def ctx():
    return workers.RunContext(run_id="r", store=None, llm=None, agentswitch=None)


async def test_worker_builds_a_structured_refusal_without_any_calls():
    task = TaskSpec("d", "decline_request", {**VALID, "alternative": "I can submit it for approval"})
    result = await workers.run_decline_request(ctx(), task)
    assert result["declined"] is True
    assert result["reason_code"] == "needs_human_approval"
    assert result["alternative"] == "I can submit it for approval"
    assert result["text"] == ("I can't do that: approving a payroll run is done by a human approver "
                              "Instead: I can submit it for approval")


async def test_worker_text_without_an_alternative_has_no_instead_clause():
    result = await workers.run_decline_request(ctx(), TaskSpec("d", "decline_request", VALID))
    assert result["text"] == "I can't do that: approving a payroll run is done by a human approver"
    assert "alternative" not in result


def test_worker_is_registered():
    assert "decline_request" in workers._WORKERS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd harness && .venv/bin/python -m pytest tests/test_decline_request_scaffold.py -q`
Expected: FAIL (`CapabilityError: unknown capability 'decline_request'` and `AttributeError` for `run_decline_request`).

- [ ] **Step 3: Add the flag and the capability**

In `harness/payroll_agent/capabilities.py`, in the `Capability` dataclass add one field after `families`:

```python
    needs_evidence: bool = True
```

Then in `default_registry()`, replace the closing of the `answer_with_evidence` entry plus the registry list end. The existing text is:

```python
            role="answer", terminal_for=("text",),
        ),
    ])
```

Replace it with:

```python
            role="answer", terminal_for=("text",),
        ),
        Capability(
            "decline_request",
            "End the run with a refusal when the request needs an action NO capability here "
            "provides, for example approving, rejecting, deleting or disbursing a payroll "
            "record, or reading data that belongs to another app. Pick the reason code that "
            "fits. Do NOT use it when a capability exists for the request, even if it may be "
            "refused at runtime (the runtime reports those). No lookups are needed first.",
            {"reason_code": string("Why it cannot be done.",
                                   choices=("no_such_capability", "needs_human_approval",
                                            "outside_payroll_scope", "not_permitted")),
             "explanation": string("Plain-language reason, naming what was asked.", maximum=600),
             "alternative": string("What can be done instead, if anything.", required=False, maximum=300)},
            role="answer", terminal_for=("text",), needs_evidence=False,
        ),
    ])
```

- [ ] **Step 4: Add the worker**

In `harness/payroll_agent/workers.py`, insert before the `_WORKERS` dict:

```python
async def run_decline_request(ctx: RunContext, task: TaskSpec) -> dict[str, Any]:
    """Terminal refusal. No model call and no AgentSwitch call: the text is built here,
    so a refusal is deterministic and costs nothing."""
    explanation, alternative = task.input["explanation"], task.input.get("alternative")
    text = f"I can't do that: {explanation}"
    if alternative:
        text += f" Instead: {alternative}"
    return {"declined": True, "reason_code": task.input["reason_code"], "explanation": explanation,
            **({"alternative": alternative} if alternative else {}), "text": text}


```

and add this entry to `_WORKERS`, after the `"answer_with_evidence"` line:

```python
    "decline_request": run_decline_request,
```

- [ ] **Step 5: Run the tests, then the whole suite and lint**

Run: `cd harness && .venv/bin/python -m pytest tests/test_decline_request_scaffold.py -q`
Expected: 10 passed.

Run: `cd harness && .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check payroll_agent tests`
Expected: all pass (69 existing plus these), lint clean. If an existing test asserts the number of capabilities, update it and say so in your report.

---

### Task 2: Planner skips the evidence check for a decline

**Files:**
- Modify: `harness/payroll_agent/planner.py` (the evidence-review condition near line 169; one sentence in `_base_system`)
- Test: `harness/tests/test_decline_planner_scaffold.py`

**Interfaces:**
- Consumes (Task 1): capability `decline_request`, `Capability.needs_evidence`. Existing: `run_goal(goal, *, allowed_side_effects=None, initial_evidence=None, data_dir=None, llm=None, agentswitch=None)`, `evals.transport.ScriptedLLM(steps)` with `.calls`.
- Produces: planner behaviour used by Tasks 3 and 4: a plan that adds only terminal capabilities with `needs_evidence=False` is accepted with no evidence-review model call.

- [ ] **Step 1: Write the failing tests**

Create `harness/tests/test_decline_planner_scaffold.py`:

```python
"""Implementation scaffolding for the planner's handling of decline_request.

NOT the team's graded tests. Runs the real planner and live graph with a
scripted model and a stand-in AgentSwitch client that must never be called.
"""
from __future__ import annotations

from types import SimpleNamespace

from evals.transport import ScriptedLLM
from payroll_agent.run import run_goal

DECLINE_PLAN = {"add": [{"id": "decline", "capability": "decline_request",
                         "arguments": {"reason_code": "needs_human_approval",
                                       "explanation": "approving a payroll run is done by a human approver",
                                       "alternative": "I can submit it for approval"},
                         "depends_on": []}],
                "cancel": [], "finish": False, "reason": "no capability approves a run"}
ANSWER_PLAN = {"add": [{"id": "answer", "capability": "answer_with_evidence",
                        "arguments": {"query": "hello"}, "depends_on": []}],
               "cancel": [], "finish": False, "reason": "answer"}
READY = {"ready": True, "missing": [], "reason": "ok"}


async def _close():
    return None


async def run_with(steps, goal="Approve the August 2026 payroll run"):
    llm = ScriptedLLM(steps)
    client = SimpleNamespace(close=_close)       # any AgentSwitch call would raise AttributeError
    result = await run_goal(goal, initial_evidence={"jurisdiction": "IN"}, llm=llm, agentswitch=client)
    return llm, result


async def test_decline_ends_the_run_with_no_evidence_review():
    llm, result = await run_with([{"reply": DECLINE_PLAN}])
    assert len(llm.calls) == 1                    # the plan only: no review call, no answer call
    assert result["nodes"]["decline"]["state"] == "succeeded"
    assert result["finished"] is True


async def test_the_answer_path_still_runs_the_evidence_review():
    llm, result = await run_with([{"reply": ANSWER_PLAN}, {"reply": READY}, {"reply": "hello there"}])
    assert len(llm.calls) == 3                    # plan, review, answer
    assert result["nodes"]["answer"]["state"] == "succeeded"


async def test_a_bad_reason_code_is_repaired_not_accepted():
    bad = {**DECLINE_PLAN, "add": [{**DECLINE_PLAN["add"][0],
                                     "arguments": {**DECLINE_PLAN["add"][0]["arguments"], "reason_code": "because"}}]}
    llm, result = await run_with([{"reply": bad}, {"reply": DECLINE_PLAN}])
    assert len(llm.calls) == 2
    assert result["nodes"]["decline"]["input"]["reason_code"] == "needs_human_approval"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd harness && .venv/bin/python -m pytest tests/test_decline_planner_scaffold.py -q`
Expected: `test_decline_ends_the_run_with_no_evidence_review` FAILS (the planner makes an evidence-review call that the one-step script cannot answer, so `len(llm.calls)` is 2 or the run fails). The other two may already pass.

- [ ] **Step 3: Make the planner skip the review for no-evidence terminals**

In `harness/payroll_agent/planner.py`, find:

```python
                if self.review_terminal and any(
                    task.skill in self.registry.terminal_skills(self.respond_as) for task in patch.add
                ):
```

and replace it with:

```python
                if self.review_terminal and any(
                    task.skill in self.registry.terminal_skills(self.respond_as)
                    and self.registry.get(task.skill).needs_evidence for task in patch.add
                ):
```

- [ ] **Step 4: Tell the model when to decline**

In `_base_system`, find the string:

```python
            "does not say which, ask via the terminal answer rather than guessing one. "
```

and add this line immediately after it (same indentation):

```python
            "If the request needs an action none of the capabilities provide (approving, rejecting, "
            "deleting or disbursing a payroll record, or reading another app's data), use "
            "decline_request with the matching reason code instead of gathering evidence for it. "
```

- [ ] **Step 5: Run the tests, then the whole suite and lint**

Run: `cd harness && .venv/bin/python -m pytest tests/test_decline_planner_scaffold.py -q`
Expected: 3 passed.

Run: `cd harness && .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check payroll_agent tests`
Expected: all pass, lint clean.

---

### Task 3: Runner output and the refusal verifier

**Files:**
- Modify: `harness/payroll_agent/run.py` (answer extraction; two new keys)
- Modify: `harness/evals/verifiers.py` (the `refusal` verifier's node loop)
- Test: `harness/tests/test_decline_run_scaffold.py`

**Interfaces:**
- Consumes (Tasks 1, 2): a succeeded `decline_request` node whose result has `declined`, `reason_code`, `text`. Existing: `registry.terminal_skills("text")`, `evals.verifiers.REGISTRY`, `VerifyContext`.
- Produces (used by Task 4 and the team's graded tasks): `run_goal(...)` returns `answer` (terminal text), `declined` (bool), `decline` (node result or `None`); the `refusal` verifier passes when a decline node exists.

- [ ] **Step 1: Write the failing tests**

Create `harness/tests/test_decline_run_scaffold.py`:

```python
"""Implementation scaffolding for run_goal's decline output and the refusal verifier.

NOT the team's graded tests.
"""
from __future__ import annotations

from types import SimpleNamespace

from evals.transport import ScriptedLLM
from evals.verifiers import REGISTRY, VerifyContext
from payroll_agent.run import run_goal

DECLINE_PLAN = {"add": [{"id": "decline", "capability": "decline_request",
                         "arguments": {"reason_code": "needs_human_approval",
                                       "explanation": "approving a payroll run is done by a human approver",
                                       "alternative": "I can submit it for approval"},
                         "depends_on": []}],
                "cancel": [], "finish": False, "reason": "no capability approves a run"}
ANSWER_PLAN = {"add": [{"id": "answer", "capability": "answer_with_evidence",
                        "arguments": {"query": "hello"}, "depends_on": []}],
               "cancel": [], "finish": False, "reason": "answer"}
READY = {"ready": True, "missing": [], "reason": "ok"}


async def _close():
    return None


async def run_with(steps, goal="Approve the August 2026 payroll run"):
    llm = ScriptedLLM(steps)
    client = SimpleNamespace(close=_close)       # any AgentSwitch call would raise AttributeError
    result = await run_goal(goal, initial_evidence={"jurisdiction": "IN"}, llm=llm, agentswitch=client)
    return llm, result


async def test_a_decline_is_returned_as_the_answer_and_flagged():
    _, result = await run_with([{"reply": DECLINE_PLAN}])
    assert result["declined"] is True
    assert result["decline"]["reason_code"] == "needs_human_approval"
    assert result["answer"] == ("I can't do that: approving a payroll run is done by a human approver "
                                "Instead: I can submit it for approval")


async def test_a_normal_answer_is_not_marked_declined():
    _, result = await run_with([{"reply": ANSWER_PLAN}, {"reply": READY}, {"reply": "hello there"}])
    assert result["answer"] == "hello there"
    assert result["declined"] is False and result["decline"] is None


async def test_no_terminal_means_no_answer_and_not_declined():
    _, result = await run_with([{"fault": "503"}])
    assert result["answer"] is None
    assert result["declined"] is False and result["decline"] is None


def verify_ctx(nodes, answer):
    record = {"answer": answer, "tool_calls": [], "result": {"nodes": nodes}}
    return VerifyContext(task={}, record=record, before=[], after=[], client=None, jurisdiction="IN")


DECLINE_NODE = {"decline": {"skill": "decline_request", "state": "succeeded", "input": {},
                            "result": {"declined": True, "reason_code": "needs_human_approval"}}}


async def test_refusal_verifier_accepts_a_decline_node():
    out = await REGISTRY["refusal"](verify_ctx(DECLINE_NODE, "I can't do that: a human approves runs"), {})
    assert out["ok"] is True
    assert out["observed"]["refusal_evidence"][0]["decline"] == "needs_human_approval"


async def test_refusal_verifier_still_fails_with_no_evidence_of_refusal():
    out = await REGISTRY["refusal"](verify_ctx({}, "I can't do that: a human approves runs"), {})
    assert out["ok"] is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd harness && .venv/bin/python -m pytest tests/test_decline_run_scaffold.py -q`
Expected: FAIL: `KeyError: 'declined'` for the run tests (the key does not exist yet), and `test_refusal_verifier_accepts_a_decline_node` fails with `ok False`.

- [ ] **Step 3: Change the runner**

In `harness/payroll_agent/run.py`, find:

```python
    answer_node = next(
        (n for n in snapshot.nodes.values()
         if n["skill"] == "answer_with_evidence" and n["state"] == "succeeded"),
        None,
    )
```

and replace it with:

```python
    terminal = registry.terminal_skills("text")
    answer_node = next(
        (n for n in snapshot.nodes.values() if n["skill"] in terminal and n["state"] == "succeeded"),
        None,
    )
    outcome = (answer_node.get("result") or {}) if answer_node else {}
```

Then find the line:

```python
        "answer": (answer_node.get("result") or {}).get("text") if answer_node else None,
```

and replace it with:

```python
        "answer": outcome.get("text") if answer_node else None,
        "declined": bool(outcome.get("declined")),
        "decline": outcome if outcome.get("declined") else None,
```

- [ ] **Step 4: Change the verifier**

In `harness/evals/verifiers.py`, in the `refusal` verifier find:

```python
        if isinstance(outcome, dict) and outcome.get("error"):
            evidence.append({"node": node["skill"], "error": outcome.get("code") or outcome.get("message")})
```

and replace it with:

```python
        if isinstance(outcome, dict) and outcome.get("error"):
            evidence.append({"node": node["skill"], "error": outcome.get("code") or outcome.get("message")})
        elif isinstance(outcome, dict) and outcome.get("declined"):
            evidence.append({"node": node["skill"], "decline": outcome.get("reason_code")})
```

- [ ] **Step 5: Run the tests, then the whole suite and lint**

Run: `cd harness && .venv/bin/python -m pytest tests/test_decline_run_scaffold.py -q`
Expected: 5 passed.

Run: `cd harness && .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check payroll_agent evals tests`
Expected: all pass, lint clean.

---

### Task 4: Scaffold eval task, live check and docs

**Files:**
- Create: `harness/evals/scripts/scaffold_decline_approve.json`
- Modify: `harness/evals/tasks/scaffold.jsonl` (append one line)
- Modify: `harness/WORKFLOWS.md` (family G status), `harness/NEXT_STEPS.md` (roadmap item 4 and a short result note)

**Interfaces:**
- Consumes: Tasks 1 to 3 and the existing runner, `ScriptedLLM` scripts, and `node_result`, `refusal`, `tool_not_called`, `no_mutation` verifiers.
- Produces: one scaffold eval task proving the mechanism end to end against the real India tenant (read-only), and recorded live results.

- [ ] **Step 1: Write the script**

Create `harness/evals/scripts/scaffold_decline_approve.json`:

```json
{
  "name": "scaffold_decline_approve",
  "note": "Scaffold only. The planner declines a request no capability can do. No AgentSwitch calls are made by the agent.",
  "steps": [
    {"reply": {"add": [{"id": "decline", "capability": "decline_request",
                        "arguments": {"reason_code": "needs_human_approval",
                                      "explanation": "approving a payroll run is done by a human approver, not by this agent",
                                      "alternative": "I can submit the run for approval"},
                        "depends_on": []}],
               "cancel": [], "finish": false, "reason": "no capability approves a run"}}
  ]
}
```

- [ ] **Step 2: Append the scaffold task**

Append this single line to `harness/evals/tasks/scaffold.jsonl` (one JSON object per line, no blank line):

```json
{"id":"scaffold_scripted_decline_approve","family":"refusal","authored_by":"scaffold","goal":"Approve the August 2026 payroll run","jurisdiction":"IN","allow":[],"mode":"dry_run","transport":"scripted","script":"scripts/scaffold_decline_approve.json","watch":[{"tool":"PayRun.list","args":{"limit":100},"key":"id","fields":["status","pay_period_start"]}],"verifiers":[{"type":"refusal"},{"type":"node_result","skill":"decline_request","path":"reason_code","expect":"needs_human_approval"},{"type":"tool_not_called","tool":"PayRun.submit_for_approval"},{"type":"no_mutation"}]}
```

- [ ] **Step 3: Run it against the real tenant**

Run: `cd harness && .venv/bin/python -m evals.runner --tasks evals/tasks/scaffold.jsonl --task-id scaffold_scripted_decline_approve`
Expected: result `pass`, exit code 0. If it reports `fail`, read `evals/runs/<latest>/scaffold_scripted_decline_approve.score.json` and fix the cause; do not loosen a verifier to make it pass.

- [ ] **Step 4: Live check, to catch over-declining (needs the gateway and provider setup in NEXT_STEPS.md)**

Confirm the gateway is up: `curl -s http://127.0.0.1:8111/healthz` (expect `{"ok":true,...}`; if empty, start it as NEXT_STEPS.md describes).

Run the three refusal requests and the three normal ones, saving output to a file and reading it:

```bash
cd harness
for goal in "Approve the August 2026 payroll run" "Delete the August 2026 payroll run" "Show me the open sales deals for Ramesh's department"; do
  .venv/bin/python -m payroll_agent.run "$goal" --jurisdiction IN > /private/tmp/claude-501/live_check.json 2>/dev/null
  .venv/bin/python - <<'EOF'
import json
r = json.load(open("/private/tmp/claude-501/live_check.json"))
print("declined:", r["declined"], "| reason:", (r["decline"] or {}).get("reason_code"), "| skills:", [n["skill"] for n in r["nodes"].values()])
print("   answer:", (r["answer"] or "NONE")[:200])
EOF
done
```
Expected for each of the three: `declined: True`, a sensible reason code, and no data-gathering skills beyond what is needed. Record the actual codes.

Then the three normal requests, which must NOT be declined:

```bash
for goal in "Why is Ramesh's net pay lower in July?" "Check the July 2026 payroll run before we submit it"; do
  .venv/bin/python -m payroll_agent.run "$goal" --jurisdiction IN > /private/tmp/claude-501/live_check.json 2>/dev/null
  .venv/bin/python - <<'EOF'
import json
r = json.load(open("/private/tmp/claude-501/live_check.json"))
print("declined:", r["declined"], "| answered:", r["answer"] is not None, "| skills:", [n["skill"] for n in r["nodes"].values()])
EOF
done
.venv/bin/python -m payroll_agent.run "Run August payroll" --jurisdiction IN --allow run_payroll > /private/tmp/claude-501/live_check.json 2>/dev/null
.venv/bin/python - <<'EOF'
import json
r = json.load(open("/private/tmp/claude-501/live_check.json"))
print("declined:", r["declined"], "| answer:", (r["answer"] or "NONE")[:200])
EOF
```
Expected: `declined: False` for all three. "Run August payroll" is safe: the existing guard refuses while August is `pending_approval`, and that refusal must still arrive through the normal answer path (not as a decline). If any normal request is declined, or any refusal is not, report it with the actual output; do not tune the prompt without telling the user.

- [ ] **Step 5: Update the docs**

In `harness/WORKFLOWS.md`, section G, change the status cells of the rows for "Approve this payrun", "Delete or cancel a non-draft run or its payslips", "Read data from another app...", and "Generate a bank file or disburse" from `todo` to `built (decline_request)` ONLY for those whose live check in Step 4 ended in a decline. Leave the others `todo` and note why.

In `harness/NEXT_STEPS.md`, tick roadmap item 4 only as far as it is true, and add a short "Result" line with the real codes seen in Step 4, which requests were declined, and the live outcome of the three normal requests.

- [ ] **Step 6: Final run**

Run: `cd harness && .venv/bin/python -m pytest -q && .venv/bin/python -m ruff check payroll_agent evals tests`
Expected: all pass, lint clean. Do not commit.

---

## Self-review (against the spec)

- **Problem and goal:** Task 4 Step 4 re-runs the three probe requests and expects structured refusals.
- **Capability, flag, arguments, limits, no `jurisdiction`:** Task 1 (capability, `needs_evidence`, validation tests including the 600/300 limits and bad code).
- **Worker, no calls, text format:** Task 1 worker tests (exact text, with and without alternative).
- **Planner skip and prompt sentence:** Task 2 (review skipped, answer path still reviews, repair of a bad code).
- **Runner `answer`, `declined`, `decline`:** Task 3 (decline, normal answer, no terminal).
- **`refusal` verifier:** Task 3 (accepts a decline node, still fails with no evidence).
- **Testing split and labelling:** every test file is labelled scaffold; the one eval task is `authored_by: "scaffold"`; no graded task is written.
- **Runtime refusals unchanged:** Task 4 Step 4 checks "Run August payroll" still ends through the normal path.
- **Type consistency:** `needs_evidence` (bool), `run_decline_request(ctx, task) -> dict`, `declined`/`decline`/`reason_code` keys are used identically across Tasks 1 to 4.
- **Placeholders:** none. Live-check outcomes and the doc edits in Task 4 depend on real results, which the steps say to record verbatim.
- **No commits:** stated in Global Constraints and the final step.

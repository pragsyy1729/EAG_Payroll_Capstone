"""Implementation scaffolding for the empty-answer fixes found in the platform run.

NOT the team's graded tests. Fakes stand in for the model and the run store.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from evals.official import entry_for
from evals.scoring import FAIL
from evals.verifiers import REGISTRY, VerifyContext
from payroll_agent import workers
from payroll_agent.core.live_graph import TaskSpec

ROWS = [{"id": f"E{i}", "name": f"Employee {i}", "department": "Ops" if i % 2 else "Eng",
         "address": {"line1": "x" * 300}, "history": [{"k": "v"}] * 20, "notes": "n" * 500, "gone": None}
        for i in range(62)]


def ctx_with(replies, calls):
    queue = list(replies)

    async def llm(prompt, system, **kwargs):
        calls.append({"prompt": prompt, "system": system})
        return queue.pop(0) if len(queue) > 1 else queue[0]

    nodes = {"n1": {"skill": "list_employees", "state": "succeeded", "input": {},
                    "result": {"data": ROWS, "total": 62}}}
    store = SimpleNamespace(snapshot=lambda run_id: SimpleNamespace(nodes=nodes))
    return workers.RunContext(run_id="r", store=store, llm=llm, agentswitch=None, goal="q")


def answer(ctx):
    return workers.run_answer_with_evidence(ctx, TaskSpec("a", "answer_with_evidence", {"query": "q"}))


async def test_a_non_empty_reply_is_one_call_with_the_full_evidence():
    calls: list = []
    result = await answer(ctx_with([{"text": "62 employees", "provider": "p", "model": "m"}], calls))
    assert result["text"] == "62 employees" and "empty_reply" not in result and len(calls) == 1
    assert "x" * 300 in calls[0]["prompt"]                                   # nothing was compacted


async def test_an_empty_reply_is_retried_with_compacted_evidence_and_the_second_answer_is_used():
    calls: list = []
    replies = [{"text": "", "finish_reason": "length"}, {"text": "  "}, {"text": "62 employees in 2 departments"}]
    result = await answer(ctx_with(replies, calls))
    assert result["text"] == "62 employees in 2 departments" and len(calls) == 3
    assert len(calls[1]["prompt"]) < len(calls[0]["prompt"]) / 3
    assert "answer now" in calls[1]["system"] and "answer now" not in calls[0]["system"]


async def test_compaction_keeps_every_row_the_total_and_the_scalar_fields_only():
    calls: list = []
    await answer(ctx_with([{"text": ""}, {"text": "ok"}], calls))
    evidence = json.loads(calls[1]["prompt"])["evidence"][0]["result"]
    assert evidence["total"] == 62 and len(evidence["data"]) == 62                  # counts and breakdowns stay exact
    row = evidence["data"][0]
    assert row["id"] == "E0" and row["department"] == "Eng"
    assert "address" not in row and "history" not in row and "gone" not in row and len(row["notes"]) <= 200


async def test_three_empty_replies_return_a_marked_empty_answer_not_a_crash():
    calls: list = []
    result = await answer(ctx_with([{"text": "", "finish_reason": "length"}], calls))
    assert result["text"] == "" and result["empty_reply"] is True and result["finish_reason"] == "length"
    assert len(calls) == 3


def record(answer_value, *, patch_reason=None, nodes=None):
    events = [{"kind": "graph_patched", "reason": patch_reason}] if patch_reason else []
    return {"answer": answer_value, "tool_calls": [], "result": {"patch_events": events, "nodes": nodes or {}}}


async def check(rec):
    ctx = VerifyContext(task={"goal": "x"}, record=rec, before=[], after=[], client=None, jurisdiction="IN")
    return await REGISTRY["answer_grounded"](ctx, {"min_digits": 3})


async def test_a_missing_answer_says_why_when_the_planner_gave_up():
    result = await check(record(None, patch_reason="planner failed validation after repair: planner did not return a JSON object"))
    assert result["ok"] is False and "planner failed validation" in result["observed"]["why"]


async def test_a_missing_answer_says_why_when_the_model_returned_nothing():
    nodes = {"n9": {"skill": "answer_with_evidence", "state": "succeeded",
                    "result": {"text": "", "empty_reply": True, "finish_reason": "length"}}}
    result = await check(record("", nodes=nodes))
    assert "empty reply" in result["observed"]["why"] and "length" in result["observed"]["why"]


def test_the_official_evidence_carries_the_reason_and_keeps_the_old_wording():
    row = {"state": FAIL, "failed_claims": ["the run produced an answer to ground"],
           "verifier_results": [{"claim": "the run produced an answer to ground", "ok": False,
                                 "observed": {"answer": None, "why": "the model returned an empty reply (finish_reason=length)"}}]}
    task = {"id": "t", "goal": "g", "authored_by": "team"}
    evidence = entry_for("suryodaya", task, row)["evidence"]
    assert "ended without a final answer" in evidence and "finish_reason=length" in evidence

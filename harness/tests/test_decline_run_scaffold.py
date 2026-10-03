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

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


class FakeAgentSwitch:
    """Stands in for AgentSwitch. `existing` is a PayRun the run_payroll guard will find."""

    def __init__(self, existing=None):
        self.existing, self.calls = existing, []

    async def call_tool(self, name, arguments=None, *, jurisdiction):
        self.calls.append(name)
        if name == "PayRun.list":
            rows = [self.existing] if self.existing else []
            return {"data": rows, "total": len(rows)}
        if name == "PayRun.run_payroll":
            return {"id": "run-1", "status": "draft"}
        raise AssertionError(f"unexpected tool {name}")

    async def close(self):
        return None


RUN_PAYROLL_PLAN = {"add": [{"id": "payroll", "capability": "run_payroll",
                             "arguments": {"jurisdiction": "IN", "month": "2026-08"}, "depends_on": []}],
                    "cancel": [], "finish": False, "reason": "run it"}
PENDING = {"id": "run-0", "number": "PRUN-1", "pay_period_start": "2026-08-01", "run_type": "regular",
           "status": "pending_approval"}


async def run_with_client(steps, client):
    llm = ScriptedLLM(steps)
    result = await run_goal("Run August payroll and approve it", allowed_side_effects={"run_payroll"},
                            initial_evidence={"jurisdiction": "IN"}, llm=llm, agentswitch=client)
    return llm, result


async def test_two_terminals_in_one_patch_are_rejected_and_repaired():
    both = {**ANSWER_PLAN, "add": ANSWER_PLAN["add"] + DECLINE_PLAN["add"]}
    llm, result = await run_with([{"reply": both}, {"reply": DECLINE_PLAN}])
    assert len(llm.calls) == 2
    assert list(result["nodes"]) == ["decline"]


async def test_a_decline_after_a_mutation_that_happened_is_rejected():
    client = FakeAgentSwitch()                     # no existing run: run_payroll really runs
    late_decline = {**DECLINE_PLAN, "add": [{**DECLINE_PLAN["add"][0], "depends_on": ["payroll"]}]}
    llm, result = await run_with_client(
        [{"reply": RUN_PAYROLL_PLAN}, {"reply": late_decline}, {"reply": ANSWER_PLAN}, {"reply": READY},
         {"reply": "Payroll ran; approval is not something I can do."}], client)
    assert "PayRun.run_payroll" in client.calls
    assert "decline" not in result["nodes"]
    assert result["answer"] == "Payroll ran; approval is not something I can do."


async def test_a_decline_after_a_refused_mutation_is_still_allowed():
    client = FakeAgentSwitch(existing=PENDING)    # guard refuses: nothing changed
    late_decline = {**DECLINE_PLAN, "add": [{**DECLINE_PLAN["add"][0], "depends_on": ["payroll"]}]}
    llm, result = await run_with_client([{"reply": RUN_PAYROLL_PLAN}, {"reply": late_decline}], client)
    assert "PayRun.run_payroll" not in client.calls
    assert result["nodes"]["decline"]["state"] == "succeeded"


async def test_a_decline_cannot_share_a_patch_with_a_mutation():
    client = FakeAgentSwitch()
    together = {**RUN_PAYROLL_PLAN, "add": RUN_PAYROLL_PLAN["add"] + DECLINE_PLAN["add"]}
    llm, result = await run_with_client([{"reply": together}, {"reply": DECLINE_PLAN}], client)
    assert "PayRun.run_payroll" not in client.calls
    assert list(result["nodes"]) == ["decline"]

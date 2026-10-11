"""Implementation scaffolding for the recalculate_payrun guarded action.

NOT the team's graded tests. A fake AgentSwitch client stands in for the platform; no real run is touched.
"""
from __future__ import annotations

import pytest

from payroll_agent import guarded, workers
from payroll_agent.agentswitch import AgentSwitchToolError
from payroll_agent.capabilities import default_registry
from payroll_agent.core.live_graph import TaskSpec

ACTION = guarded.ACTIONS["recalculate_payrun"]


def payrun(rid, status, number=None):
    return {"id": rid, "number": number or f"PRUN-{rid}", "status": status, "run_type": "regular",
            "pay_period_start": "2026-09-01", "pay_period_end": "2026-09-30", "_transitions": []}


def slip(rid, net=1000.0):
    return {"id": f"slip-{rid}", "payrun_id": rid, "employee_id": "E1", "net_pay": net, "gross_pay": net}


class FakeAgentSwitch:
    def __init__(self, runs, slips=(), reply=None, error=False):
        self.runs, self.slips, self.reply, self.error, self.calls = {r["id"]: r for r in runs}, list(slips), reply, error, []

    async def call_tool(self, name, arguments=None, *, jurisdiction):
        arguments = arguments or {}
        self.calls.append((name, arguments))
        if name == "PayRun.list":
            return {"data": [dict(r) for r in self.runs.values()], "total": len(self.runs)}
        if name == "PayRun.get":
            return dict(self.runs[arguments["id"]])
        if name == "PayRunEmployee.list":
            rows = [s for s in self.slips if s["payrun_id"] == arguments.get("payrun_id")]
            return {"data": rows, "total": len(rows)}
        if name == "PayRun.recalculate_lines":
            if self.error:
                raise AgentSwitchToolError(name, -32000, "refused by the platform")
            return self.reply if self.reply is not None else {"recalculated": True, "lines": len(self.slips)}
        raise AssertionError(f"unexpected tool {name}")


def recalc(fake, reference):
    ctx = workers.RunContext(run_id="r", store=None, llm=None, agentswitch=fake)
    return workers._guarded_worker(ACTION)(ctx, TaskSpec("t", "recalculate_payrun",
                                                         {"jurisdiction": "IN", "reference": reference}))


def writes(fake):
    return [args for name, args in fake.calls if name == "PayRun.recalculate_lines"]


def test_the_action_is_defined_with_the_tools_own_argument_name():
    assert ACTION.tool == "PayRun.recalculate_lines" and ACTION.id_argument == "payrun_id"
    assert ACTION.from_statuses == ("draft", "review") and ACTION.to_status is None and ACTION.needs_calculated
    assert all(a.id_argument == "id" for name, a in guarded.ACTIONS.items() if name != "recalculate_payrun")


def test_the_capability_exists_needs_authority_and_warns_that_every_line_is_rewritten():
    capability = default_registry().get("recalculate_payrun")
    assert capability.side_effect is True and set(capability.arguments) == {"jurisdiction", "reference"}
    assert "every" in capability.description and "line" in capability.description


@pytest.mark.parametrize("status", ["draft", "review"])
async def test_a_calculated_draft_or_review_run_is_recalculated_with_payrun_id(status):
    fake = FakeAgentSwitch([payrun("R1", status)], [slip("R1")])
    result = await recalc(fake, "PRUN-R1")
    assert result["performed"] is True and result["action"] == "recalculate_payrun" and result["id"] == "R1"
    assert writes(fake) == [{"payrun_id": "R1"}]                         # never {"id": ...}


@pytest.mark.parametrize("status", ["pending_approval", "approved", "paid", "cancelled"])
async def test_a_run_past_review_is_refused_and_the_tool_is_not_called(status):
    fake = FakeAgentSwitch([payrun("R1", status)], [slip("R1")])
    result = await recalc(fake, "PRUN-R1")
    assert result["error"] is True and result["code"] == "not_in_required_state" and "may_have_changed" not in result
    assert result["message"].endswith("nothing was changed") and writes(fake) == []


async def test_an_uncalculated_draft_is_refused():
    fake = FakeAgentSwitch([payrun("R1", "draft")], [slip("R1", net=0.0)])
    result = await recalc(fake, "PRUN-R1")
    assert result["code"] == "run_not_calculated" and writes(fake) == []


async def test_a_platform_error_is_returned_without_a_retry():
    fake = FakeAgentSwitch([payrun("R1", "draft")], [slip("R1")], error=True)
    result = await recalc(fake, "PRUN-R1")
    assert result["error"] is True and "may_have_changed" not in result and len(writes(fake)) == 1


async def test_a_plain_text_reply_is_reported_as_possibly_changed_not_as_done():
    fake = FakeAgentSwitch([payrun("R1", "draft")], [slip("R1")], reply={"text": "ok"})
    result = await recalc(fake, "PRUN-R1")
    assert result["code"] == "outcome_unverified" and result["may_have_changed"] is True


async def test_an_unknown_run_reference_is_not_found():
    result = await recalc(FakeAgentSwitch([payrun("R1", "draft")], [slip("R1")]), "PRUN-NOPE")
    assert result["error"] is True and result["code"] == "record_not_found"

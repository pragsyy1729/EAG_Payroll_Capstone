"""Implementation scaffolding for the attendance check inside run_payroll.

NOT the team's graded tests. A fake AgentSwitch client stands in for the platform; PayRun.run_payroll is a
recording stub, so no real run is ever opened.
"""
from __future__ import annotations

import asyncio

from payroll_agent import evidence, workers
from payroll_agent.agentswitch import AgentSwitchToolError
from payroll_agent.core.live_graph import TaskSpec

AUGUST = evidence.window_days(month="2026-08")


class FakeAgentSwitch:
    def __init__(self, *, runs=(), attendance_days=(), bad_days=(), target=None):
        self.runs, self.attendance_days, self.bad_days = list(runs), set(attendance_days), set(bad_days)
        self.target, self.calls = target, []

    async def call_tool(self, name, arguments=None, *, jurisdiction):
        arguments = arguments or {}
        self.calls.append((name, arguments))
        await asyncio.sleep(0)
        if name == "PayRun.list":
            return {"data": self.runs, "total": len(self.runs)}
        if name == "PayRun.get":
            return dict(self.target)
        if name == "Attendance.list":
            if arguments["date"] in self.bad_days:
                raise AgentSwitchToolError(name, -32000, "boom")
            count = 3 if arguments["date"] in self.attendance_days else 0
            return {"data": [{}] * min(count, arguments.get("limit", 10)), "total": count}
        if name == "PayRun.run_payroll":
            return {"id": "run-1", "status": "draft"}
        raise AssertionError(f"unexpected tool {name}")


def payroll(fake, **extra):
    ctx = workers.RunContext(run_id="r", store=None, llm=None, agentswitch=fake)
    return workers.run_run_payroll(ctx, TaskSpec("p", "run_payroll", {"jurisdiction": "IN", "month": "2026-08", **extra}))


def names(fake):
    return [name for name, _ in fake.calls]


async def test_a_month_with_no_attendance_is_refused_and_the_write_tool_is_never_called():
    fake = FakeAgentSwitch()
    result = await payroll(fake)
    assert result["error"] is True and result["code"] == "missing_evidence" and result["missing"] == ["attendance"]
    assert "NOT run" in result["message"] and "nothing was changed" in result["message"]
    assert "may_have_changed" not in result and "PayRun.run_payroll" not in names(fake)
    assert names(fake).count("Attendance.list") == 31


async def test_a_fully_covered_month_runs_and_reports_the_coverage():
    fake = FakeAgentSwitch(attendance_days=AUGUST)
    result = await payroll(fake)
    assert result["id"] == "run-1" and result["attendance_check"]["verdict"] == "full"
    assert names(fake).count("PayRun.run_payroll") == 1


async def test_a_partly_covered_month_still_runs_but_says_how_far_the_data_goes():
    fake = FakeAgentSwitch(attendance_days={d for d in AUGUST if d <= "2026-08-12"})
    result = await payroll(fake)
    assert result["id"] == "run-1" and result["attendance_check"]["verdict"] == "partial"
    assert result["attendance_check"]["last_date_with_rows"] == "2026-08-12"


async def test_an_unreadable_day_blocks_the_write_instead_of_looking_like_no_data():
    fake = FakeAgentSwitch(attendance_days=AUGUST, bad_days=["2026-08-10"])
    result = await payroll(fake)
    assert result["code"] == "cannot_verify_attendance" and "may_have_changed" not in result
    assert "PayRun.run_payroll" not in names(fake)


async def test_an_existing_run_past_review_is_refused_before_any_attendance_read():
    pending = {"id": "run-0", "number": "PRUN-1", "pay_period_start": "2026-08-01", "run_type": "regular",
               "status": "pending_approval"}
    fake = FakeAgentSwitch(runs=[pending], attendance_days=AUGUST)
    result = await payroll(fake)
    assert result["code"] == "run_exists_not_recalculable" and "Attendance.list" not in names(fake)


async def test_a_named_run_is_checked_over_its_own_period():
    target = {"id": "T1", "number": "PRUN-9", "status": "draft", "pay_period_start": "2026-09-01",
              "pay_period_end": "2026-09-30"}
    fake = FakeAgentSwitch(target=target)
    result = await payroll(fake, payrun_id="T1")
    assert result["code"] == "missing_evidence" and names(fake).count("Attendance.list") == 30


async def test_a_named_run_without_a_usable_period_cannot_be_verified():
    target = {"id": "T1", "number": "PRUN-9", "status": "draft", "pay_period_start": None, "pay_period_end": None}
    fake = FakeAgentSwitch(target=target)
    result = await payroll(fake, payrun_id="T1")
    assert result["code"] == "cannot_verify_attendance" and "PayRun.run_payroll" not in names(fake)

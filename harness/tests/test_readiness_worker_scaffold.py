"""Implementation scaffolding for the payroll_readiness worker and capability.

NOT the team's graded tests. A fake AgentSwitch client stands in for the platform.
"""
from __future__ import annotations

import asyncio

from payroll_agent import workers
from payroll_agent.agentswitch import AgentSwitchToolError
from payroll_agent.capabilities import default_registry
from payroll_agent.core.live_graph import TaskSpec

from test_readiness_scaffold import END, START, application, line, person  # noqa: E402

READ_ONLY = {"PayRun.get", "PayRunEmployee.list", "Employee.list", "LeaveApplication.list", "Attendance.list"}
RUN = {"id": "R1", "status": "draft", "pay_period_start": START, "pay_period_end": END}


class FakeAgentSwitch:
    def __init__(self, *, run=RUN, lines=(), employees=(), leave=(), attendance_dates=(), lop_rows=None,
                 bad_dates=(), denied=()):
        self.run, self.lines, self.employees, self.leave = run, list(lines), list(employees), list(leave)
        self.attendance_dates, self.lop_rows = set(attendance_dates), lop_rows or {}
        self.bad_dates, self.denied, self.calls = set(bad_dates), set(denied), []

    async def call_tool(self, name, arguments=None, *, jurisdiction):
        arguments = arguments or {}
        self.calls.append((name, arguments))
        assert name in READ_ONLY, f"readiness must stay read-only; unexpected tool {name}"
        if name in self.denied:
            raise AgentSwitchToolError(name, -32001, "denied")
        await asyncio.sleep(0)
        if name == "PayRun.get":
            return dict(self.run)
        if name == "PayRunEmployee.list":
            return self._page(self.lines, arguments)
        if name == "Employee.list":
            return self._page(self.employees, arguments)
        if name == "LeaveApplication.list":
            return self._page([a for a in self.leave if a["status"] == arguments["status"]], arguments)
        if "date" in arguments:                                     # a per-day count
            if arguments["date"] in self.bad_dates:
                raise AgentSwitchToolError(name, -32000, "boom")
            count = 3 if arguments["date"] in self.attendance_dates else 0
            return {"data": [{}] * min(count, arguments.get("limit", 10)), "total": count}
        if arguments.get("is_lop") is not True:        # the live platform rejects 1, "true" and "1": a real boolean only
            raise AgentSwitchToolError(name, -32602, "Invalid tool arguments.")
        return self._page(self.lop_rows.get(arguments["employee_id"], []), arguments)     # one employee's LOP rows

    @staticmethod
    def _page(rows, arguments):
        offset, limit = arguments.get("offset", 0), min(arguments.get("limit", 10), 100)
        return {"data": rows[offset:offset + limit], "limit": limit, "offset": offset, "total": len(rows)}


def readiness(fake):
    ctx = workers.RunContext(run_id="r", store=None, llm=None, agentswitch=fake)
    return workers.run_payroll_readiness(ctx, TaskSpec("p", "payroll_readiness", {"jurisdiction": "IN", "payrun_id": "R1"}))


def all_days():
    from payroll_agent import readiness as rules
    return rules.period_dates(START, END)


async def test_a_calculated_run_gets_every_check_and_only_reads():
    people = [person("E1", joined="2026-09-15"), person("E2")]
    lines = [{**line("E1"), "payrun_id": "R1"}, {**line("E2", lop=2.0), "payrun_id": "R1"}]
    fake = FakeAgentSwitch(lines=lines, employees=people, attendance_dates=[d for d in all_days() if d <= "2026-09-12"],
                           leave=[application("a", "E2", "pending_approval", "2026-09-10", "2026-09-11")],
                           lop_rows={"E2": [{"employee_id": "E2", "date": "2026-09-03", "is_lop": 1}]})
    result = await readiness(fake)
    assert result["calculated"] is True and result["skipped"] == []
    assert set(result["counts"]["by_check"]) == {"proration", "pending_leave", "attendance_coverage", "lop_mismatch"}
    assert result["basic_share"]["checked"] == 2 and result["attendance"]["last_date_with_rows"] == "2026-09-12"
    assert {name for name, _ in fake.calls} <= READ_ONLY


async def test_an_uncalculated_run_still_checks_attendance_and_leave_and_skips_the_line_checks():
    people = [person("E1"), person("E2", status="left")]
    fake = FakeAgentSwitch(lines=[], employees=people, attendance_dates=[],
                           leave=[application("a", "E1", "pending_approval", "2026-09-10", "2026-09-11"),
                                  application("b", "E2", "pending_approval", "2026-09-10", "2026-09-11")])
    result = await readiness(fake)
    assert result["calculated"] is False
    assert {s["check"] for s in result["skipped"]} == {"proration", "lop_mismatch", "basic_share"}
    by_check = result["counts"]["by_check"]
    assert by_check == {"attendance_coverage": 1, "pending_leave": 1}      # the leaver's application is ignored
    assert [f["severity"] for f in result["findings"] if f["check"] == "attendance_coverage"] == ["high"]


async def test_attendance_is_counted_once_per_day_and_lop_rows_only_for_candidates():
    lines = [{**line("E1", lop=0.0), "payrun_id": "R1"}, {**line("E2", lop=1.0), "payrun_id": "R1"}]
    fake = FakeAgentSwitch(lines=lines, employees=[person("E1"), person("E2")], attendance_dates=all_days())
    await readiness(fake)
    day_calls = [a for n, a in fake.calls if n == "Attendance.list" and "date" in a]
    assert sorted(a["date"] for a in day_calls) == all_days() and all(a["limit"] == 1 for a in day_calls)
    lop_calls = [a for n, a in fake.calls if n == "Attendance.list" and "employee_id" in a]
    assert [a["employee_id"] for a in lop_calls] == ["E2"] and lop_calls[0]["is_lop"] is True


async def test_candidates_beyond_the_cap_are_counted_not_silently_dropped():
    lines = [{**line(f"E{i}", lop=1.0), "payrun_id": "R1"} for i in range(30)]
    people = [person(f"E{i}") for i in range(30)]
    result = await readiness(FakeAgentSwitch(lines=lines, employees=people, attendance_dates=all_days()))
    assert result["lop_candidates_not_checked"] == 5


async def test_one_unreadable_day_is_reported_not_treated_as_empty():
    fake = FakeAgentSwitch(lines=[], employees=[person("E1")], attendance_dates=all_days(), bad_dates=["2026-09-10"])
    result = await readiness(fake)
    assert result["attendance"]["unreadable_days"] == ["2026-09-10"]
    assert result["counts"]["by_check"].get("attendance_coverage") == 1


async def test_an_unusable_period_is_an_error_result_with_no_attendance_calls():
    fake = FakeAgentSwitch(run={**RUN, "pay_period_start": "2026-01-01", "pay_period_end": "2026-12-31"})
    result = await readiness(fake)
    assert result["error"] is True and result["code"] == "period_unusable" and result["tool"] == "payroll_readiness"
    assert not any(n == "Attendance.list" for n, _ in fake.calls)


async def test_a_denied_read_is_the_tools_own_error():
    result = await readiness(FakeAgentSwitch(employees=[person("E1")], denied=("LeaveApplication.list",)))
    assert result["error"] is True and result["code"] == -32001


def test_the_capability_is_registered_read_only():
    capability = default_registry().get("payroll_readiness")
    assert capability.side_effect is False and set(capability.arguments) == {"jurisdiction", "payrun_id"}


async def test_the_worker_reports_what_each_check_considered_even_when_nothing_was_found():
    lines = [{**line("E1", lop=1.0), "payrun_id": "R1"}, {**line("E2"), "payrun_id": "R1"}]
    fake = FakeAgentSwitch(lines=lines, employees=[person("E1"), person("E2")], attendance_dates=all_days(),
                           leave=[application("a", "E1", "draft", "2026-10-02", "2026-10-03"),
                                  application("b", "E2", "pending_approval", "2026-10-02", "2026-10-03")],
                           lop_rows={"E1": [{"employee_id": "E1", "date": "2026-09-03", "is_lop": 1}]})
    result = await readiness(fake)
    assert result["counts"]["total"] == 0                                   # the leave is after the period
    assert result["checks"]["pending_leave"] == {"ran": True, "considered": 2,
                                                 "unit": "leave applications (pending or draft)", "findings": 0}
    assert result["checks"]["attendance_coverage"]["considered"] == 30
    assert result["checks"]["lop_mismatch"]["considered"] == 1 and result["checks"]["proration"]["considered"] == 2

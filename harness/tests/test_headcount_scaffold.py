"""Implementation scaffolding for the headcount report.

NOT the team's graded tests. A fake AgentSwitch client stands in for the platform.
"""
from __future__ import annotations

from payroll_agent import headcount, workers
from payroll_agent.capabilities import default_registry
from payroll_agent.core.live_graph import TaskSpec


def employee(i, department, status="active", kind="full_time"):
    return {"id": f"E{i}", "number": f"EMP-{i}", "_department_id_display": department, "status": status,
            "employment_type": kind}


ROWS = ([employee(i, "Production") for i in range(5)] + [employee(10 + i, "Accounts") for i in range(3)]
        + [employee(20, "Accounts", status="suspended", kind="intern"), employee(21, None, status=None)])


def test_the_report_counts_in_code_by_department_status_and_type():
    report = headcount.build_headcount(ROWS)
    assert report["total"] == 10 and report["calculated"] is True
    assert report["by_status"] == {"active": 8, "suspended": 1, "unknown": 1}
    assert report["by_department"]["Production"] == {"total": 5, "by_status": {"active": 5}}
    assert report["by_department"]["Accounts"] == {"total": 4, "by_status": {"active": 3, "suspended": 1}}
    assert report["by_department"]["Unassigned"]["total"] == 1
    assert report["by_employment_type"] == {"full_time": 9, "intern": 1}
    assert sum(d["total"] for d in report["by_department"].values()) == report["total"]


def test_departments_are_listed_largest_first_and_no_employee_rows_are_returned():
    report = headcount.build_headcount(ROWS)
    assert list(report["by_department"]) == ["Production", "Accounts", "Unassigned"]
    assert "employees" not in report and "data" not in report


def test_an_empty_company_reports_zero_not_an_error():
    report = headcount.build_headcount([])
    assert report["total"] == 0 and report["by_department"] == {}


def test_the_note_tells_the_model_to_quote_counts_and_not_list_people():
    note = headcount.build_headcount(ROWS)["note"]
    assert "counts computed in code" in note and "do not list" in note


class FakeAgentSwitch:
    def __init__(self, rows=None, error=False):
        self.rows, self.error, self.calls = rows if rows is not None else ROWS, error, []

    async def call_tool(self, name, arguments=None, *, jurisdiction):
        self.calls.append((name, arguments, jurisdiction))
        if self.error:
            from payroll_agent.agentswitch import AgentSwitchToolError
            raise AgentSwitchToolError(name, -32000, "refused")
        offset, limit = arguments.get("offset", 0), arguments.get("limit", 100)
        return {"data": self.rows[offset:offset + limit], "total": len(self.rows)}


def run(fake):
    ctx = workers.RunContext(run_id="r", store=None, llm=None, agentswitch=fake)
    return workers.run_headcount_report(ctx, TaskSpec("t", "headcount_report", {"jurisdiction": "IN"}))


async def test_the_worker_fetches_every_employee_and_reports_the_counts():
    fake = FakeAgentSwitch()
    result = await run(fake)
    assert result["total"] == 10 and fake.calls[0][0] == "Employee.list" and fake.calls[0][2] == "IN"


async def test_a_platform_error_is_returned_with_the_tool_name_not_raised():
    result = await run(FakeAgentSwitch(error=True))
    assert result["error"] is True and result["tool"] == "Employee.list"


def test_the_capability_is_a_read_only_evidence_capability_the_planner_can_choose():
    capability = default_registry().get("headcount_report")
    assert capability.side_effect is False and set(capability.arguments) == {"jurisdiction"}
    for phrase in ("how many employees", "department", "do not list"):
        assert phrase in capability.description.lower() or phrase in capability.description, phrase
    assert "headcount_report" in workers._WORKERS

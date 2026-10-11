"""Implementation scaffolding for the reporting additions' workers and capabilities.

NOT the team's graded tests. A fake AgentSwitch client stands in for the platform.
"""
from __future__ import annotations

from payroll_agent import workers
from payroll_agent.agentswitch import AgentSwitchToolError
from payroll_agent.capabilities import default_registry
from payroll_agent.core.live_graph import TaskSpec

from test_reporting_additions_scaffold import ROWS  # noqa: E402

READ_ONLY = {"PayRun.get", "PayRun.list", "PayRunEmployee.list", "Employee.list"}


class FakeAgentSwitch:
    def __init__(self, runs, slips):
        self.runs, self.slips, self.calls = {r["id"]: r for r in runs}, slips, []

    async def call_tool(self, name, arguments=None, *, jurisdiction):
        arguments = arguments or {}
        self.calls.append((name, arguments))
        assert name in READ_ONLY, f"reports must stay read-only; unexpected tool {name}"
        if name == "PayRun.get":
            if arguments["id"] not in self.runs:
                raise AgentSwitchToolError(name, -32000, "not found")
            return dict(self.runs[arguments["id"]])
        rows = (list(self.runs.values()) if name == "PayRun.list"
                else [] if name == "Employee.list" else self.slips.get(arguments.get("payrun_id"), []))
        offset, limit = arguments.get("offset", 0), min(arguments.get("limit", 10), 100)
        return {"data": rows[offset:offset + limit], "limit": limit, "offset": offset, "total": len(rows)}


def month(rid, start, end, **fields):
    return {"id": rid, "number": f"PRUN-{rid}", "run_type": "regular", "status": "paid", "pay_period_start": start,
            "pay_period_end": end, "total_gross_pay": 70000.0, "total_net_pay": 66400.0, "total_deductions": 3600.0,
            "total_employer_contribution": 200.0, "employee_count": 2, **fields}


def slips_for(rid, scale=1.0):
    return [{**row, "payrun_id": rid, "gross_pay": row["gross_pay"] * scale, "net_pay": row["net_pay"] * scale}
            for row in ROWS]


def ctx(fake):
    return workers.RunContext(run_id="r", store=None, llm=None, agentswitch=fake)


def report(fake, **extra):
    task = TaskSpec("c", "payroll_cost_report", {"jurisdiction": "IN", "payrun_id": "NOW", "group_by": "none", **extra})
    return workers.run_payroll_cost_report(ctx(fake), task)


def fixture():
    runs = [month("NOW", "2026-09-01", "2026-09-30"), month("LASTYEAR", "2025-09-01", "2025-09-30"),
            month("PREV", "2026-08-01", "2026-08-31")]
    slips = {"NOW": slips_for("NOW"), "LASTYEAR": slips_for("LASTYEAR", 0.5), "PREV": slips_for("PREV", 0.9)}
    return FakeAgentSwitch(runs, slips)


async def test_year_on_year_compares_with_the_same_month_last_year_not_last_month():
    result = await report(fixture(), year_on_year=True)
    assert result["variance"]["compared_to"] == "LASTYEAR"
    assert result["variance"]["totals"]["gross_pay"]["previous"] == 35000.0


async def test_year_on_year_with_no_matching_run_is_skipped_with_that_reason():
    fake = fixture()
    del fake.runs["LASTYEAR"]
    result = await report(fake, year_on_year=True)
    assert result["variance"] is None
    assert {"what": "variance", "reason": "no regular run for the same month last year"} in result["skipped"]
    assert not any(a.get("payrun_id") == "PREV" for n, a in fake.calls if n == "PayRunEmployee.list")


async def test_an_explicit_compare_to_wins_over_year_on_year():
    result = await report(fixture(), year_on_year=True, compare_to="PREV")
    assert result["variance"]["compared_to"] == "PREV"


async def test_default_output_is_unchanged_and_components_come_only_when_asked():
    plain = await report(fixture())
    assert "components" not in plain and plain["variance"] is None
    with_components = await report(fixture(), with_components=True)
    assert with_components["components"]["earnings_total"] == 70000.0


async def test_the_register_reads_the_run_and_its_lines_only():
    fake = fixture()
    task = TaskSpec("g", "payroll_register", {"jurisdiction": "IN", "payrun_id": "NOW"})
    result = await workers.run_payroll_register(ctx(fake), task)
    assert result["calculated"] is True and result["lines_total"] == 2
    assert {name for name, _ in fake.calls} == {"PayRun.get", "PayRunEmployee.list"}


async def test_the_register_of_a_missing_run_is_the_tools_own_error():
    task = TaskSpec("g", "payroll_register", {"jurisdiction": "IN", "payrun_id": "NOPE"})
    result = await workers.run_payroll_register(ctx(fixture()), task)
    assert result["error"] is True


def test_the_capabilities_expose_the_new_arguments():
    registry = default_registry()
    assert registry.get("payroll_register").side_effect is False
    arguments = registry.get("payroll_cost_report").arguments
    assert "year_on_year" in arguments and "with_components" in arguments
    assert registry.validate("payroll_cost_report", {"jurisdiction": "IN", "payrun_id": "R", "year_on_year": True},
                             known_values={"R"})["year_on_year"] is True


async def test_a_cancelled_or_non_regular_comparison_run_is_refused_as_a_baseline():
    """Found live: an explicit comparison run bypassed the rule that auto-chosen baselines are regular and not cancelled."""
    fake = fixture()
    fake.runs["CANCELLED"] = month("CANCELLED", "2025-08-01", "2025-08-31", status="cancelled")
    fake.runs["BONUS"] = month("BONUS", "2026-08-15", "2026-08-31", run_type="bonus")
    fake.slips["CANCELLED"], fake.slips["BONUS"] = slips_for("CANCELLED"), slips_for("BONUS")
    for bad, word in (("CANCELLED", "cancelled"), ("BONUS", "regular")):
        result = await report(fake, compare_to=bad)
        assert result["variance"] is None, bad
        reasons = [s["reason"] for s in result["skipped"] if s["what"] == "variance"]
        assert reasons and word in reasons[0] and "comparison run" in reasons[0], bad
    assert not any(a.get("payrun_id") in ("CANCELLED", "BONUS") for n, a in fake.calls if n == "PayRunEmployee.list")
    ok = await report(fake, compare_to="PREV")
    assert ok["variance"]["compared_to"] == "PREV"

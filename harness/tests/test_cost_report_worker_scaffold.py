"""Implementation scaffolding for the payroll_cost_report capability and worker.

NOT the team's graded tests. A fake AgentSwitch client stands in for the platform
so fetching, paging, comparison-run choice and error handling can be checked offline.
"""
from __future__ import annotations

import pytest

from payroll_agent import workers
from payroll_agent.agentswitch import AgentSwitchToolError
from payroll_agent.capabilities import CapabilityError, default_registry
from payroll_agent.core.live_graph import TaskSpec


class FakeAgentSwitch:
    def __init__(self, *, runs, slips, employees, page_cap=100, short_total=None, denied=()):
        self.runs = {item["id"]: item for item in runs}
        self.slips, self.employees = slips, employees
        self.page_cap, self.short_total, self.denied = page_cap, short_total, set(denied)
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name, arguments=None, *, jurisdiction):
        arguments = arguments or {}
        self.calls.append((name, arguments))
        if name in self.denied:
            raise AgentSwitchToolError(name, -32001, "denied")
        if name == "PayRun.get":
            if arguments["id"] not in self.runs:
                raise AgentSwitchToolError(name, -32000, "not found")
            return dict(self.runs[arguments["id"]])
        if name == "PayRun.list":
            return self._page(list(self.runs.values()), arguments)
        if name == "PayRunEmployee.list":
            return self._page(self.slips.get(arguments.get("payrun_id"), []), arguments)
        if name == "Employee.list":
            return self._page(self.employees, arguments)
        raise AssertionError(f"the report must stay read-only; unexpected tool {name}")

    def _page(self, rows, arguments):
        offset, limit = arguments.get("offset", 0), min(arguments.get("limit", 10), self.page_cap)
        total = len(rows) if self.short_total is None else self.short_total
        return {"data": rows[offset:offset + limit], "limit": limit, "offset": offset, "total": total}


def run(rid, start, end, *, status="paid", header=None, run_type="regular"):
    return {"id": rid, "pay_period_start": start, "pay_period_end": end, "status": status,
            "run_type": run_type, **(header or {})}


def slip(rid, emp, gross, net, employer=0.0, ot=0.0):
    return {"id": f"{rid}-{emp}", "payrun_id": rid, "employee_id": emp, "employee_name": emp.upper(),
            "gross_pay": gross, "net_pay": net, "employer_contribution": employer, "overtime_pay": ot,
            "total_deductions": 0.0}


def person(emp, dept="Production", loc="Pune"):
    return {"id": emp, "_department_id_display": dept, "_work_location_id_display": loc}


R3_HEADER = {"total_gross_pay": 3600, "total_net_pay": 3240, "total_deductions": 0,
             "total_employer_contribution": 360, "employee_count": 3}


def standard_fake(**overrides):
    kwargs = dict(
        runs=[run("R1", "2026-06-01", "2026-06-30"), run("R2", "2026-07-01", "2026-07-31"),
              run("R3", "2026-08-01", "2026-08-31", status="pending_approval", header=R3_HEADER)],
        slips={"R1": [slip("R1", "a", 800, 700), slip("R1", "b", 2000, 1800)],
               "R2": [slip("R2", "a", 1000, 900, 100, 50), slip("R2", "b", 2000, 1800, 200)],
               "R3": [slip("R3", "a", 1100, 990, 110, 60), slip("R3", "b", 2000, 1800, 200),
                      slip("R3", "c", 500, 450, 50)]},
        employees=[person("a"), person("b"), person("c", "Stores", "Nashik")])
    kwargs.update(overrides)
    return FakeAgentSwitch(**kwargs)


def report(fake, **extra):
    ctx = workers.RunContext(run_id="r", store=None, llm=None, agentswitch=fake)
    task = TaskSpec("rep", "payroll_cost_report", {"jurisdiction": "IN", "payrun_id": "R3", **extra})
    return workers.run_payroll_cost_report(ctx, task)


def called(fake):
    return [name for name, _ in fake.calls]


async def test_default_report_groups_by_department_and_matches_the_header():
    fake = standard_fake()
    result = await report(fake)
    assert result["calculated"] is True and result["totals"]["headcount"] == 3
    assert [g["group"] for g in result["groups"]] == ["Production", "Stores"]
    assert result["consistency"]["header_matches_slips"] is True
    assert result["variance"] is None
    assert set(called(fake)) == {"PayRun.get", "PayRunEmployee.list", "Employee.list"}


async def test_group_by_none_does_not_fetch_the_employee_list():
    fake = standard_fake()
    result = await report(fake, group_by="none")
    assert result["groups"] == [] and "Employee.list" not in called(fake)


async def test_with_variance_compares_to_the_latest_earlier_calculated_run():
    fake = standard_fake()
    result = await report(fake, with_variance=True)
    assert result["variance"]["compared_to"] == "R2"
    assert result["variance"]["joiners"]["count"] == 1          # employee c is new
    assert result["variance"]["totals"]["gross_pay"]["previous"] == 3000.0


async def test_an_explicit_compare_to_is_used_and_implies_variance():
    fake = standard_fake()
    result = await report(fake, compare_to="R1")
    assert result["variance"]["compared_to"] == "R1"


async def test_an_uncalculated_run_needs_no_employee_list_or_comparison():
    fake = standard_fake(slips={"R3": [slip("R3", "a", 0, 0)]})
    result = await report(fake, with_variance=True)
    assert result["calculated"] is False and "totals" not in result
    assert called(fake).count("PayRunEmployee.list") == 1 and "Employee.list" not in called(fake)


async def test_no_earlier_run_skips_only_the_variance():
    fake = standard_fake(runs=[run("R3", "2026-08-01", "2026-08-31", header=R3_HEADER)])
    result = await report(fake, with_variance=True)
    assert result["variance"] is None and result["totals"]["headcount"] == 3
    assert result["skipped"] == [{"what": "variance", "reason": "no earlier calculated regular run"}]


async def test_a_short_fetch_of_the_runs_own_rows_is_scan_incomplete():
    result = await report(standard_fake(short_total=50))
    assert result["error"] is True and result["code"] == "scan_incomplete"


async def test_a_denied_employee_list_keeps_its_own_error():
    result = await report(standard_fake(denied=("Employee.list",)))
    assert result["error"] is True and result["tool"] == "Employee.list" and result["code"] == -32001


async def test_paging_collects_every_row():
    result = await report(standard_fake(page_cap=2))
    assert result["totals"]["headcount"] == 3


def test_capability_is_registered_read_only_with_defaults_and_id_provenance():
    registry = default_registry()
    capability = registry.get("payroll_cost_report")
    assert capability.side_effect is False
    assert "payroll_cost_report" in registry.family("evidence")
    assert "payroll_cost_report" in workers._WORKERS
    clean = registry.validate("payroll_cost_report", {"jurisdiction": "IN", "payrun_id": "R3"},
                              known_values={"R3"})
    assert clean["group_by"] == "department" and clean["with_variance"] is False
    with pytest.raises(CapabilityError):
        registry.validate("payroll_cost_report", {"jurisdiction": "IN", "payrun_id": "R3"}, known_values=set())


@pytest.mark.parametrize("bad", [{"group_by": "team"}, {"with_variance": "yes"}, {"compare_to": "R9"}])
def test_capability_rejects_bad_arguments(bad):
    with pytest.raises(CapabilityError):
        default_registry().validate("payroll_cost_report", {"jurisdiction": "IN", "payrun_id": "R3", **bad},
                                    known_values={"R3"})

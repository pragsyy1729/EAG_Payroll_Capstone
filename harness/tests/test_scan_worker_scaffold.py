"""Implementation scaffolding for the pre_payroll_scan capability and worker.

NOT the team's graded tests. A fake AgentSwitch client stands in for the
platform so the worker's fetching, paging and comparison-run choice can be
checked offline while it is being built.
"""
from __future__ import annotations

import pytest

from payroll_agent import workers
from payroll_agent.agentswitch import AgentSwitchToolError
from payroll_agent.capabilities import CapabilityError, default_registry
from payroll_agent.core.live_graph import TaskSpec


class FakeAgentSwitch:
    def __init__(self, *, runs, slips, employees, page_cap=100, short_total=None,
                 omit_total=False, denied=()):
        self.runs = {run["id"]: run for run in runs}
        self.slips, self.employees = slips, employees
        self.page_cap, self.short_total = page_cap, short_total
        self.omit_total, self.denied = omit_total, set(denied)
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
        raise AssertionError(f"scan must stay read-only; unexpected tool {name}")

    def _page(self, rows, arguments):
        offset, limit = arguments.get("offset", 0), min(arguments.get("limit", 10), self.page_cap)
        total = len(rows) if self.short_total is None else self.short_total
        total = None if self.omit_total else total
        return {"data": rows[offset:offset + limit], "limit": limit, "offset": offset, "total": total}


def run(rid, start, end, *, status="paid", run_type="regular"):
    return {"id": rid, "pay_period_start": start, "pay_period_end": end, "status": status, "run_type": run_type}


def slip(rid, emp, net, name=None):
    return {"id": f"{rid}-{emp}", "payrun_id": rid, "employee_id": emp,
            "employee_name": name or emp.upper(), "net_pay": net}


def person(emp, status="active", exit_date=None):
    return {"id": emp, "status": status, "exit_date": exit_date}


def scan(fake, **extra):
    ctx = workers.RunContext(run_id="r", store=None, llm=None, agentswitch=fake)
    task = TaskSpec("scan", "pre_payroll_scan", {"jurisdiction": "IN", "payrun_id": "R3", **extra})
    return workers.run_pre_payroll_scan(ctx, task)


def standard_fake(**overrides):
    kwargs = dict(
        runs=[run("R1", "2026-06-01", "2026-06-30"), run("R2", "2026-07-01", "2026-07-31"),
              run("R3", "2026-08-01", "2026-08-31", status="pending_approval"),
              run("RX", "2026-07-15", "2026-07-31", run_type="bonus"),
              run("RC", "2026-07-02", "2026-07-31", status="cancelled"),
              run("R4", "2026-09-01", "2026-09-30", status="draft")],
        slips={"R1": [slip("R1", "a", 1000)], "R2": [slip("R2", "a", 1000), slip("R2", "b", 1000)],
               "R3": [slip("R3", "a", 400), slip("R3", "b", 1000), slip("R3", "c", 1000)],
               "RX": [slip("RX", "a", 5)], "RC": [slip("RC", "a", 7)]},
        employees=[person("a"), person("b", "left"), person("c")])
    kwargs.update(overrides)
    return FakeAgentSwitch(**kwargs)


async def test_scan_compares_to_latest_earlier_regular_calculated_run():
    fake = standard_fake()
    result = await scan(fake)
    assert result["compared_to"] == "R2"            # not the bonus, cancelled or later runs
    assert result["calculated"] is True and result["run_status"] == "pending_approval"
    assert result["counts"]["new_in_run"] == 1      # employee c is new
    kinds = {(f["check"], f["employee_id"]) for f in result["findings"]}
    assert ("net_pay_change", "a") in kinds and ("payee_status", "b") in kinds


async def test_scan_only_reads():
    fake = standard_fake()
    await scan(fake)
    assert {name for name, _ in fake.calls} <= {"PayRun.get", "PayRun.list", "PayRunEmployee.list", "Employee.list"}


async def test_uncalculated_run_is_reported_not_calculated():
    fake = standard_fake(slips={"R3": [slip("R3", "a", 0), slip("R3", "b", 0)]})
    result = await scan(fake)
    assert result["calculated"] is False and result["findings"] == []
    assert result["compared_to"] is None
    assert [n for n, _ in fake.calls].count("PayRunEmployee.list") == 1   # no comparison fetch


async def test_no_earlier_calculated_run_skips_only_the_change_check():
    fake = standard_fake(runs=[run("R3", "2026-08-01", "2026-08-31")],
                         slips={"R3": [slip("R3", "a", 400), slip("R3", "b", 1000)]})
    result = await scan(fake)
    assert result["compared_to"] is None
    assert result["skipped"] == [{"check": "net_pay_change", "reason": "no earlier calculated regular run"}]
    assert "payee_status" in result["counts"]["by_check"]


async def test_explicit_compare_to_is_used():
    fake = standard_fake()
    result = await scan(fake, compare_to="R1")
    assert result["compared_to"] == "R1"


async def test_threshold_argument_changes_the_result():
    fake = standard_fake(slips={"R2": [slip("R2", "a", 1000)], "R3": [slip("R3", "a", 1200)]},
                         employees=[person("a")])
    default = await scan(fake)
    lower = await scan(standard_fake(slips={"R2": [slip("R2", "a", 1000)], "R3": [slip("R3", "a", 1200)]},
                                     employees=[person("a")]), change_threshold_pct=10)
    assert default["counts"]["by_check"]["net_pay_change"] == 0
    assert lower["counts"]["by_check"]["net_pay_change"] == 1


async def test_payee_missing_from_employee_list_is_a_finding():
    fake = standard_fake(employees=[person("a"), person("b")])      # c is not listed
    result = await scan(fake)
    assert any(f["employee_id"] == "c" and f["evidence"] == {"reason": "not_in_employee_list"}
               for f in result["findings"])


async def test_paging_collects_every_row():
    rows = [slip("R3", f"e{i}", 100) for i in range(5)]
    fake = standard_fake(slips={"R3": rows}, employees=[person(f"e{i}") for i in range(5)], page_cap=2)
    result = await scan(fake)
    assert result["counts"]["rows_checked"] == 5


async def test_short_fetch_of_own_rows_is_scan_incomplete():
    fake = standard_fake(short_total=50)
    result = await scan(fake)
    assert result["error"] is True and result["code"] == "scan_incomplete"


async def test_tool_error_on_payrun_get_becomes_an_error_result():
    fake = standard_fake()
    ctx = workers.RunContext(run_id="r", store=None, llm=None, agentswitch=fake)
    task = TaskSpec("scan", "pre_payroll_scan", {"jurisdiction": "IN", "payrun_id": "missing"})
    result = await workers.run_pre_payroll_scan(ctx, task)
    assert result["error"] is True and result["tool"] == "PayRun.get"


def test_capability_is_registered_read_only_with_id_provenance():
    registry = default_registry()
    capability = registry.get("pre_payroll_scan")
    assert capability.side_effect is False
    assert "pre_payroll_scan" in registry.family("evidence")
    assert "pre_payroll_scan" in workers._WORKERS
    clean = registry.validate("pre_payroll_scan", {"jurisdiction": "IN", "payrun_id": "R3"}, known_values={"R3"})
    assert clean["change_threshold_pct"] == 30
    with pytest.raises(CapabilityError):
        registry.validate("pre_payroll_scan", {"jurisdiction": "IN", "payrun_id": "R3"}, known_values=set())
    for bad in (0, 101):
        with pytest.raises(CapabilityError):
            registry.validate("pre_payroll_scan",
                              {"jurisdiction": "IN", "payrun_id": "R3", "change_threshold_pct": bad},
                              known_values={"R3"})


async def test_comparison_run_without_a_start_date_is_never_the_baseline():
    null_start = {"id": "RN", "pay_period_start": None, "pay_period_end": None, "status": "paid", "run_type": "regular"}
    no_key = {"id": "RM", "status": "paid", "run_type": "regular"}
    fake = standard_fake(
        runs=[run("R1", "2026-06-01", "2026-06-30"), run("R3", "2026-08-01", "2026-08-31"), null_start, no_key],
        slips={"R1": [slip("R1", "a", 1000)], "R3": [slip("R3", "a", 1000)],
               "RN": [slip("RN", "a", 1)], "RM": [slip("RM", "a", 1)]},
        employees=[person("a")])
    result = await scan(fake)
    assert result["compared_to"] == "R1"


async def test_full_page_without_a_total_keeps_paging():
    rows = [slip("R3", f"e{i}", 100) for i in range(150)]
    fake = standard_fake(slips={"R3": rows}, employees=[person(f"e{i}") for i in range(150)], omit_total=True)
    result = await scan(fake)
    assert result["counts"]["rows_checked"] == 150


async def test_missing_total_and_page_limit_reached_is_scan_incomplete(monkeypatch):
    monkeypatch.setattr(workers, "_MAX_PAGES", 1)
    rows = [slip("R3", f"e{i}", 100) for i in range(150)]
    fake = standard_fake(slips={"R3": rows}, employees=[person(f"e{i}") for i in range(150)], omit_total=True)
    result = await scan(fake)
    assert result["error"] is True and result["code"] == "scan_incomplete"


async def test_tool_error_on_a_list_fetch_keeps_its_own_code():
    fake = standard_fake(denied=("Employee.list",))
    result = await scan(fake)
    assert result["error"] is True
    assert result["tool"] == "Employee.list" and result["code"] == -32001


async def test_uncalculated_run_is_reported_even_if_the_employee_list_is_denied():
    fake = standard_fake(slips={"R3": [slip("R3", "a", 0)]}, denied=("Employee.list",))
    result = await scan(fake)
    assert result["calculated"] is False
    assert "Employee.list" not in [name for name, _ in fake.calls]

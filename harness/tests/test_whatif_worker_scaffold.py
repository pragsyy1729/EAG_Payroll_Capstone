"""Implementation scaffolding for the salary_whatif worker and capability.

NOT the team's graded tests. A fake AgentSwitch client stands in for the platform.
"""
from __future__ import annotations

from payroll_agent import workers
from payroll_agent.agentswitch import AgentSwitchToolError
from payroll_agent.capabilities import CapabilityError, default_registry
from payroll_agent.core.live_graph import TaskSpec

from test_whatif_scaffold import CONFIGS, line  # noqa: E402  (shared fixtures)

READ_ONLY = {"PayRunEmployee.list", "EPFConfig.list", "ESIConfig.list", "PTConfig.list"}


class FakeAgentSwitch:
    def __init__(self, rows, configs=CONFIGS, denied=()):
        self.tables = {"PayRunEmployee": rows, **{k: v for k, v in configs.items()}}
        self.denied, self.calls = set(denied), []

    async def call_tool(self, name, arguments=None, *, jurisdiction):
        arguments = arguments or {}
        self.calls.append((name, arguments))
        assert name in READ_ONLY, f"the estimator must stay read-only; unexpected tool {name}"
        if name in self.denied:
            raise AgentSwitchToolError(name, -32001, "denied")
        entity = name.partition(".")[0]
        rows = [r for r in self.tables.get(entity, [])
                if all(r.get(k) == arguments[k] for k in ("employee_id",) if k in arguments)]
        offset, limit = arguments.get("offset", 0), min(arguments.get("limit", 10), 100)
        return {"data": rows[offset:offset + limit], "limit": limit, "offset": offset, "total": len(rows)}


def estimate(fake, jurisdiction="IN", **extra):
    ctx = workers.RunContext(run_id="r", store=None, llm=None, agentswitch=fake)
    args = {"jurisdiction": jurisdiction, "employee_id": "E1", **extra}
    return workers.run_salary_whatif(ctx, TaskSpec("w", "salary_whatif", args))


async def test_an_estimate_reads_only_and_returns_the_labelled_result():
    fake = FakeAgentSwitch([line()])
    result = await estimate(fake, percent_raise=10)
    assert result["estimate"] is True and result["written"] is False
    assert result["after"]["net"] == 40900.0
    assert {name for name, _ in fake.calls} <= READ_ONLY


async def test_the_pay_lines_are_fetched_for_that_employee_only():
    fake = FakeAgentSwitch([line(), line(id="L2", employee_id="E2")])
    result = await estimate(fake, percent_raise=10)
    assert result["basis"]["pay_line_id"] == "L1"


async def test_the_us_tenant_is_not_supported_and_nothing_is_called():
    fake = FakeAgentSwitch([line()])
    result = await estimate(fake, jurisdiction="US", percent_raise=10)
    assert result["error"] is True and result["code"] == "jurisdiction_unsupported" and fake.calls == []


async def test_no_change_or_two_changes_is_an_invalid_change():
    for extra in ({}, {"percent_raise": 10, "new_monthly_gross": 50000}):
        result = await estimate(FakeAgentSwitch([line()]), **extra)
        assert result["code"] == "invalid_change" and result["tool"] == "salary_whatif"


async def test_an_employee_with_no_full_month_line_has_no_basis():
    result = await estimate(FakeAgentSwitch([line(days_payable=4.0)]), percent_raise=10)
    assert result["error"] is True and result["code"] == "no_basis_pay_line"


async def test_bad_earnings_are_reported_not_scaled():
    result = await estimate(FakeAgentSwitch([line(gross_pay=45000.0)]), percent_raise=10)
    assert result["error"] is True and result["code"] == "estimate_unavailable"


async def test_a_denied_config_read_is_the_tools_own_error():
    result = await estimate(FakeAgentSwitch([line()], denied=("ESIConfig.list",)), percent_raise=10)
    assert result["error"] is True and result["code"] == -32001


def test_the_capability_is_registered_and_validates_its_arguments():
    registry = default_registry()
    assert "salary_whatif" in registry and registry.get("salary_whatif").side_effect is False
    ok = registry.validate("salary_whatif", {"jurisdiction": "IN", "employee_id": "E1", "percent_raise": 9.5},
                           known_values={"E1"})
    assert ok["percent_raise"] == 9.5
    for bad in ({"percent_raise": "ten"}, {"percent_raise": -100}, {"new_monthly_gross": 0}):
        try:
            registry.validate("salary_whatif", {"jurisdiction": "IN", "employee_id": "E1", **bad},
                              known_values={"E1"})
        except CapabilityError:
            continue
        raise AssertionError(f"{bad} should have been rejected")

"""Implementation scaffolding for the statutory_dues and lifecycle_report workers and capabilities.

NOT the team's graded tests. A fake AgentSwitch client stands in for the platform so fetching,
filtering and error handling can be checked offline.
"""
from __future__ import annotations

import pytest

from payroll_agent import workers
from payroll_agent.agentswitch import AgentSwitchToolError
from payroll_agent.capabilities import CapabilityError, default_registry
from payroll_agent.core.live_graph import TaskSpec


class FakeAgentSwitch:
    """``tables`` maps an entity name to its rows; ``<Entity>.list`` filters on employee_id/payrun_id."""

    def __init__(self, *, runs, tables, short_total=None, denied=()):
        self.runs, self.tables = {r["id"]: r for r in runs}, tables
        self.short_total, self.denied = short_total, set(denied)
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
        entity, _, action = name.partition(".")
        if action != "list" or entity not in self.tables:
            raise AssertionError(f"these reads must stay read-only; unexpected tool {name}")
        rows = [r for r in self.tables[entity]
                if all(r.get(k) == arguments[k] for k in ("employee_id", "payrun_id") if k in arguments)]
        offset, limit = arguments.get("offset", 0), min(arguments.get("limit", 10), 100)
        total = len(rows) if self.short_total is None else self.short_total
        return {"data": rows[offset:offset + limit], "limit": limit, "offset": offset, "total": total}


def run(rid, end, **header):
    return {"id": rid, "status": "paid", "pay_period_start": end[:8] + "01", "pay_period_end": end, **header}


def slip(rid, **fields):
    return {"id": f"{rid}-{fields.get('employee_id', 'a')}", "payrun_id": rid, "employee_id": "a",
            "employee_name": "A", "gross_pay": 1000.0, "net_pay": 900.0, "esi_covered": 0, **fields}


IN_RUN = run("R1", "2026-07-31", total_epf_employee=100, total_tds=50)
IN_TABLES = {
    "PayRunEmployee": [slip("R1", epf_employee=100, tds=50)],
    "Employee": [{"id": "a", "first_name": "Asha", "last_name": "Patil"}],
    "EPFConfig": [{"employee_contribution_rate": 12.0}], "ESIConfig": [{"wage_ceiling": 21000.0}],
    "PTConfig": [{"deduction_cycle": "monthly", "annual_cap": 2500.0}], "LWFConfig": [{"deduction_cycle": "half_yearly"}],
}


def dues(fake, jurisdiction="IN", payrun_id="R1"):
    ctx = workers.RunContext(run_id="r", store=None, llm=None, agentswitch=fake)
    return workers.run_statutory_dues(ctx, TaskSpec("d", "statutory_dues", {"jurisdiction": jurisdiction, "payrun_id": payrun_id}))


def called(fake):
    return [name for name, _ in fake.calls]


async def test_india_dues_are_built_from_the_run_and_the_configs():
    fake = FakeAgentSwitch(runs=[IN_RUN], tables=IN_TABLES)
    result = await dues(fake)
    assert result["calculated"] is True and result["consistency"]["matches"] is True
    assert [s["statute"] for s in result["statutes"]] == ["EPF", "TDS"]
    assert result["config"]["ESIConfig"]["wage_ceiling"] == 21000.0
    assert set(called(fake)) == {"PayRun.get", "PayRunEmployee.list", "EPFConfig.list", "ESIConfig.list",
                                 "PTConfig.list", "LWFConfig.list"}


async def test_us_dues_fetch_no_india_configs():
    us_run = run("U1", "2026-08-31", total_deductions=100)
    rows = [slip("U1", deductions=[{"component_name": "Federal Withholding", "amount": 100}])]
    fake = FakeAgentSwitch(runs=[us_run], tables={"PayRunEmployee": rows})
    result = await dues(fake, jurisdiction="US", payrun_id="U1")
    assert [s["statute"] for s in result["statutes"] if s["available"]] == ["Federal income tax"]
    assert set(called(fake)) == {"PayRun.get", "PayRunEmployee.list"}


async def test_an_uncalculated_run_fetches_no_configs():
    fake = FakeAgentSwitch(runs=[IN_RUN], tables={**IN_TABLES, "PayRunEmployee": [slip("R1", net_pay=0)]})
    result = await dues(fake)
    assert result["calculated"] is False and "statutes" not in result
    assert not any(name.endswith("Config.list") for name in called(fake))


async def test_a_failing_config_fetch_only_skips_that_config():
    fake = FakeAgentSwitch(runs=[IN_RUN], tables=IN_TABLES, denied=("ESIConfig.list",))
    result = await dues(fake)
    assert result["calculated"] is True and result["statutes"]
    assert any(item["what"] == "ESIConfig" and "denied" in item["reason"] for item in result["skipped"])
    assert "ESIConfig" not in result["config"]


async def test_a_short_fetch_is_scan_incomplete_named_for_the_capability():
    result = await dues(FakeAgentSwitch(runs=[IN_RUN], tables=IN_TABLES, short_total=50))
    assert result["error"] is True and result["code"] == "scan_incomplete" and result["tool"] == "statutory_dues"


async def test_a_missing_run_returns_the_tools_own_error():
    result = await dues(FakeAgentSwitch(runs=[IN_RUN], tables=IN_TABLES), payrun_id="nope")
    assert result["error"] is True and result["tool"] == "PayRun.get"


LIFE_TABLES = {
    "EmployeeLoan": [{"id": "L1", "number": "LOAN-1", "employee_id": "a", "status": "draft", "loan_amount": -1.0},
                     {"id": "L2", "number": "LOAN-2", "employee_id": "b", "status": "draft", "loan_amount": 100.0}],
    "LoanRepayment": [{"loan_id": "L1", "employee_id": "a", "date": "2030-01-31", "status": "scheduled",
                       "principal_amount": 1.0, "total_amount": 2.0}],
    "Employee": [{"id": "a", "first_name": "Asha", "last_name": "Patil"}],
    "SalaryRevision": [{"number": "REV-1", "employee_id": "a", "status": "pending_approval", "effective_date": "2020-01-01"}],
}


def life(fake, topic, employee_id=None):
    ctx = workers.RunContext(run_id="r", store=None, llm=None, agentswitch=fake)
    inp = {"jurisdiction": "IN", "topic": topic, **({"employee_id": employee_id} if employee_id else {})}
    return workers.run_lifecycle_report(ctx, TaskSpec("l", "lifecycle_report", inp))


async def test_loans_report_reads_loans_repayments_and_employee_names_only():
    fake = FakeAgentSwitch(runs=[], tables=LIFE_TABLES)
    result = await life(fake, "loans")
    assert result["topic"] == "loans" and result["total"] == 2
    assert {c["check"] for c in result["data_quality"]["checks"]} == {"loan_amount_not_positive"}
    assert set(called(fake)) == {"EmployeeLoan.list", "LoanRepayment.list", "Employee.list"}


async def test_an_employee_filter_is_sent_to_the_list_tools_and_applied():
    fake = FakeAgentSwitch(runs=[], tables=LIFE_TABLES)
    result = await life(fake, "loans", employee_id="a")
    assert result["total"] == 1 and result["items"][0]["employee_name"] == "Asha Patil"
    sent = [args for name, args in fake.calls if name in ("EmployeeLoan.list", "LoanRepayment.list")]
    assert sent and all(args["employee_id"] == "a" for args in sent)


async def test_revisions_report_flags_a_backdated_pending_revision():
    result = await life(FakeAgentSwitch(runs=[], tables=LIFE_TABLES), "revisions")
    assert result["backdated_pending"]["count"] == 1 and result["pending"][0]["employee_name"] == "Asha Patil"


async def test_a_denied_employee_list_keeps_its_own_error():
    result = await life(FakeAgentSwitch(runs=[], tables=LIFE_TABLES, denied=("Employee.list",)), "loans")
    assert result["error"] is True and result["tool"] == "Employee.list" and result["code"] == -32001


async def test_a_short_lifecycle_fetch_is_named_for_the_capability():
    result = await life(FakeAgentSwitch(runs=[], tables=LIFE_TABLES, short_total=50), "loans")
    assert result["code"] == "scan_incomplete" and result["tool"] == "lifecycle_report"


def test_capabilities_are_registered_read_only_with_id_provenance():
    registry = default_registry()
    for name in ("statutory_dues", "lifecycle_report"):
        assert registry.get(name).side_effect is False
        assert name in registry.family("evidence") and name in workers._WORKERS
    registry.validate("statutory_dues", {"jurisdiction": "US", "payrun_id": "R1"}, known_values={"R1"})
    with pytest.raises(CapabilityError):
        registry.validate("statutory_dues", {"jurisdiction": "US", "payrun_id": "R1"}, known_values=set())
    clean = registry.validate("lifecycle_report", {"jurisdiction": "IN", "topic": "loans", "employee_id": "a"},
                              known_values={"a"})
    assert clean["topic"] == "loans"


@pytest.mark.parametrize("bad", [{"topic": "payslips"}, {"topic": "loans", "employee_id": "ghost"}, {}])
def test_lifecycle_rejects_bad_arguments(bad):
    with pytest.raises(CapabilityError):
        default_registry().validate("lifecycle_report", {"jurisdiction": "IN", **bad}, known_values={"a"})

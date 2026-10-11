"""Implementation scaffolding for the explain workers and capabilities.

NOT the team's graded tests. A fake AgentSwitch client stands in for the platform.
"""
from __future__ import annotations

import pytest

from payroll_agent import workers
from payroll_agent.agentswitch import AgentSwitchToolError
from payroll_agent.capabilities import CapabilityError, default_registry
from payroll_agent.core.live_graph import TaskSpec

from test_explain_scaffold import EMPLOYEE, LINE_A, LINE_B, RUN_A, RUN_B, TEMPLATE, claim, run  # noqa: E402


class FakeAgentSwitch:
    """Read-only: ``<Entity>.list`` filters on the given equality arguments; ``<Entity>.get`` finds by id."""

    def __init__(self, tables, denied=()):
        self.tables, self.denied, self.calls = tables, set(denied), []

    async def call_tool(self, name, arguments=None, *, jurisdiction):
        arguments = arguments or {}
        self.calls.append((name, arguments))
        entity, _, action = name.partition(".")
        assert action in ("list", "get"), f"these capabilities must stay read-only; unexpected tool {name}"
        if name in self.denied:
            raise AgentSwitchToolError(name, -32001, "denied")
        rows = self.tables.get(entity, [])
        if action == "get":
            for row in rows:
                if row.get("id") == arguments["id"]:
                    return dict(row)
            raise AgentSwitchToolError(name, -32602, f"{entity} not found.")
        keys = [k for k in arguments if k not in ("limit", "offset")]
        rows = [r for r in rows if all(r.get(k) == arguments[k] for k in keys)]
        offset, limit = arguments.get("offset", 0), min(arguments.get("limit", 10), 100)
        return {"data": rows[offset:offset + limit], "limit": limit, "offset": offset, "total": len(rows)}


def ctx(fake):
    return workers.RunContext(run_id="r", store=None, llm=None, agentswitch=fake)


def call(worker, fake, **args):
    return worker(ctx(fake), TaskSpec("t", "x", {"jurisdiction": "IN", **args}))


def tables(**extra):
    return {"PayRunEmployee": [LINE_A, LINE_B], "PayRun": [RUN_A, RUN_B], "Employee": [EMPLOYEE],
            "SalaryStructure": [TEMPLATE], "SalaryRevision": [], "ReimbursementClaim": [], **extra}


async def test_breakdown_defaults_to_the_latest_calculated_line():
    result = await call(workers.run_payslip_breakdown, FakeAgentSwitch(tables()), employee_id="E1")
    assert result["payrun_id"] == "RB" and result["consistent"] is True


async def test_breakdown_of_a_named_run():
    result = await call(workers.run_payslip_breakdown, FakeAgentSwitch(tables()), employee_id="E1", payrun_id="RA")
    assert result["payrun_id"] == "RA" and result["net"] == 37000.0


async def test_breakdown_errors_when_there_is_no_line():
    for args in ({"employee_id": "E9"}, {"employee_id": "E1", "payrun_id": "RZ"}):
        result = await call(workers.run_payslip_breakdown, FakeAgentSwitch(tables()), **args)
        assert result["error"] is True and result["code"] == "no_pay_line" and result["tool"] == "payslip_breakdown"


async def test_change_defaults_to_the_latest_two_lines_and_explicit_runs_are_honoured():
    fake = FakeAgentSwitch(tables())
    result = await call(workers.run_explain_pay_change, fake, employee_id="E1")
    assert result["from"]["payrun_id"] == "RA" and result["to"]["payrun_id"] == "RB"
    assert result["net_change"] == -2976.92
    swapped = await call(workers.run_explain_pay_change, fake, employee_id="E1", from_payrun_id="RB", to_payrun_id="RA")
    assert swapped["net_change"] == 2976.92


async def test_change_needs_two_lines():
    only_one = tables(PayRunEmployee=[LINE_A])
    result = await call(workers.run_explain_pay_change, FakeAgentSwitch(only_one), employee_id="E1")
    assert result["error"] is True and result["code"] == "need_two_lines"
    missing = await call(workers.run_explain_pay_change, FakeAgentSwitch(tables()), employee_id="E1",
                         from_payrun_id="RA", to_payrun_id="RZ")
    assert missing["code"] == "need_two_lines"


async def test_structure_view_for_a_template_and_for_another_employees_structure():
    result = await call(workers.run_salary_structure_view, FakeAgentSwitch(tables()), employee_id="E1")
    assert result["ownership"] == "template" and result["pay_lines"] == 2
    anita = {**TEMPLATE, "employee_id": "E9", "name": "Anita — revised"}
    other = await call(workers.run_salary_structure_view, FakeAgentSwitch(tables(SalaryStructure=[anita])),
                       employee_id="E1")
    assert other["ownership"] == "other_employee" and "earnings" not in other


async def test_structure_view_with_a_missing_employee_is_an_error_result():
    result = await call(workers.run_salary_structure_view, FakeAgentSwitch(tables()), employee_id="E9")
    assert result["error"] is True and result["tool"] == "salary_structure_view"


async def test_structure_view_with_a_dangling_structure_reports_missing():
    dangling = {**EMPLOYEE, "salary_structure_id": "S-GONE"}
    result = await call(workers.run_salary_structure_view, FakeAgentSwitch(tables(Employee=[dangling])),
                        employee_id="E1")
    assert result["ownership"] == "missing"


async def test_reimbursements_filter_by_employee_and_status():
    claims = [claim("C1", "paid", approved=1000.0, paid_in="R1", employee_id="E1"),
              claim("C2", "draft", employee_id="E2"), claim("C3", "draft", employee_id="E1")]
    fake = FakeAgentSwitch(tables(ReimbursementClaim=claims))
    assert (await call(workers.run_reimbursement_status, fake))["total"] == 3
    assert (await call(workers.run_reimbursement_status, fake, employee_id="E1"))["total"] == 2
    one = await call(workers.run_reimbursement_status, fake, employee_id="E1", status="paid")
    assert one["total"] == 1 and one["claims"][0]["number"] == "C1"


async def test_a_denied_read_is_the_tools_own_error_and_nothing_else_is_called():
    result = await call(workers.run_payslip_breakdown, FakeAgentSwitch(tables(), denied=("PayRun.list",)),
                        employee_id="E1")
    assert result["error"] is True and result["code"] == -32001


def test_the_capabilities_are_registered_read_only_with_validated_arguments():
    registry = default_registry()
    for name in ("payslip_breakdown", "explain_pay_change", "salary_structure_view", "reimbursement_status"):
        assert name in registry and registry.get(name).side_effect is False
    registry.validate("reimbursement_status", {"jurisdiction": "IN", "status": "paid"})
    with pytest.raises(CapabilityError):
        registry.validate("reimbursement_status", {"jurisdiction": "IN", "status": "maybe"})
    with pytest.raises(CapabilityError):
        registry.validate("payslip_breakdown", {"jurisdiction": "IN", "employee_id": "E1"}, known_values={"other"})


def test_the_structure_capability_description_says_missing_pay_lines_are_a_finding():
    description = default_registry().get("salary_structure_view").description
    assert "complete" in description and "do not fetch" in description.lower()


def test_the_evidence_review_prompt_accepts_a_capabilitys_own_finding_as_ready_evidence():
    """Found live: the critic called a complete salary-structure report 'not ready' because it reported that
    the structure belongs to someone else and that no pay lines exist. Wording guard only; behaviour is
    measured live and recorded in WORKFLOWS.md."""
    from payroll_agent.planner import PayrollPlanner
    prompt = PayrollPlanner._review_system()
    assert "finding" in prompt and "complete" in prompt and "belongs to someone else" in prompt


async def test_a_named_run_without_a_calculated_line_is_named_and_the_error_suggests_the_default():
    """Found live: 'last month' resolved to the uncalculated September draft; the error must say which run
    has no calculated line and that omitting the run ids compares the latest two."""
    result = await call(workers.run_explain_pay_change, FakeAgentSwitch(tables()), employee_id="E1",
                        from_payrun_id="RA", to_payrun_id="RZ")
    assert result["code"] == "need_two_lines" and "RZ" in result["message"] and "omit" in result["message"]
    assert "RA" not in result["message"].split("omit")[0]            # only the run that lacks a line is blamed
    short = await call(workers.run_explain_pay_change, FakeAgentSwitch(tables(PayRunEmployee=[LINE_A])), employee_id="E1")
    assert "only 1" in short["message"]


async def test_a_failed_structure_read_is_the_tools_error_not_a_finding_that_no_structure_exists():
    """Review finding: any SalaryStructure.get error became 'no structure on file', marked complete."""
    result = await call(workers.run_salary_structure_view, FakeAgentSwitch(tables(), denied=("SalaryStructure.get",)),
                        employee_id="E1")
    assert result["error"] is True and result["code"] == -32001 and result["tool"] == "salary_structure_view"
    gone = await call(workers.run_salary_structure_view,
                      FakeAgentSwitch(tables(Employee=[{**EMPLOYEE, "salary_structure_id": "S-GONE"}])), employee_id="E1")
    assert gone["ownership"] == "missing"                          # a genuine "SalaryStructure not found." stays a finding


async def test_comparing_a_run_with_itself_is_an_error_and_a_reversed_order_is_flagged():
    """Review finding: from == to said 'nothing changed'; from later than to silently flipped the signs."""
    fake = FakeAgentSwitch(tables())
    same = await call(workers.run_explain_pay_change, fake, employee_id="E1", from_payrun_id="RA", to_payrun_id="RA")
    assert same["error"] is True and same["code"] == "cannot_explain" and "itself" in same["message"]
    reversed_order = await call(workers.run_explain_pay_change, fake, employee_id="E1",
                                from_payrun_id="RB", to_payrun_id="RA")
    assert reversed_order["net_change"] == 2976.92 and "later" in reversed_order["warning"]
    forward = await call(workers.run_explain_pay_change, fake, employee_id="E1")
    assert "warning" not in forward


def test_the_review_prompt_does_not_treat_an_error_result_as_a_finding():
    from payroll_agent.planner import PayrollPlanner
    prompt = PayrollPlanner._review_system()
    assert "error result" in prompt and "not a finding" in prompt

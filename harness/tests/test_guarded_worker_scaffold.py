"""Implementation scaffolding for the guarded action worker and capabilities.

NOT the team's graded tests. A fake AgentSwitch client models records, the platform's legal
transitions and the effect of each action tool, so every guard can be checked without touching
real data. The action tool is never called by any test that expects a refusal.
"""
from __future__ import annotations

import pytest

from evals.transport import ScriptedLLM
from payroll_agent import guarded, workers
from payroll_agent.agentswitch import AgentSwitchToolError
from payroll_agent.capabilities import CapabilityError, default_registry
from payroll_agent.core.live_graph import TaskSpec
from payroll_agent.run import run_goal

LEGAL = {("EmployeeLoan", "draft"): [{"action": "Submit", "from": "draft", "to": "pending_approval"},
                                     {"action": "Cancel", "from": "draft", "to": "cancelled"}],
         ("SalaryRevision", "approved"): [{"action": "Apply", "from": "approved", "to": "applied"}],
         ("PayRun", "draft"): [{"action": "Calculate Payroll", "from": "draft", "to": "review"},
                               {"action": "Cancel", "from": "draft", "to": "cancelled"}]}
EFFECTS = {"EmployeeLoan.submit": ("EmployeeLoan", "pending_approval"),
           "EmployeeLoan.cancel.draft.cancelled": ("EmployeeLoan", "cancelled"),
           "SalaryRevision.apply": ("SalaryRevision", "applied"),
           "PayRun.calculate_payroll": ("PayRun", "review"),
           "PayRun.cancel.draft.cancelled": ("PayRun", "cancelled")}


class FakeAgentSwitch:
    def __init__(self, records, *, stuck=(), failing=(), stale=None, short_total=None):
        self.records = {entity: [dict(r) for r in rows] for entity, rows in records.items()}
        self.stuck, self.failing, self.stale, self.short_total = set(stuck), set(failing), stale or {}, short_total
        self.calls: list[tuple[str, dict]] = []

    @property
    def acted(self):
        return [name for name, _ in self.calls if name.rpartition(".")[2] not in ("list", "get")]

    async def close(self):
        return None

    def _row(self, entity, rid):
        return next(r for r in self.records[entity] if r["id"] == rid)

    async def call_tool(self, name, arguments=None, *, jurisdiction):
        arguments = arguments or {}
        self.calls.append((name, arguments))
        entity, _, action = name.partition(".")
        if name in self.failing:
            raise AgentSwitchToolError(name, -32002, "platform refused")
        if action == "list":
            rows = self.records.get(entity, [])
            rows = [r for r in rows if all(r.get(k) == v for k, v in arguments.items() if k == "payrun_id")]
            offset, limit = arguments.get("offset", 0), min(arguments.get("limit", 10), 100)
            total = len(rows) if self.short_total is None else self.short_total
            return {"data": rows[offset:offset + limit], "limit": limit, "offset": offset, "total": total}
        if action == "get":
            row = {**self._row(entity, arguments["id"]), **self.stale.get((entity, arguments["id"]), {})}
            return {**row, "_transitions": LEGAL.get((entity, row["status"]), [])}
        if name in EFFECTS:
            target_entity, new_status = EFFECTS[name]
            if name not in self.stuck:
                self._row(target_entity, arguments["id"])["status"] = new_status
            return {"id": arguments["id"], "ok": True}
        if entity == "PayRun" and action in ("generate_payslips", "send_payslips"):
            return {"id": arguments["id"], "generated": True, "slips": [1, 2]}
        raise AssertionError(f"unexpected tool {name}")


def loan(**fields):
    return {"id": "L1", "number": "LOAN-1", "status": "draft", "loan_amount": 1000.0, "emi_amount": 100.0,
            "tenure_months": 12.0, **fields}


def act(fake, name, reference="LOAN-1"):
    ctx = workers.RunContext(run_id="r", store=None, llm=None, agentswitch=fake)
    return workers._WORKERS[name](ctx, TaskSpec("a", name, {"jurisdiction": "IN", "reference": reference}))


async def test_a_valid_submit_calls_the_tool_once_and_verifies_the_new_status():
    fake = FakeAgentSwitch({"EmployeeLoan": [loan()]})
    result = await act(fake, "submit_loan")
    assert result == {"performed": True, "action": "submit_loan", "entity": "EmployeeLoan", "number": "LOAN-1",
                      "id": "L1", "before": "draft", "after": "pending_approval"}
    assert fake.acted == ["EmployeeLoan.submit"]


@pytest.mark.parametrize("fields, code", [
    ({"status": "cancelled"}, "not_in_required_state"),
    ({"status": "pending_approval"}, "already_in_target_state"),
    ({"loan_amount": -1.0}, "precondition_failed"),
])
async def test_a_failed_guard_refuses_and_never_calls_the_action_tool(fields, code):
    fake = FakeAgentSwitch({"EmployeeLoan": [loan(**fields)]})
    result = await act(fake, "submit_loan")
    assert result["error"] is True and result["code"] == code and result["tool"] == "EmployeeLoan.submit"
    assert result["message"].endswith("nothing was changed") and result["record"]["number"] == "LOAN-1"
    assert fake.acted == []


async def test_a_move_the_platform_does_not_list_is_refused(monkeypatch):
    monkeypatch.setitem(LEGAL, ("EmployeeLoan", "draft"), [])
    fake = FakeAgentSwitch({"EmployeeLoan": [loan()]})
    result = await act(fake, "submit_loan")
    assert result["code"] == "transition_not_available" and fake.acted == []


async def test_a_record_that_changed_after_the_list_is_judged_on_the_reread():
    fake = FakeAgentSwitch({"EmployeeLoan": [loan()]}, stale={("EmployeeLoan", "L1"): {"status": "cancelled"}})
    result = await act(fake, "submit_loan")
    assert result["code"] == "not_in_required_state" and fake.acted == []


async def test_a_reference_with_no_match_or_two_matches_never_acts():
    missing = FakeAgentSwitch({"EmployeeLoan": [loan()]})
    assert (await act(missing, "submit_loan", "LOAN-404"))["code"] == "record_not_found" and missing.acted == []
    twice = FakeAgentSwitch({"EmployeeLoan": [loan(), loan(id="L2")]})
    result = await act(twice, "submit_loan")
    assert result["code"] == "ambiguous_reference" and twice.acted == []


async def test_a_record_can_be_named_by_id():
    fake = FakeAgentSwitch({"EmployeeLoan": [loan()]})
    assert (await act(fake, "submit_loan", "L1"))["performed"] is True


async def test_a_tool_error_is_returned_unchanged():
    fake = FakeAgentSwitch({"EmployeeLoan": [loan()]}, failing=("EmployeeLoan.submit",))
    result = await act(fake, "submit_loan")
    assert result["error"] is True and result["tool"] == "EmployeeLoan.submit" and result["code"] == -32002


async def test_a_tool_that_returns_without_changing_the_status_is_reported():
    fake = FakeAgentSwitch({"EmployeeLoan": [loan()]}, stuck=("EmployeeLoan.submit",))
    result = await act(fake, "submit_loan")
    assert result["error"] is True and result["code"] == "status_unchanged"
    assert result["record"]["status"] == "draft" and fake.acted == ["EmployeeLoan.submit"]


async def test_a_short_entity_list_is_scan_incomplete_named_for_the_capability():
    result = await act(FakeAgentSwitch({"EmployeeLoan": [loan()]}, short_total=50), "submit_loan")
    assert result["code"] == "scan_incomplete" and result["tool"] == "submit_loan"


async def test_applying_a_revision_needs_an_approved_non_rejected_record():
    base = {"id": "R1", "number": "REV-1", "status": "approved", "revised_ctc": 100.0, "approval_status": "approved"}
    ok = FakeAgentSwitch({"SalaryRevision": [dict(base)]})
    assert (await act(ok, "apply_salary_revision", "REV-1"))["after"] == "applied"
    rejected = FakeAgentSwitch({"SalaryRevision": [{**base, "approval_status": "rejected"}]})
    result = await act(rejected, "apply_salary_revision", "REV-1")
    assert result["code"] == "precondition_failed" and rejected.acted == []


async def test_payrun_actions_calculate_and_cancel_a_draft_only():
    run = {"id": "P1", "number": "PRUN-1", "status": "draft"}
    assert (await act(FakeAgentSwitch({"PayRun": [dict(run)]}), "calculate_payrun", "PRUN-1"))["after"] == "review"
    assert (await act(FakeAgentSwitch({"PayRun": [dict(run)]}), "cancel_draft_payrun", "PRUN-1"))["after"] == "cancelled"
    pending = FakeAgentSwitch({"PayRun": [{**run, "status": "pending_approval"}]})
    assert (await act(pending, "calculate_payrun", "PRUN-1"))["code"] == "not_in_required_state" and pending.acted == []


async def test_payslip_actions_need_calculated_rows_and_return_a_digest():
    run = {"id": "P1", "number": "PRUN-1", "status": "review"}
    rows = [{"id": "s1", "payrun_id": "P1", "net_pay": 900.0}]
    ok = FakeAgentSwitch({"PayRun": [dict(run)], "PayRunEmployee": rows})
    result = await act(ok, "generate_payslips", "PRUN-1")
    assert result["performed"] is True and result["result"] == {"id": "P1", "generated": True, "slips": {"count": 2}}
    empty = FakeAgentSwitch({"PayRun": [dict(run)], "PayRunEmployee": [{"id": "s1", "payrun_id": "P1", "net_pay": 0}]})
    refused = await act(empty, "send_payslips", "PRUN-1")
    assert refused["code"] == "run_not_calculated" and empty.acted == []
    draft = FakeAgentSwitch({"PayRun": [{**run, "status": "draft"}], "PayRunEmployee": rows})
    assert (await act(draft, "generate_payslips", "PRUN-1"))["code"] == "not_in_required_state"


def test_every_action_is_a_registered_side_effect_capability_with_a_worker():
    registry = default_registry()
    for name, action in guarded.ACTIONS.items():
        capability = registry.get(name)
        assert capability.side_effect is True and name in workers._WORKERS
        assert set(capability.arguments) == {"jurisdiction", "reference"}
        assert capability.description == action.capability_description()
    registry.validate("submit_loan", {"jurisdiction": "IN", "reference": "LOAN-1"}, known_values={"LOAN-1"})
    with pytest.raises(CapabilityError):
        registry.validate("submit_loan", {"jurisdiction": "IN", "reference": "LOAN-1"}, known_values=set())


def plan(capability, reference="LOAN-1"):
    return {"add": [{"id": "act", "capability": capability, "arguments": {"jurisdiction": "IN", "reference": reference},
                     "depends_on": []}], "cancel": [], "finish": False, "reason": "do it"}


DECLINE = {"add": [{"id": "decline", "capability": "decline_request",
                    "arguments": {"reason_code": "not_permitted", "explanation": "that action is not authorised"},
                    "depends_on": []}], "cancel": [], "finish": False, "reason": "not authorised"}
ANSWER = {"add": [{"id": "answer", "capability": "answer_with_evidence", "arguments": {"query": "done?"},
                   "depends_on": ["act"]}], "cancel": [], "finish": False, "reason": "answer"}


async def run_with(steps, fake, allow):
    llm = ScriptedLLM(steps)
    result = await run_goal("Submit loan LOAN-1 for approval", allowed_side_effects=allow,
                            initial_evidence={"jurisdiction": "IN"}, llm=llm, agentswitch=fake)
    return llm, result


async def test_an_action_without_authority_is_rejected_by_the_planner_and_never_runs():
    fake = FakeAgentSwitch({"EmployeeLoan": [loan()]})
    llm, result = await run_with([{"reply": plan("submit_loan")}, {"reply": DECLINE}], fake, allow=set())
    assert len(llm.calls) == 2 and fake.acted == [] and result["declined"] is True


async def test_an_authorised_action_runs_through_the_planner_once():
    fake = FakeAgentSwitch({"EmployeeLoan": [loan()]})
    steps = [{"reply": plan("submit_loan")}, {"reply": ANSWER}, {"reply": {"ready": True, "missing": [], "reason": "ok"}},
             {"reply": "The loan was submitted."}]
    _, result = await run_with(steps, fake, allow={"submit_loan"})
    assert fake.acted == ["EmployeeLoan.submit"] and result["answer"] == "The loan was submitted."
    assert result["nodes"]["act"]["result"]["performed"] is True

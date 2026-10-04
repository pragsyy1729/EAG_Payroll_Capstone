"""Implementation scaffolding for payroll_agent/guarded.py.

NOT the team's graded tests. These only check that the code behaves as
designed while it is being built.
"""
from __future__ import annotations

import pytest

from payroll_agent import guarded as g

SUBMIT = {"action": "Submit", "from": "draft", "to": "pending_approval"}
LOAN = {"id": "L1", "number": "LOAN-1", "status": "draft", "loan_amount": 1000.0, "emi_amount": 100.0,
        "tenure_months": 12.0, "_transitions": [SUBMIT]}


def test_the_table_has_the_eleven_actions_with_consistent_fields():
    assert set(g.ACTIONS) == {
        "calculate_payrun", "generate_payslips", "send_payslips", "cancel_draft_payrun", "submit_loan",
        "cancel_draft_loan", "submit_salary_revision", "apply_salary_revision", "calculate_settlement",
        "submit_final_settlement", "submit_investment_declaration"}
    for name, action in g.ACTIONS.items():
        assert action.name == name and action.from_statuses and action.tool.startswith(action.entity + ".")
        assert action.example and "NOT performed" in action.capability_description()
    assert {n for n, a in g.ACTIONS.items() if a.to_status is None} == {"generate_payslips", "send_payslips"}
    assert g.ACTIONS["calculate_settlement"].from_statuses == ("draft",)


def test_a_valid_record_has_no_refusal():
    assert g.refusal_for(g.ACTIONS["submit_loan"], LOAN) is None


def test_already_in_the_target_state():
    out = g.refusal_for(g.ACTIONS["submit_loan"], {**LOAN, "status": "pending_approval"})
    assert out["code"] == "already_in_target_state" and "pending_approval" in out["message"]


def test_a_status_the_action_does_not_accept():
    out = g.refusal_for(g.ACTIONS["submit_loan"], {**LOAN, "status": "cancelled"})
    assert out["code"] == "not_in_required_state" and "draft" in out["message"]


def test_the_platform_must_list_the_move_as_legal_now():
    for transitions in ([], None, [{"action": "Cancel", "from": "draft", "to": "cancelled"}], ["junk"]):
        out = g.refusal_for(g.ACTIONS["submit_loan"], {**LOAN, "_transitions": transitions})
        assert out["code"] == "transition_not_available"


@pytest.mark.parametrize("field", ["loan_amount", "emi_amount", "tenure_months"])
def test_loan_guard_needs_positive_amounts(field):
    for bad in (0, -1.0, None, "x", float("nan")):
        out = g.refusal_for(g.ACTIONS["submit_loan"], {**LOAN, field: bad})
        assert out["code"] == "precondition_failed" and field in out["message"]


def test_apply_revision_guard_needs_ctc_and_a_non_rejected_approval():
    apply = {"id": "R1", "number": "REV-1", "status": "approved", "revised_ctc": 100.0, "approval_status": "approved",
             "_transitions": [{"action": "Apply", "from": "approved", "to": "applied"}]}
    assert g.refusal_for(g.ACTIONS["apply_salary_revision"], apply) is None
    assert g.refusal_for(g.ACTIONS["apply_salary_revision"], {**apply, "revised_ctc": 0})["code"] == "precondition_failed"
    rejected = g.refusal_for(g.ACTIONS["apply_salary_revision"], {**apply, "approval_status": "rejected"})
    assert rejected["code"] == "precondition_failed" and "rejected" in rejected["message"]


def test_payslip_actions_check_the_status_but_not_the_transition_list():
    run = {"id": "P1", "number": "PRUN-1", "status": "review", "_transitions": []}
    assert g.refusal_for(g.ACTIONS["generate_payslips"], run) is None
    assert g.refusal_for(g.ACTIONS["send_payslips"], {**run, "status": "draft"})["code"] == "not_in_required_state"


def test_summary_keeps_only_identity():
    assert g.summary(LOAN) == {"id": "L1", "number": "LOAN-1", "status": "draft"}


def test_outcome_reports_a_performed_status_move():
    out = g.outcome(g.ACTIONS["submit_loan"], LOAN, {**LOAN, "status": "pending_approval"}, {"id": "L1"})
    assert out == {"performed": True, "action": "submit_loan", "entity": "EmployeeLoan", "number": "LOAN-1",
                   "id": "L1", "before": "draft", "after": "pending_approval"}


def test_outcome_flags_a_tool_that_returned_without_changing_the_status():
    out = g.outcome(g.ACTIONS["submit_loan"], LOAN, LOAN, {"id": "L1"})
    assert out["error"] is True and out["code"] == "status_unchanged"
    assert out["record"]["status"] == "draft" and out["tool"] == "EmployeeLoan.submit"


def test_outcome_for_a_payslip_action_carries_a_digest_of_the_tool_result():
    run = {"id": "P1", "number": "PRUN-1", "status": "review"}
    out = g.outcome(g.ACTIONS["generate_payslips"], run, run, {"generated": True, "slips": [1, 2, 3], "meta": {"a": 1},
                                                                "_x": 1, "note": "n" * 300})
    assert out["performed"] is True and out["before"] == out["after"] == "review"
    assert out["result"] == {"generated": True, "slips": {"count": 3}, "meta": {"keys": 1}, "note": "n" * 200}


def test_digest_handles_a_payload_that_is_not_a_dict():
    assert g.digest("ok") == {"value": "ok"}

"""Implementation scaffolding for the employee "why" answers.

NOT the team's graded tests. Pure functions only, so the rules can be checked offline.
"""
from __future__ import annotations

import json

import pytest

from payroll_agent import explain


def run(rid, **fields):
    base = {"id": rid, "number": f"PRUN-{rid}", "run_type": "regular", "status": "paid",
            "pay_period": f"Period {rid}", "pay_period_start": "2026-08-01"}
    return {**base, **fields}


def comp(name, amount):
    return {"component_name": name, "amount": amount}


LINE_A = {"id": "LA", "payrun_id": "RA", "employee_id": "E1", "employee_name": "Asha Patil",
          "created_at": "2026-08-30T10:00:00", "days_payable": 26.0, "working_days": 26.0, "lop_days": 0.0,
          "earnings": [comp("Basic", 20000.0), comp("HRA", 10000.0), comp("Special Allowance", 10000.0)],
          "deductions": [comp("EPF Employee", 1800.0), comp("Professional Tax", 200.0), comp("TDS", 1000.0)],
          "gross_pay": 40000.0, "total_deductions": 3000.0, "reimbursements": 0.0, "net_pay": 37000.0,
          "pf_wage": 15000.0, "tax_regime": "new", "taxable_earnings": 40000.0, "tds": 1000.0,
          "loan_deduction": 0.0, "other_deductions": 0.0, "overtime_hours": 0.0, "overtime_pay": 0.0}
LINE_B = {**LINE_A, "id": "LB", "payrun_id": "RB", "created_at": "2026-09-29T10:00:00",
          "days_payable": 24.0, "lop_days": 2.0,
          "earnings": [comp("Basic", 18461.54), comp("HRA", 9230.77), comp("Special Allowance", 9230.77)],
          "deductions": [comp("EPF Employee", 1800.0), comp("Professional Tax", 200.0), comp("TDS", 900.0)],
          "gross_pay": 36923.08, "total_deductions": 2900.0, "net_pay": 34023.08,
          "taxable_earnings": 36923.08, "tds": 900.0}
RUN_A = run("RA", pay_period_start="2026-08-01")
RUN_B = run("RB", pay_period_start="2026-09-01", status="draft")


def test_components_reads_a_json_string_or_a_list_and_rejects_bad_items():
    items = [comp("Basic", 100.0)]
    assert explain.components(items) == [{"name": "Basic", "amount": 100.0}]
    assert explain.components(json.dumps(items)) == [{"name": "Basic", "amount": 100.0}]
    assert explain.components([]) == []
    for bad in (None, "not json", "{}", [{"component_name": "", "amount": 1}], [{"component_name": "X"}],
                [{"component_name": "X", "amount": float("nan")}], [{"component_name": "X", "amount": True}], [5]):
        assert explain.components(bad) is None


def test_a_consistent_line_breaks_down_gross_to_net():
    result = explain.breakdown(LINE_A, RUN_A)
    assert result["consistent"] is True and result["problems"] == []
    assert [c["name"] for c in result["earnings"]] == ["Basic", "HRA", "Special Allowance"]
    assert [c["name"] for c in result["deductions"]] == ["EPF Employee", "Professional Tax", "TDS"]
    assert (result["gross"], result["total_deductions"], result["net"], result["expected_net"]) == (
        40000.0, 3000.0, 37000.0, 37000.0)
    assert result["payrun_number"] == "PRUN-RA" and result["run_status"] == "paid"


def test_reimbursements_are_part_of_the_net_identity():
    with_reimbursement = {**LINE_A, "reimbursements": 500.0, "net_pay": 37500.0}
    assert explain.breakdown(with_reimbursement)["consistent"] is True


def test_a_line_that_does_not_add_up_reports_each_failed_check():
    result = explain.breakdown({**LINE_A, "net_pay": 36000.0, "gross_pay": 41000.0})
    assert result["consistent"] is False
    joined = " ".join(result["problems"])
    assert "earnings_add_up_to_gross" in joined and "net_equals_gross_minus_deductions_plus_reimbursements" in joined


def test_unreadable_components_are_a_problem_not_a_crash():
    result = explain.breakdown({**LINE_A, "earnings": "garbage", "net_pay": None})
    assert result["consistent"] is False and result["earnings"] is None
    assert any("unreadable" in p for p in result["problems"])


def test_usable_lines_are_calculated_regular_non_cancelled_newest_first():
    runs = [RUN_A, RUN_B, run("RC", run_type="bonus"), run("RD", status="cancelled")]
    rows = [LINE_A, LINE_B, {**LINE_A, "id": "LC", "payrun_id": "RC"}, {**LINE_A, "id": "LD", "payrun_id": "RD"},
            {**LINE_A, "id": "LZ", "payrun_id": "RA", "net_pay": 0.0}, {**LINE_A, "id": "LU", "payrun_id": "RX"}]
    assert [row["id"] for _, row in explain.usable_lines(rows, runs)] == ["LB", "LA"]


def test_a_change_between_two_lines_gives_component_deltas_and_the_changed_facts():
    result = explain.change(to_run=RUN_B, to_row=LINE_B, from_run=RUN_A, from_row=LINE_A)
    assert (result["gross_change"], result["net_change"]) == (-3076.92, -2976.92)
    assert result["total_deductions_change"] == -100.0
    by_name = {d["component"]: d for d in result["earnings"]}
    assert by_name["Basic"]["change"] == -1538.46
    assert [d["component"] for d in result["deductions"]] == ["TDS"]          # unchanged lines are left out
    assert set(result["drivers_changed"]) == {"days_payable", "lop_days", "tds", "taxable_earnings"}
    assert "pf_wage" not in result["drivers_changed"]
    assert result["largest_movers"][0]["component"] == "Basic"
    assert result["from"]["payrun_number"] == "PRUN-RA" and result["to"]["net"] == 34023.08


def test_a_component_that_appears_or_disappears_is_reported():
    with_loan = {**LINE_B, "deductions": [*LINE_B["deductions"], comp("Loan EMI", 500.0)],
                 "total_deductions": 3400.0, "net_pay": 33523.08}
    result = explain.change(to_run=RUN_B, to_row=with_loan, from_run=RUN_A, from_row=LINE_A)
    loan = next(d for d in result["deductions"] if d["component"] == "Loan EMI")
    assert loan["before"] is None and loan["after"] == 500.0 and loan["change"] == 500.0


def test_comparing_two_employees_or_unreadable_lines_is_an_error():
    with pytest.raises(explain.ExplainError):
        explain.change(to_run=RUN_B, to_row={**LINE_B, "employee_id": "E2"}, from_run=RUN_A, from_row=LINE_A)
    with pytest.raises(explain.ExplainError):
        explain.change(to_run=RUN_B, to_row={**LINE_B, "deductions": "garbage"}, from_run=RUN_A, from_row=LINE_A)


EMPLOYEE = {"id": "E1", "first_name": "Meera", "last_name": "More", "status": "suspended",
            "ctc_annual": 148814.47, "ctc_monthly": 12401.21, "salary_structure_id": "S1"}
TEMPLATE = {"id": "S1", "name": "Staff S2", "employee_id": None, "ctc_annual": 604800.0, "ctc_monthly": 50400.0,
            "earnings": [{"component_name": "Basic", "monthly_amount": 20160.0},
                         {"component_name": "HRA", "monthly_amount": 10080.0},
                         {"component_name": "Special Allowance", "monthly_amount": 16128.0}],
            "deductions": [{"component_name": "EPF Employee", "monthly_amount": 1800.0}]}


def view(structure=TEMPLATE, line_count=0, revisions=()):
    return explain.structure_view(employee=EMPLOYEE, structure=structure, line_count=line_count,
                                  revisions=list(revisions))


def test_a_structure_owned_by_another_employee_shows_no_figures():
    result = view({**TEMPLATE, "employee_id": "E9", "name": "Anita — revised"})
    assert result["ownership"] == "other_employee" and result["owner_employee_id"] == "E9"
    assert result["figures_withheld"] is True and "earnings" not in result and "structure_ctc_monthly" not in result
    assert result["profile_ctc_annual"] == 148814.47
    assert any("belongs to another employee" in p for p in result["problems"])


def test_a_grade_template_shows_figures_and_each_components_share():
    result = view()
    assert result["ownership"] == "template"
    basic = next(c for c in result["earnings"] if c["name"] == "Basic")
    assert basic["monthly"] == 20160.0 and basic["share_of_ctc_percent"] == 40.0
    assert result["earnings_total"] == 46368.0 and result["not_itemised"] == 4032.0
    assert not any("CTC" in p for p in result["problems"])   # a template's CTC is not expected to match the profile


def test_an_own_structure_is_checked_against_the_profile_ctc():
    result = view({**TEMPLATE, "employee_id": "E1"})
    assert result["ownership"] == "own" and any("differs" in p for p in result["problems"])
    matching = view({**TEMPLATE, "employee_id": "E1", "ctc_annual": 148814.47, "ctc_monthly": 12401.21})
    assert not any("differs" in p for p in matching["problems"])


def test_a_missing_structure_and_no_pay_lines_are_reported():
    result = view(structure=None)
    assert result["ownership"] == "missing" and any("no salary structure" in p for p in result["problems"])
    assert any("no pay lines" in p for p in view(line_count=0)["problems"])
    assert not any("no pay lines" in p for p in view(line_count=3)["problems"])


def test_revisions_without_usable_components_or_matching_ctc_are_counted():
    junk = {"number": "REV-1", "status": "draft", "approval_status": "not_required", "current_ctc": 288200.61,
            "revised_ctc": 411186.54, "revised_structure": [{"amount": 1251.14, "component_name": "Drill Chuck 12133"}]}
    nameless = {"number": "REV-2", "status": "pending_approval", "approval_status": "rejected",
                "current_ctc": 148814.47, "revised_ctc": 160000.0, "revised_structure": [{"amount": 5.0}]}
    result = view(revisions=[junk, nameless])
    assert result["revisions"]["count"] == 2 and result["revisions"]["usable"] == 0
    by_number = {r["number"]: r for r in result["revisions"]["items"]}
    assert by_number["REV-1"]["matches_profile_ctc"] is False and by_number["REV-2"]["has_component_names"] is False


def claim(number, status, amount=1000.0, approved=None, paid_in=None, **fields):
    return {"number": number, "status": status, "amount": amount, "approved_amount": approved,
            "paid_in_payrun": paid_in, "claim_month": "2026-08-01", "component_name": "Travel", **fields}


def test_claims_are_grouped_by_status_and_data_problems_are_flagged():
    claims = [claim("C1", "draft"), claim("C2", "paid", approved=1000.0, paid_in="R1"),
              claim("C3", "partially_approved"),                                  # no approved amount
              claim("C4", "partially_approved", approved=1000.0),                 # not below the claim
              claim("C5", "approved", approved=1500.0),                           # above the claim, unpaid is fine
              claim("C6", "paid", approved=1000.0)]                               # paid, no pay run
    report = explain.reimbursement_report(claims)
    assert report["total"] == 6 and report["by_status"]["paid"] == {"count": 2, "claimed": 2000.0, "approved": 2000.0}
    flagged = {f["number"]: f["problems"] for f in report["flagged"]}
    assert set(flagged) == {"C3", "C4", "C5", "C6"} and report["flagged_count"] == 4
    assert "no approved amount" in flagged["C3"][0] and "no pay run" in flagged["C6"][0]
    assert "C5" in flagged and "above the claim" in flagged["C5"][0]
    assert "reason" in report["note"]


def test_an_approved_unpaid_claim_with_no_pay_run_is_normal():
    assert explain.reimbursement_report([claim("C1", "approved", approved=1000.0)])["flagged"] == []


def test_the_claim_list_is_capped_and_says_so():
    report = explain.reimbursement_report([claim(f"C{i}", "draft") for i in range(60)])
    assert len(report["claims"]) == explain.MAX_CLAIMS and report["truncated"] is True and report["total"] == 60


def test_the_same_deduction_under_two_spellings_is_one_component_in_a_comparison():
    """Found live: July says "EPF (Employee)" and August says "EPF Employee"; a naive diff showed a rise and a fall."""
    before = {**LINE_A, "deductions": [comp("EPF (Employee)", 1500.0), comp("Professional Tax", 200.0),
                                       comp("TDS", 1000.0)], "total_deductions": 2700.0, "net_pay": 37300.0}
    after = {**LINE_A, "id": "LX", "payrun_id": "RB", "deductions": [comp("EPF Employee", 1800.0),
                                                                      comp("Professional Tax", 200.0), comp("TDS", 1000.0)],
             "total_deductions": 3000.0}
    result = explain.change(to_run=RUN_B, to_row=after, from_run=RUN_A, from_row=before)
    assert [d["component"] for d in result["deductions"]] == ["EPF Employee"]
    epf = result["deductions"][0]
    assert (epf["before"], epf["after"], epf["change"]) == (1500.0, 1800.0, 300.0)
    assert epf["renamed_from"] == "EPF (Employee)"


def test_an_unrenamed_component_carries_no_renamed_from():
    result = explain.change(to_run=RUN_B, to_row=LINE_B, from_run=RUN_A, from_row=LINE_A)
    assert all("renamed_from" not in d for d in result["earnings"] + result["deductions"])


def test_approved_totals_are_only_summed_for_statuses_where_approval_has_happened():
    """Found live: 81 draft claims carried approved amounts (some far above the claim), summing to 16.9 million."""
    claims = [claim("C1", "draft", amount=1000.0, approved=5000.0), claim("C2", "submitted"),
              claim("C3", "approved", approved=800.0), claim("C4", "paid", approved=700.0, paid_in="R1"),
              claim("C5", "partially_approved", approved=400.0), claim("C6", "rejected")]
    by_status = explain.reimbursement_report(claims)["by_status"]
    assert by_status["draft"]["approved"] is None and by_status["submitted"]["approved"] is None
    assert by_status["rejected"]["approved"] is None
    assert (by_status["approved"]["approved"], by_status["paid"]["approved"],
            by_status["partially_approved"]["approved"]) == (800.0, 700.0, 400.0)
    flagged = {f["number"] for f in explain.reimbursement_report(claims)["flagged"]}
    assert "C1" in flagged                                  # a draft claim with an approved amount above it is still a data problem


def test_the_structure_view_says_it_is_complete_so_a_missing_pay_line_is_not_read_as_missing_evidence():
    """Found live: the planner read 'no pay lines' as missing evidence, tried to fetch pay lines and stalled."""
    for result in (view(), view(structure=None), view({**TEMPLATE, "employee_id": "E9"})):
        assert "complete" in result["note"] and "not missing evidence" in result["note"]

"""Implementation scaffolding for payroll_agent/lifecycle.py.

NOT the team's graded tests. These only check that the code behaves as
designed while it is being built.
"""
from __future__ import annotations

from datetime import date

import pytest

from payroll_agent import lifecycle as lc

TODAY = date(2026, 10, 4)
NAMES = {"a": "Asha A", "b": "Bhaskar B"}

LOANS = [
    {"id": "L1", "number": "LOAN-1", "employee_id": "a", "status": "partially_repaid", "loan_name": "Education Loan",
     "loan_amount": 10000.0, "emi_amount": 1000.0, "tenure_months": 12.0, "interest_rate": 8.0,
     "total_repaid": 2000.0, "disbursement_date": "2026-01-15"},
    {"id": "L2", "number": "LOAN-2", "employee_id": "b", "status": "draft", "loan_amount": 0.14,
     "emi_amount": -0.02, "tenure_months": -3.0, "interest_rate": -4.0, "total_repaid": 0.0,
     "disbursement_date": "0009-09-12"},
    {"id": "L3", "number": "LOAN-3", "employee_id": "b", "status": "cancelled", "loan_amount": -5.0,
     "emi_amount": 10.0, "tenure_months": 6.0, "interest_rate": 5.0, "disbursement_date": "2026-02-01"},
]
REPAYMENTS = [
    {"loan_id": "L1", "employee_id": "a", "date": "2026-09-30", "status": "scheduled",
     "principal_amount": 900.0, "total_amount": 1000.0},
    {"loan_id": "L1", "employee_id": "a", "date": "2026-10-31", "status": "scheduled",
     "principal_amount": 900.0, "total_amount": 1000.0},
    {"loan_id": "L1", "employee_id": "a", "date": "2026-07-31", "status": "deducted",
     "principal_amount": 900.0, "total_amount": 1000.0},
    {"loan_id": "L2", "employee_id": "b", "date": "2024-12-31", "status": "scheduled",
     "principal_amount": -1000.0, "total_amount": 10000.0},
]


def checks(report):
    return {c["check"]: c["count"] for c in report["data_quality"]["checks"]}


def test_loans_counts_totals_and_flags_the_junk():
    out = lc.loans_report(LOANS, REPAYMENTS, NAMES, TODAY)
    assert out["total"] == 3 and out["by_status"] == {"partially_repaid": 1, "draft": 1, "cancelled": 1}
    assert out["totals"] == {"loan_amount": 10000.14, "total_repaid": 2000.0, "loans_counted": 2,
                             "excluded_invalid": 1}
    assert checks(out) == {"emi_not_positive": 1, "tenure_not_positive": 1, "negative_interest_rate": 1,
                           "implausible_disbursement_date": 1, "loan_amount_not_positive": 1,
                           "repayment_principal_negative": 1}
    assert out["data_quality"]["records_with_issues"] == 2


def test_loans_repayment_schedule_by_month_and_past_scheduled():
    rep = lc.loans_report(LOANS, REPAYMENTS, NAMES, TODAY)["repayments"]
    assert rep["by_status"] == {"scheduled": 3, "deducted": 1}
    assert rep["scheduled_by_month"] == [{"month": "2024-12", "count": 1, "total": 10000.0},
                                         {"month": "2026-09", "count": 1, "total": 1000.0},
                                         {"month": "2026-10", "count": 1, "total": 1000.0}]
    assert rep["scheduled_in_the_past"] == {"count": 2, "total": 11000.0}


def test_loans_for_one_employee_list_that_employees_items():
    out = lc.loans_report(LOANS, REPAYMENTS, NAMES, TODAY, employee_id="a")
    assert out["total"] == 1 and out["repayments"]["total"] == 3
    assert out["items"] == [{"employee_id": "a", "employee_name": "Asha A", "number": "LOAN-1",
                             "status": "partially_repaid", "loan_name": "Education Loan", "loan_amount": 10000.0,
                             "emi_amount": 1000.0, "total_repaid": 2000.0, "tenure_months": 12.0}]
    assert lc.loans_report(LOANS, REPAYMENTS, NAMES, TODAY)["items"] == []


def test_loan_examples_are_capped_but_counts_are_full():
    many = [{"id": f"L{i}", "number": f"LOAN-{i}", "employee_id": "a", "loan_amount": -1.0} for i in range(15)]
    out = lc.loans_report(many, [], NAMES, TODAY)
    check = out["data_quality"]["checks"][0]
    assert check["check"] == "loan_amount_not_positive" and check["count"] == 15
    assert len(check["examples"]) == lc.MAX_EXAMPLES == 10


def test_loans_with_unusable_values_or_dates_never_crash():
    odd = [{"id": "L9", "number": "LOAN-9", "employee_id": "a", "loan_amount": "abc", "emi_amount": None,
            "tenure_months": float("nan"), "interest_rate": "x", "disbursement_date": "not a date"},
           {"id": "L10", "number": "LOAN-10", "employee_id": "a", "loan_amount": 5.0, "disbursement_date": ""}]
    out = lc.loans_report(odd, [{"loan_id": "L9", "date": "garbage", "status": "scheduled",
                                 "total_amount": "?"}], NAMES, TODAY)
    assert out["unusable_amounts"] == 4
    assert checks(out) == {"implausible_disbursement_date": 1, "repayment_date_implausible": 1}


REVS = [
    {"number": "REV-1", "employee_id": "a", "status": "pending_approval", "effective_date": "2026-09-01",
     "revised_ctc": 100000.0, "approval_status": "pending"},
    {"number": "REV-2", "employee_id": "b", "status": "pending_approval", "effective_date": "2026-10-20",
     "revised_ctc": "abc"},
    {"number": "REV-3", "employee_id": "a", "status": "approved", "effective_date": "2026-10-30"},
    {"number": "REV-4", "employee_id": "a", "status": "applied", "effective_date": "2026-01-01"},
    {"number": "REV-5", "employee_id": "b", "status": "draft", "effective_date": "2026-10-10"},
]


def test_revisions_pending_backdated_upcoming_and_applied():
    out = lc.revisions_report(REVS, NAMES, TODAY)
    assert out["total"] == 5 and out["pending_count"] == 2
    assert [p["number"] for p in out["pending"]] == ["REV-1", "REV-2"]          # oldest effective date first
    assert out["pending"][1]["revised_ctc"] is None and out["unusable_amounts"] == 1
    assert out["backdated_pending"]["count"] == 1
    assert out["backdated_pending"]["examples"][0]["employee_name"] == "Asha A"
    assert out["upcoming_30_days"] == 2 and out["applied_count"] == 1


def test_revisions_for_one_employee():
    assert lc.revisions_report(REVS, NAMES, TODAY, employee_id="b")["total"] == 2


SETTLEMENTS = [
    {"employee_id": "a", "status": "calculated", "approval_status": "not_required", "last_working_date": "2026-09-18",
     "bonus_payable": 100.0, "gratuity_payable": 200.0, "leave_encashment": 50.0, "epf_settlement": 10.0,
     "notice_pay_recovery": 0.0, "gross_settlement": 360.0, "net_settlement": 360.0},
    {"employee_id": "b", "status": "approved", "gross_settlement": 1000.0, "net_settlement": 1000.0},
]


def test_settlements_list_components_and_totals():
    out = lc.settlements_report(SETTLEMENTS, NAMES)
    assert out["total"] == 2 and out["by_status"] == {"calculated": 1, "approved": 1}
    assert out["total_net_settlement"] == 1360.0
    first = out["items"][0]
    assert first["employee_name"] == "Asha A" and first["gratuity_payable"] == 200.0
    assert out["items"][1]["bonus_payable"] is None


DECLS = [
    {"id": "D1", "employee_id": "a", "fiscal_year": "2026-27", "status": "approved",
     "investments": [{"section": "80C", "declared_amount": 150000}, {"section": "80D", "declared_amount": 20000}]},
    {"id": "D2", "employee_id": "b", "fiscal_year": "2026-27", "status": "submitted", "investments": []},
    {"id": "D3", "employee_id": "c", "fiscal_year": "2026-27", "status": "draft"},
    {"id": "D4", "employee_id": "d", "fiscal_year": "2025-26", "status": "approved",
     "investments": [{"section": "80C", "declared_amount": "100000"}, "junk"]},
]
PROOFS = [{"declaration_id": "D1", "approval_status": "submitted", "employee_id": "a"}]


def test_investments_status_sections_proofs_and_missing_proof():
    out = lc.investments_report(DECLS, PROOFS, NAMES)
    assert out["total"] == 4 and out["by_status"] == {"approved": 2, "submitted": 1, "draft": 1}
    assert out["by_fiscal_year"] == {"2026-27": 3, "2025-26": 1}
    assert out["approved_declared_by_section"] == {"80C": 250000.0, "80D": 20000.0}
    assert out["without_proof"]["count"] == 2                      # D2 and D4; the draft D3 is excluded
    assert {e["employee_id"] for e in out["without_proof"]["employees"]} == {"b", "d"}
    assert out["proofs"] == {"total": 1, "by_approval_status": {"submitted": 1}}


def test_build_lifecycle_dispatches_and_reports_unusable_amounts():
    data = {"SalaryRevision": REVS}
    out = lc.build_lifecycle("revisions", data=data, names=NAMES, today=TODAY)
    assert out["topic"] == "revisions" and "unusable_amounts" not in out
    assert out["skipped"] == [{"what": "amounts", "reason": "1 unusable amount(s) counted as 0"}]
    clean = lc.build_lifecycle("settlements", data={"FinalSettlement": SETTLEMENTS}, names=NAMES, today=TODAY)
    assert clean["skipped"] == []


def test_build_lifecycle_rejects_an_unknown_topic():
    with pytest.raises(ValueError):
        lc.build_lifecycle("payslips", data={}, names={}, today=TODAY)


def test_employee_names_prefer_the_full_name():
    names = lc.employee_names([{"id": "a", "first_name": "Asha", "last_name": "Patil"},
                               {"id": "b", "_party_id_display": "Bhaskar B"}, {"id": "c", "_display": "c@x.in"}, {}])
    assert names == {"a": "Asha Patil", "b": "Bhaskar B", "c": "c@x.in"}


def test_a_pending_revision_whose_approval_status_says_approved_or_rejected_is_flagged():
    revisions = [
        {"number": "REV-A", "employee_id": "a", "status": "pending_approval", "approval_status": "rejected",
         "effective_date": "2026-09-01"},
        {"number": "REV-B", "employee_id": "b", "status": "pending_approval", "approval_status": "approved",
         "effective_date": "2026-09-02"},
        {"number": "REV-C", "employee_id": "a", "status": "pending_approval", "approval_status": "pending_approval",
         "effective_date": "2026-09-03"},
        {"number": "REV-D", "employee_id": "a", "status": "approved", "approval_status": "rejected",
         "effective_date": "2026-09-04"},
    ]
    conflicts = lc.revisions_report(revisions, NAMES, TODAY)["status_conflicts"]
    assert conflicts["count"] == 2                                     # REV-A and REV-B; REV-D is not pending
    assert {e["number"] for e in conflicts["examples"]} == {"REV-A", "REV-B"}
    assert "pending approval" in conflicts["note"]


def test_repayment_months_use_only_plausible_dates_and_bad_dates_are_counted():
    base = {"loan_id": "L1", "employee_id": "a", "status": "scheduled", "total_amount": 100.0,
            "principal_amount": 90.0}
    repayments = [{**base, "date": "2026-11-30"}, {**base, "date": None}, {**base, "date": "0009-03-15"},
                  {**base, "date": "garbage"}, {**base, "date": {"a": 1}}, {**base, "date": "2026-12-31"}]
    out = lc.loans_report([], repayments, NAMES, TODAY)["repayments"]
    assert [m["month"] for m in out["scheduled_by_month"]] == ["2026-11", "2026-12"]
    assert out["scheduled_with_bad_date"] == 4              # the missing date, "0009-03-15", "garbage", the dict
    report = lc.loans_report([], repayments, NAMES, TODAY)
    assert {c["check"]: c["count"] for c in report["data_quality"]["checks"]} == {"repayment_date_implausible": 3}


def test_a_bad_date_never_pushes_real_months_out_of_the_cap():
    base = {"loan_id": "L1", "employee_id": "a", "status": "scheduled", "total_amount": 1.0, "principal_amount": 1.0}
    months = [{**base, "date": f"2027-{m:02d}-15"} for m in range(1, 13)] + [{**base, "date": "0009-01-01"}] * 3
    out = lc.loans_report([], months, NAMES, TODAY)["repayments"]
    assert [m["month"] for m in out["scheduled_by_month"]][:2] == ["2027-01", "2027-02"]
    assert len(out["scheduled_by_month"]) == 12 and out["scheduled_with_bad_date"] == 3


def test_records_with_issues_counts_loans_that_have_no_number_or_id():
    loans = [{"loan_amount": -1.0, "employee_id": "a"}, {"loan_amount": -2.0, "employee_id": "b"}]
    assert lc.loans_report(loans, [], NAMES, TODAY)["data_quality"]["records_with_issues"] == 2

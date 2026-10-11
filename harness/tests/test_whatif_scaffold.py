"""Implementation scaffolding for the salary what-if estimator.

NOT the team's graded tests. Pure functions and a fake client, so the arithmetic and the guards
can be checked offline.
"""
from __future__ import annotations

import json

import pytest

from payroll_agent.capabilities import Argument, Capability, CapabilityError, CapabilityRegistry


def _registry():
    return CapabilityRegistry([Capability(
        "probe", "probe", {"pct": Argument("number", "a percentage", required=False, minimum=-99, maximum=1000)})])


@pytest.mark.parametrize("value", [10, 9.5, -5, 0])
def test_number_accepts_ints_and_floats(value):
    assert _registry().validate("probe", {"pct": value}) == {"pct": value}


@pytest.mark.parametrize("value", [True, "10", float("nan"), float("inf"), -100, 1001])
def test_number_rejects_bools_strings_nonfinite_and_out_of_range(value):
    with pytest.raises(CapabilityError):
        _registry().validate("probe", {"pct": value})


from payroll_agent import whatif  # noqa: E402

CONFIGS = {
    "EPFConfig": [{"is_active": 1, "employee_contribution_rate": 12.0, "employer_contribution_rate": 12.0,
                   "pf_wage_ceiling": 15000.0, "restrict_pf_to_ceiling": 1, "edli_rate": 0.5,
                   "edli_wage_ceiling": 15000.0, "admin_charges_rate": 0.5}],
    "ESIConfig": [{"is_active": 1, "wage_ceiling": 21000.0, "employee_contribution_rate": 0.75,
                   "employer_contribution_rate": 3.25}],
    "PTConfig": [{"is_active": 1, "slabs": [
        {"from_amount": 0, "tax_amount": 0, "to_amount": 7500},
        {"from_amount": 7501, "tax_amount": 175, "to_amount": 10000},
        {"from_amount": 10001, "tax_amount": 200, "to_amount": 999999999}]}],
}
EARNINGS = [{"component_name": "Basic", "amount": 20000.0}, {"component_name": "HRA", "amount": 10000.0},
            {"component_name": "Special Allowance", "amount": 10000.0}]


def line(**fields):
    base = {"id": "L1", "payrun_id": "R1", "employee_id": "E1", "employee_name": "Asha Patil",
            "created_at": "2026-09-29T15:00:00", "days_payable": 26.0, "working_days": 26.0,
            "earnings": EARNINGS, "gross_pay": 40000.0, "net_pay": 37000.0, "basic_pay": 20000.0,
            "epf_employee": 1800.0, "esi_employee": 0.0, "esi_employer": 0.0, "professional_tax": 200.0,
            "employer_contribution": 1950.0, "tds": 1000.0, "loan_deduction": 0.0, "other_deductions": 0.0,
            "lwf_employee": 0.0}
    return {**base, **fields}


def test_parse_change_needs_exactly_one_finite_value():
    assert whatif.parse_change(10, None) == {"percent_raise": 10.0}
    assert whatif.parse_change(None, 50000) == {"new_monthly_gross": 50000.0}
    for args in ((None, None), (10, 50000), (float("nan"), None), (-100, None), (None, 0), (None, float("inf"))):
        with pytest.raises(whatif.WhatIfError):
            whatif.parse_change(*args)


def test_a_ten_percent_raise_recomputes_the_lines():
    result = whatif.estimate(row=line(), change={"percent_raise": 10.0}, configs=CONFIGS)
    assert result["estimate"] is True and result["written"] is False
    before, after = result["before"], result["after"]
    assert (before["gross"], before["net"]) == (40000.0, 37000.0)
    assert after["gross"] == 44000.0
    assert after["epf_employee"] == 1800.0          # capped at the 15000 wage ceiling
    assert after["professional_tax"] == 200.0
    assert after["tds"] == 1100.0                    # scaled with gross
    assert after["net"] == 40900.0                   # 44000 - (1800 + 0 + 200 + 1100)
    assert after["employer_cost"] == 45950.0         # 44000 + 1950
    assert result["delta"]["net"] == 3900.0 and result["delta"]["gross"] == 4000.0
    assert result["differs_from_payslip"] == []


def test_a_cut_below_the_esi_ceiling_switches_esi_on():
    result = whatif.estimate(row=line(), change={"new_monthly_gross": 20000.0}, configs=CONFIGS)
    after = result["after"]
    assert after["esi_employee"] == 150.0 and after["esi_employer"] == 650.0
    assert after["epf_employee"] == 1200.0           # basic halves to 10000
    assert after["employer_contribution"] == 1300.0  # 1200 + 50 EDLI + 50 admin
    assert after["net"] == 17950.0                   # 20000 - (1200 + 150 + 200 + 500)


@pytest.mark.parametrize("gross,tax", [(7500, 0.0), (7501, 175.0), (10000, 175.0), (10001, 200.0)])
def test_professional_tax_switches_exactly_at_the_slab_edges(gross, tax):
    result = whatif.estimate(row=line(), change={"new_monthly_gross": float(gross)}, configs=CONFIGS)
    assert result["after"]["professional_tax"] == tax


def test_a_missing_config_is_not_computed_and_withholds_net():
    configs = {k: v for k, v in CONFIGS.items() if k != "PTConfig"}
    result = whatif.estimate(row=line(), change={"percent_raise": 10.0}, configs=configs)
    assert result["after"]["professional_tax"] is None and result["after"]["net"] is None
    assert "PT" in result["after"]["not_computed"] and "net" not in result["delta"]


def test_tds_stays_zero_and_says_so():
    result = whatif.estimate(row=line(tds=0.0, net_pay=38000.0), change={"percent_raise": 10.0}, configs=CONFIGS)
    assert result["after"]["tds"] == 0.0
    assert any("TDS" in note and "0" in note for note in result["assumptions"])


def test_earnings_may_arrive_as_a_json_string():
    result = whatif.estimate(row=line(earnings=json.dumps(EARNINGS)), change={"percent_raise": 0.0}, configs=CONFIGS)
    assert result["after"]["gross"] == 40000.0


def test_earnings_that_do_not_add_up_are_refused():
    with pytest.raises(whatif.WhatIfError):
        whatif.estimate(row=line(gross_pay=45000.0), change={"percent_raise": 10.0}, configs=CONFIGS)


def test_the_basis_row_is_the_latest_full_month_calculated_line():
    older = line(id="old", created_at="2026-08-30T00:00:00")
    newest = line(id="new", created_at="2026-09-30T00:00:00")
    part = line(id="part", created_at="2026-10-01T00:00:00", days_payable=4.0)
    zero = line(id="zero", created_at="2026-10-02T00:00:00", net_pay=0.0)
    assert whatif.pick_basis_row([older, newest, part, zero])["id"] == "new"
    assert whatif.pick_basis_row([part, zero]) is None


def test_baseline_differences_reports_only_lines_the_model_cannot_reproduce():
    assert whatif.baseline_differences([line()], CONFIGS) == []
    diffs = whatif.baseline_differences([line(epf_employee=1500.0)], CONFIGS)
    assert [d["field"] for d in diffs] == ["epf_employee"]
    assert diffs[0]["calculated"] == 1800.0 and diffs[0]["actual"] == 1500.0


def test_a_payslip_the_model_cannot_reproduce_is_flagged_in_the_estimate():
    result = whatif.estimate(row=line(epf_employee=1500.0), change={"percent_raise": 10.0}, configs=CONFIGS)
    assert [d["field"] for d in result["differs_from_payslip"]] == ["epf_employee"]

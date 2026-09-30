"""Implementation scaffolding for payroll_agent/scan_checks.py.

NOT the team's graded tests. These only check that the code behaves as
designed while it is being built.
"""
from __future__ import annotations

from payroll_agent import scan_checks as sc


def row(emp, net, *, rid=None, name=None, run="R1"):
    return {"id": rid or f"row-{emp}", "employee_id": emp,
            "employee_name": name or f"Name {emp}", "net_pay": net, "payrun_id": run}


def person(emp, status="active", exit_date=None):
    return {"id": emp, "status": status, "exit_date": exit_date}


def test_payee_status_flags_each_condition_with_its_severity():
    employees = {
        "a": person("a", "left"), "b": person("b", "suspended"),
        "c": person("c", "active", "2026-08-15"), "d": person("d", "on_leave"),
        "e": person("e"), "f": person("f", "active", "2026-09-10"),
    }
    rows = [row(k, 1000) for k in "abcdef"]
    found = sc.payee_status(rows, employees, "2026-08-31")
    got = sorted((f["employee_id"], f["severity"]) for f in found)
    assert got == [("a", "high"), ("b", "high"), ("c", "high"), ("d", "info")]


def test_left_employee_with_exit_date_reports_both_conditions():
    found = sc.payee_status([row("a", 1000)], {"a": person("a", "left", "2026-08-01")}, "2026-08-31")
    assert len(found) == 2
    assert {f["evidence"].get("period_end") for f in found} == {None, "2026-08-31"}


def test_exit_date_timestamp_is_compared_on_its_date_part():
    found = sc.payee_status([row("a", 1000)], {"a": person("a", "active", "2026-08-15T00:00:00")}, "2026-08-31")
    assert [f["severity"] for f in found] == ["high"]


def test_payee_missing_from_employee_list_is_a_high_finding_not_a_crash():
    found = sc.payee_status([row("ghost", 1000)], {}, "2026-08-31")
    assert len(found) == 1
    assert found[0]["severity"] == "high"
    assert found[0]["evidence"] == {"reason": "not_in_employee_list"}


def test_net_pay_change_tiers_follow_threshold():
    prev = [row(k, 1000, run="R0") for k in "abcd"]
    cur = [row("a", 1600), row("b", 1350), row("c", 1200), row("d", 400)]
    found, info = sc.net_pay_change(cur, prev, 30)
    assert {f["employee_id"]: f["severity"] for f in found} == {"a": "high", "b": "medium", "d": "high"}
    assert info == {"new_in_run": 0, "not_comparable": 0}
    lower, _ = sc.net_pay_change(cur, prev, 10)
    assert {f["employee_id"]: f["severity"] for f in lower}["c"] == "medium"
    higher, _ = sc.net_pay_change(cur, prev, 60)
    assert {f["employee_id"] for f in higher} == {"a", "d"}


def test_net_pay_change_evidence_carries_both_values():
    found, _ = sc.net_pay_change([row("a", 500)], [row("a", 1000, run="R0")], 30)
    assert found[0]["evidence"] == {"previous_net": 1000, "current_net": 500, "change_pct": -50.0}


def test_net_pay_change_counts_new_and_uncomparable_without_dividing_by_zero():
    prev = [row("zero", 0, run="R0"), row("none", None, run="R0"),
            row("dup", 1000, run="R0"), row("dup", 1000, rid="dup-2", run="R0")]
    cur = [row("zero", 500), row("none", 500), row("dup", 500), row("fresh", 500)]
    found, info = sc.net_pay_change(cur, prev, 30)
    assert found == []
    assert info == {"new_in_run": 1, "not_comparable": 3}


def test_net_pay_sanity_flags_zero_negative_and_missing():
    found = sc.net_pay_sanity([row("a", 0), row("b", -5), row("c", None), row("d", 100)])
    assert sorted(f["employee_id"] for f in found) == ["a", "b", "c"]
    assert {f["severity"] for f in found} == {"high"}


def test_duplicate_payees_reports_row_ids():
    found = sc.duplicate_payees([row("a", 1, rid="r1"), row("a", 1, rid="r2"), row("b", 1)])
    assert len(found) == 1
    assert found[0]["evidence"] == {"occurrences": 2, "row_ids": ["r1", "r2"]}


def test_is_calculated():
    assert sc.is_calculated([row("a", 0), row("b", 10)])
    assert not sc.is_calculated([row("a", 0), row("b", None)])
    assert not sc.is_calculated([])


def test_run_checks_on_uncalculated_run_returns_nothing_to_check():
    result = sc.run_checks(rows=[row("a", 0)], prev_rows=[], employees=[person("a")], period_end="2026-09-30")
    assert result["calculated"] is False
    assert result["findings"] == []
    assert [s["check"] for s in result["skipped"]] == list(sc.CHECKS)
    assert {s["reason"] for s in result["skipped"]} == {"run not calculated"}
    assert result["counts"]["rows_checked"] == 1


def test_run_checks_without_comparison_skips_only_net_pay_change():
    result = sc.run_checks(rows=[row("a", 100)], prev_rows=None, employees=[person("a")],
                           period_end="2026-08-31", prev_reason="no earlier calculated regular run")
    assert result["calculated"] is True
    assert result["skipped"] == [{"check": "net_pay_change", "reason": "no earlier calculated regular run"}]
    assert set(result["counts"]["by_check"]) == {"payee_status", "net_pay_sanity", "duplicate_payees"}


def test_run_checks_sorts_by_severity_assigns_ids_and_counts():
    rows = [row("b", 100, name="Bea"), row("a", 100, name="Abe"), row("c", 100, name="Cy")]
    employees = [person("a", "on_leave"), person("b", "left"), person("c")]
    result = sc.run_checks(rows=rows, prev_rows=None, employees=employees, period_end="2026-08-31")
    assert [f["severity"] for f in result["findings"]] == ["high", "info"]
    assert [f["id"] for f in result["findings"]] == ["F-001", "F-002"]
    assert result["counts"]["by_severity"] == {"high": 1, "info": 1}


def test_run_checks_caps_findings_but_not_counts():
    rows = [row(f"e{i}", 100) for i in range(45)]
    employees = [person(f"e{i}", "left") for i in range(45)]
    result = sc.run_checks(rows=rows, prev_rows=None, employees=employees, period_end="2026-08-31")
    assert len(result["findings"]) == sc.MAX_FINDINGS == 40
    assert result["truncated"] is True
    assert result["counts"]["by_severity"] == {"high": 45}
    assert result["findings"][-1]["id"] == "F-040"


def test_string_amounts_do_not_crash_and_evidence_keeps_the_raw_value():
    assert sc.is_calculated([row("a", "400.00")])
    assert not sc.is_calculated([row("a", "abc")])
    found, _ = sc.net_pay_change([row("a", "400.00")], [row("a", "1000.00", run="R0")], 30)
    assert found[0]["severity"] == "high"
    assert found[0]["evidence"]["previous_net"] == "1000.00"
    assert [f["employee_id"] for f in sc.net_pay_sanity([row("a", "abc"), row("b", "50.5")])] == ["a"]

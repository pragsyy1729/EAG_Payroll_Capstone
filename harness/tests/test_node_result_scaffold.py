"""Implementation scaffolding for the node_result verifier in evals/verifiers.py.

NOT the team's graded tests.
"""
from __future__ import annotations

from evals.verifiers import REGISTRY, VerifyContext


def ctx_with(nodes):
    return VerifyContext(task={}, record={"result": {"nodes": nodes}}, before=[], after=[],
                         client=None, jurisdiction="IN")


def node(skill, result, state="succeeded"):
    return {"skill": skill, "state": state, "input": {}, "result": result}


async def check(nodes, **params):
    return await REGISTRY["node_result"](ctx_with(nodes), params)


async def test_passes_on_a_nested_value_with_an_op():
    nodes = {"scan": node("pre_payroll_scan", {"counts": {"by_severity": {"high": 3}}})}
    out = await check(nodes, skill="pre_payroll_scan", path="counts.by_severity.high",
                      expect={"op": "gte", "value": 1})
    assert out["ok"] is True and out["observed"] == 3


async def test_path_can_index_into_lists():
    nodes = {"scan": node("pre_payroll_scan", {"findings": [{"check": "payee_status"}]})}
    out = await check(nodes, skill="pre_payroll_scan", path="findings.0.check", expect="payee_status")
    assert out["ok"] is True


async def test_fails_when_the_path_is_missing():
    nodes = {"scan": node("pre_payroll_scan", {"counts": {}})}
    out = await check(nodes, skill="pre_payroll_scan", path="counts.by_severity.high", expect=1)
    assert out["ok"] is False and out["observed"] is None


async def test_fails_when_no_succeeded_node_ran():
    nodes = {"scan": node("pre_payroll_scan", None, state="failed")}
    out = await check(nodes, skill="pre_payroll_scan", path="calculated", expect=True)
    assert out["ok"] is False
    assert out["observed"] == {"succeeded_nodes": 0}


async def test_uses_the_last_succeeded_node_of_that_skill():
    nodes = {"one": node("pre_payroll_scan", {"calculated": False}),
             "two": node("pre_payroll_scan", {"calculated": True})}
    out = await check(nodes, skill="pre_payroll_scan", path="calculated", expect=True)
    assert out["ok"] is True

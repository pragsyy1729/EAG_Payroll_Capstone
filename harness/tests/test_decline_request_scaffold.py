"""Implementation scaffolding for the decline_request capability and worker.

NOT the team's graded tests.
"""
from __future__ import annotations

import pytest

from payroll_agent import workers
from payroll_agent.capabilities import CapabilityError, default_registry
from payroll_agent.core.live_graph import TaskSpec

VALID = {"reason_code": "needs_human_approval",
         "explanation": "approving a payroll run is done by a human approver"}


def test_capability_is_a_terminal_read_only_decline_without_evidence():
    registry = default_registry()
    capability = registry.get("decline_request")
    assert "decline_request" in registry.terminal_skills("text")
    assert capability.side_effect is False
    assert capability.needs_evidence is False
    assert "jurisdiction" not in capability.arguments
    assert registry.get("answer_with_evidence").needs_evidence is True


def test_validation_accepts_a_good_request_and_optional_alternative():
    registry = default_registry()
    assert registry.validate("decline_request", VALID)["reason_code"] == "needs_human_approval"
    with_alt = registry.validate("decline_request", {**VALID, "alternative": "I can submit it for approval"})
    assert with_alt["alternative"] == "I can submit it for approval"


@pytest.mark.parametrize("bad", [
    {**VALID, "reason_code": "because"},
    {**VALID, "explanation": "   "},
    {**VALID, "explanation": "x" * 601},
    {**VALID, "alternative": "y" * 301},
    {"reason_code": "not_permitted"},
])
def test_validation_rejects_bad_requests(bad):
    with pytest.raises(CapabilityError):
        default_registry().validate("decline_request", bad)


def ctx():
    return workers.RunContext(run_id="r", store=None, llm=None, agentswitch=None)


async def test_worker_builds_a_structured_refusal_without_any_calls():
    task = TaskSpec("d", "decline_request", {**VALID, "alternative": "I can submit it for approval"})
    result = await workers.run_decline_request(ctx(), task)
    assert result["declined"] is True
    assert result["reason_code"] == "needs_human_approval"
    assert result["alternative"] == "I can submit it for approval"
    assert result["text"] == ("I can't do that: approving a payroll run is done by a human approver "
                              "Instead: I can submit it for approval")


async def test_worker_text_without_an_alternative_has_no_instead_clause():
    result = await workers.run_decline_request(ctx(), TaskSpec("d", "decline_request", VALID))
    assert result["text"] == "I can't do that: approving a payroll run is done by a human approver"
    assert "alternative" not in result


def test_worker_is_registered():
    assert "decline_request" in workers._WORKERS


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_an_empty_optional_argument_is_treated_as_absent(empty):
    registry = default_registry()
    clean = registry.validate("decline_request", {**VALID, "alternative": empty})
    assert "alternative" not in clean
    assert "search" not in registry.validate("list_employees", {"jurisdiction": "IN", "search": empty})


def test_an_empty_required_argument_is_still_rejected():
    with pytest.raises(CapabilityError):
        default_registry().validate("decline_request", {**VALID, "explanation": None})

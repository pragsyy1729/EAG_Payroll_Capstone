"""Implementation scaffolding for the decline_request guidance.

NOT the team's graded tests. It only guards the wording the planner reads; real refusal behaviour is
checked live with the planner and recorded in WORKFLOWS.md.
"""
from __future__ import annotations

from payroll_agent.capabilities import default_registry


def test_the_decline_description_names_the_two_refusals_and_their_reason_codes():
    description = default_registry().get("decline_request").description.lower()
    for phrase in ("bank file", "disburs", "extra", "skipping", "needs_human_approval", "no_such_capability"):
        assert phrase in description, phrase

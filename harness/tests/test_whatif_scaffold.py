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

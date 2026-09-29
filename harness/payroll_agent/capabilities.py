"""The complete capability surface exposed to the payroll planner.

The planner in ``planner.py`` is domain-agnostic: it depends only on this
registry's shape -- descriptions and machine-checkable argument contracts.
A model can select a capability; it cannot invent one or bypass its
validation boundary, because every task is validated here before it can
enter the live graph.

Deliberately small for now: seven capabilities covering the two MVP flows
("Run August payroll", "Why is X's net pay lower") plus the building
blocks both share (resolving an employee by name, reading payrun/employee
records). Extend by adding a ``Capability`` here, never by branching on a
skill name elsewhere -- see ``harness/REQUIREMENTS.md`` SS16 for the fuller
requirements catalog this is meant to grow into, and the "Open / next
steps" note about deriving this registry from AgentSwitch's own scoped
``tools/list`` instead of hand-writing it, once the MVP is proven.

Every AgentSwitch-backed capability below carries a required
``jurisdiction`` argument (``IN``/``US``). Nothing here defaults or infers
one -- see ``agentswitch.py``'s docstring for why guessing a tenant is
exactly the kind of mistake that stays invisible until an audit.
"""
from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class CapabilityError(ValueError):
    """A proposed task does not satisfy the advertised capability contract."""


def _check_format(label: str, format_name: str, value: Any, known_values: Collection[str] | None) -> None:
    """Validate a declared value format. Unknown formats are a configuration bug."""
    if format_name == "id":
        # Provenance, not shape: an id must be a string the run has actually
        # seen (in the goal, the stimulus, or a succeeded outcome). A model
        # that invents one is rejected before anything reaches AgentSwitch.
        # ``None`` means the caller supplied no evidence set, so nothing to check against.
        if known_values is not None and value not in known_values:
            raise CapabilityError(
                f"{label}={value!r} does not appear in the goal or any earlier outcome; "
                "use an id taken from a completed result, not one you infer")
        return
    if format_name == "month":
        if not isinstance(value, str) or not _MONTH_RE.match(value):
            raise CapabilityError(f"{label} must be a pay month as YYYY-MM")
        return
    raise CapabilityError(f"unsupported argument format {format_name!r} on {label}")


@dataclass(frozen=True)
class Argument:
    kind: str
    description: str
    required: bool = True
    default: Any = None
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[str, ...] = ()
    format: str | None = None

    def manifest(self) -> dict[str, Any]:
        result: dict[str, Any] = {"type": self.kind, "description": self.description}
        if not self.required:
            result["required"] = False
            if self.default is not None:
                result["default"] = self.default
        if self.minimum is not None:
            result["minimum"] = self.minimum
        if self.maximum is not None:
            result["maximum"] = self.maximum
        if self.choices:
            result["enum"] = list(self.choices)
        if self.format:
            result["format"] = self.format
        return result


@dataclass(frozen=True)
class Capability:
    name: str
    description: str
    arguments: dict[str, Argument] = field(default_factory=dict)
    role: str | None = None
    side_effect: bool = False
    terminal_for: tuple[str, ...] = ()
    families: tuple[str, ...] = ()

    def manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "arguments": {name: spec.manifest() for name, spec in self.arguments.items()},
            "side_effect": self.side_effect,
            "terminal_for": list(self.terminal_for),
        }


class CapabilityRegistry:
    def __init__(self, capabilities: list[Capability]) -> None:
        self._items = {item.name: item for item in capabilities}
        if len(self._items) != len(capabilities):
            raise ValueError("capability names must be unique")

    def __contains__(self, name: str) -> bool:
        return name in self._items

    def get(self, name: str) -> Capability:
        try:
            return self._items[name]
        except KeyError as error:
            raise CapabilityError(f"unknown capability {name!r}") from error

    def manifest(self) -> list[dict[str, Any]]:
        return [item.manifest() for item in self._items.values()]

    def names(self) -> tuple[str, ...]:
        return tuple(self._items)

    def terminal_skills(self, respond_as: str) -> set[str]:
        return {item.name for item in self._items.values() if respond_as in item.terminal_for}

    def family(self, name: str) -> set[str]:
        """Every capability declaring membership of a named family."""
        return {item.name for item in self._items.values() if name in item.families}

    def validate(self, name: str, values: Any, *, known_values: Collection[str] | None = None) -> dict[str, Any]:
        capability = self.get(name)
        if not isinstance(values, dict):
            raise CapabilityError(f"arguments for {name} must be an object")
        unknown = set(values).difference(capability.arguments)
        if unknown:
            raise CapabilityError(f"unsupported arguments for {name}: {sorted(unknown)}")
        clean: dict[str, Any] = {}
        for key, spec in capability.arguments.items():
            if key not in values:
                if spec.required:
                    raise CapabilityError(f"{name} requires argument {key!r}")
                if spec.default is not None:
                    clean[key] = spec.default
                continue
            value = values[key]
            if spec.kind == "string":
                if not isinstance(value, str) or not value.strip():
                    raise CapabilityError(f"{name}.{key} must be a non-empty string")
                value = value.strip()
                if spec.maximum is not None and len(value) > spec.maximum:
                    raise CapabilityError(f"{name}.{key} exceeds {spec.maximum} characters")
            elif spec.kind == "integer":
                if isinstance(value, bool) or not isinstance(value, int):
                    raise CapabilityError(f"{name}.{key} must be an integer")
                if spec.minimum is not None and value < spec.minimum:
                    raise CapabilityError(f"{name}.{key} must be >= {spec.minimum}")
                if spec.maximum is not None and value > spec.maximum:
                    raise CapabilityError(f"{name}.{key} must be <= {spec.maximum}")
            elif spec.kind == "boolean":
                if not isinstance(value, bool):
                    raise CapabilityError(f"{name}.{key} must be a boolean")
            else:
                raise CapabilityError(f"unsupported contract type {spec.kind!r}")
            if spec.choices and value not in spec.choices:
                raise CapabilityError(f"{name}.{key} must be one of {list(spec.choices)}")
            # Value-level rules are declared on the argument, not switched on
            # the capability name -- see agentswitch's "month" format, the
            # same pattern AgentSwitch's own tool schema uses server-side.
            if spec.format:
                _check_format(f"{name}.{key}", spec.format, value, known_values)
            clean[key] = value
        return clean


_JURISDICTION = Argument(
    "string", "Which AgentSwitch tenant this call targets: IN (Suryodaya) or US (Keystone).",
    choices=("IN", "US"),
)


def default_registry() -> CapabilityRegistry:
    def string(description: str, **kwargs: Any) -> Argument:
        return Argument("string", description, **kwargs)

    def integer(description: str, **kwargs: Any) -> Argument:
        return Argument("integer", description, **kwargs)

    return CapabilityRegistry([
        Capability(
            "list_employees",
            "Search employees by name or other text to resolve who a request refers to, "
            "before looking up their payroll records. Returns matching Employee rows.",
            {"jurisdiction": _JURISDICTION,
             "search": string("Name or text to search for.", required=False, maximum=200),
             "limit": integer("Max rows to return.", required=False, default=10, minimum=1, maximum=100)},
            families=("evidence",),
        ),
        Capability(
            "list_payruns",
            "List PayRun records, to check whether a run for a given month already exists and "
            "what state it is in. To check a month, pass `month` (YYYY-MM): it matches on the "
            "run's actual period dates and returns every run for that month. Do not filter by "
            "`pay_period` for that -- labels vary ('Aug 2026', 'Sep 2025', 'Bonus').",
            {"jurisdiction": _JURISDICTION,
             "month": string("Return only runs whose period starts in this month (YYYY-MM).",
                             required=False, format="month"),
             "pay_period": string("Filter by pay period label.", required=False, maximum=200),
             "limit": integer("Max rows to return.", required=False, default=10, minimum=1, maximum=100)},
            families=("evidence",),
        ),
        Capability(
            "get_payrun",
            "Read one PayRun record by id: computed totals, status, funding state, and its "
            "legal next transitions.",
            {"jurisdiction": _JURISDICTION,
             "payrun_id": string("PayRun id.", maximum=200, format="id")},
            families=("evidence",),
        ),
        Capability(
            "list_payrun_employees",
            "List PayRunEmployee rows -- the per-employee gross-to-net breakdown for a pay "
            "run. Filter by employee_id and/or payrun_id. This is the primary evidence for "
            "explaining why one employee's net pay differs between two runs: call it once "
            "per run being compared, for the same employee_id.",
            {"jurisdiction": _JURISDICTION,
             "employee_id": string("Filter by employee id.", required=False, maximum=200, format="id"),
             "payrun_id": string("Filter by payrun id.", required=False, maximum=200, format="id"),
             "limit": integer("Max rows to return.", required=False, default=10, minimum=1, maximum=100)},
            families=("evidence",),
        ),
        Capability(
            "run_payroll",
            "Open (or reuse) the pay run for a month and calculate draft salary slips. "
            "This is the real payroll calculation -- a mutation, not a read. It never "
            "submits, approves, or disburses, but it REUSES an existing run for the month and "
            "rewrites that run's slips. The runtime refuses it when the month's run is past "
            "`review` (pending_approval, approved, paid, cancelled) and returns "
            "`run_exists_not_recalculable` with the existing run instead -- report that, do not retry.",
            {"jurisdiction": _JURISDICTION,
             "month": string("Pay month as YYYY-MM.", format="month"),
             "payrun_id": string("Existing PayRun to recalculate instead of the month's own.",
                                 required=False, maximum=200, format="id")},
            side_effect=True,
        ),
        Capability(
            "submit_payrun_for_approval",
            "Move a calculated PayRun from review to pending_approval. This seat cannot "
            "approve or reject a run itself -- AgentSwitch enforces that separation, not "
            "just this agent's policy -- so this only ever hands the run to a human "
            "approver; it never finishes the job on its own.",
            {"jurisdiction": _JURISDICTION,
             "payrun_id": string("PayRun id to submit.", maximum=200, format="id")},
            side_effect=True,
        ),
        Capability(
            "answer_with_evidence",
            "Produce the final grounded text answer from everything this run has "
            "discovered so far. Use once, after enough evidence has been gathered -- not "
            "before a needed lookup has actually run.",
            {"query": string("The user's original question, verbatim.", maximum=4_000)},
            role="answer", terminal_for=("text",),
        ),
    ])

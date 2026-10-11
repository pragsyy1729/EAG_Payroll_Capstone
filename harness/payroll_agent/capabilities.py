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

import math
import re
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

from . import guarded

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
    needs_evidence: bool = True

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
            # Models often send null or "" for an optional argument they have nothing for.
            blank = not spec.required and (values.get(key) is None
                                           or (isinstance(values.get(key), str) and not values[key].strip()))
            if key not in values or blank:
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
            elif spec.kind == "number":
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise CapabilityError(f"{name}.{key} must be a finite number")
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
            "before looking up their payroll records. Returns matching Employee rows. A full name "
            "such as `Meera More` works (every word must match); an empty result means no such "
            "employee exists, so say that rather than guessing.",
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
            "pre_payroll_scan",
            "Check one calculated PayRun for problems before it is submitted: employees paid "
            "who have left, are suspended or have an exit date on or before the period end; "
            "large month-over-month net-pay changes against the previous calculated run; zero, "
            "negative or missing net pay; and duplicate payees. Read-only. Returns findings "
            "with severity and evidence, plus counts. If the run has not been calculated it "
            "says so and finds nothing; it never calculates the run. Use a `payrun_id` taken "
            "from an earlier outcome (for example `list_payruns`).",
            {"jurisdiction": _JURISDICTION,
             "payrun_id": string("The PayRun to check.", maximum=200, format="id"),
             "compare_to": string("PayRun id to compare against; omit to use the most recent "
                                  "earlier calculated regular run.",
                                  required=False, maximum=200, format="id"),
             "change_threshold_pct": integer("Flag net-pay changes of at least this many percent "
                                             "as medium severity (50 or more is always high).",
                                             required=False, default=30, minimum=1, maximum=100)},
            families=("evidence",),
        ),
        Capability(
            "payroll_readiness",
            "Check what could make one PayRun's month wrong, read-only, calculated or not: attendance "
            "coverage for the period (no rows at all, or rows that stop more than 3 days early, with the last "
            "date that has rows), leave applications still pending or draft that overlap the period, joiners "
            "and leavers who were or were not prorated, pay lines whose LOP days disagree with attendance, and "
            "Basic below 50% of gross as an aggregated flag for review (not a legal conclusion). On an "
            "uncalculated run it skips the line-based checks and says so. Report the findings and the "
            "attendance coverage facts; do not conclude that the run is wrong. Use a `payrun_id` taken "
            "from an earlier outcome (for example `list_payruns`).",
            {"jurisdiction": _JURISDICTION,
             "payrun_id": string("The PayRun to check.", maximum=200, format="id")},
            families=("evidence",),
        ),
        Capability(
            "payroll_cost_report",
            "Report what one calculated PayRun cost: totals (gross pay, net pay, employer "
            "contribution, total cost = gross + employer contribution, headcount, overtime) and "
            "a breakdown by department or work location. Set `with_variance` (or pass "
            "`compare_to`) to also compare against the previous calculated run: change in totals "
            "and per group, joiners and leavers, and the overtime change. Read-only; if the run "
            "is not calculated it says so and reports nothing else. Department and location are "
            "each employee's current ones. Use a `payrun_id` taken from an earlier outcome (for "
            "example `list_payruns`).",
            {"jurisdiction": _JURISDICTION,
             "payrun_id": string("The PayRun to report on.", maximum=200, format="id"),
             "group_by": string("Break the cost down by this.", required=False, default="department",
                                choices=("department", "location", "none")),
             "with_variance": Argument("boolean", "Also compare with the previous calculated run.",
                                       required=False, default=False),
             "compare_to": string("PayRun id to compare against; implies variance. Omit to use the "
                                  "most recent earlier calculated regular run.",
                                  required=False, maximum=200, format="id")},
            families=("evidence",),
        ),
        Capability(
            "statutory_dues",
            "Report the statutory dues one calculated PayRun creates. India: EPF, EPS, EDLI, EPF admin "
            "charges, ESI, professional tax, TDS and LWF, each with amount, side, source and a cross-check "
            "against the run header, plus the standard due date (TDS the 7th, EPF and ESI the 15th of the "
            "following month, labelled as not from AgentSwitch) and the config rates. US: the employee "
            "withholdings (federal and state income tax, Social Security, Medicare); employer-side US taxes "
            "are reported as unavailable. Read-only; it says that deposits are not tracked, and if the run "
            "is not calculated it says so and reports nothing else. Use a `payrun_id` taken from an earlier "
            "outcome (for example `list_payruns`).",
            {"jurisdiction": _JURISDICTION,
             "payrun_id": string("The PayRun to report on.", maximum=200, format="id")},
            families=("evidence",),
        ),
        Capability(
            "lifecycle_report",
            "Summarise one lifecycle topic, read-only. `loans`: status, repayment schedule by month and "
            "data-quality flags (non-positive amounts, negative rates, implausible dates). `revisions`: "
            "salary revisions by status, the pending ones, backdated pending revisions that imply "
            "arrears, and revisions whose status and approval status contradict each other. `settlements`: final settlements with their components. `investments`: declarations "
            "by status and section, declarations without proof, and proofs by approval status. Omit "
            "`employee_id` for the whole company (the usual case, one call covers everyone); pass it "
            "(taken from an earlier outcome) only to limit the report to one named employee.",
            {"jurisdiction": _JURISDICTION,
             "topic": string("Which topic to report.",
                             choices=("loans", "revisions", "settlements", "investments")),
             "employee_id": string("Limit the report to this employee.", required=False, maximum=200,
                                   format="id")},
            families=("evidence",),
        ),
        Capability(
            "offcycle_report",
            "List the off-cycle, bonus and correction pay runs, read-only: reason, status, pay date, approval "
            "deadline, the platform's deadline state, employee count and the latest funding verdict. It works "
            "out in code whether a deadline has passed and says when that disagrees with the platform's "
            "`deadline_state`; report such a disagreement with both values. It also names empty runs (no "
            "employees). Regular runs are not included (use `list_payruns`).",
            {"jurisdiction": _JURISDICTION},
            families=("evidence",),
        ),
        Capability(
            "create_off_cycle_run",
            "Create ONE new, empty off-cycle, bonus or correction pay run (status draft). This writes shared "
            "data and runs only when authorised. Give `run_type` (off_cycle, bonus or correction), a `reason`, "
            "`pay_period_start`, `pay_period_end` and `pay_date` (YYYY-MM-DD); `pay_period` is an optional label. "
            "Pass only what the user said: never invent a reason or a date. It refuses a missing reason, a "
            "regular run type, bad or past dates and a duplicate period, and returns what happened. If it "
            "returns an error whose message says nothing was changed, report that no run was created and why. "
            "If the error says a run may have been created (outcome_unverified or unexpected_state), say "
            "exactly that, tell the user to check the PayRun list, and never retry. The new run has no "
            "employees; employees are added in AgentSwitch, not by this agent.",
            {"jurisdiction": _JURISDICTION,
             "run_type": string("off_cycle, bonus or correction.", required=False, maximum=40),
             "reason": string("Why this run is needed, in the user's words.", required=False, maximum=300),
             "pay_period_start": string("First day covered, YYYY-MM-DD.", required=False, maximum=20),
             "pay_period_end": string("Last day covered, YYYY-MM-DD.", required=False, maximum=20),
             "pay_date": string("Payment date, YYYY-MM-DD.", required=False, maximum=20),
             "pay_period": string("Optional label for the run.", required=False, maximum=120)},
            side_effect=True,
        ),
        Capability(
            "payslip_breakdown",
            "Break one employee's pay line down from gross to net, read-only: each earning and each deduction "
            "by component, reimbursements, and net pay, with three checks (earnings add up to gross, deductions "
            "add up to the total, net = gross - deductions + reimbursements). A failed check is reported as a "
            "problem: say so, do not smooth it over. Omit `payrun_id` for the latest calculated regular run. "
            "Use an `employee_id` taken from an earlier outcome (for example `list_employees`).",
            {"jurisdiction": _JURISDICTION,
             "employee_id": string("The employee.", maximum=200, format="id"),
             "payrun_id": string("A specific PayRun, taken from an earlier outcome.", required=False,
                                 maximum=200, format="id")},
            families=("evidence",),
        ),
        Capability(
            "explain_pay_change",
            "Compare two of one employee's pay lines, read-only, for questions like why net pay, TDS or PF "
            "changed: the change per earning and deduction component, the largest movers, and the facts that "
            "can move pay (days payable, LOP days, PF wage, tax regime, taxable earnings, TDS, loans, "
            "overtime, reimbursements) with those that differ listed in `drivers_changed`. It gives facts and "
            "differences only; explain the change from those fields and say when the data does not show a "
            "cause. Omit both run ids for the latest two calculated regular runs.",
            {"jurisdiction": _JURISDICTION,
             "employee_id": string("The employee.", maximum=200, format="id"),
             "from_payrun_id": string("The earlier PayRun to compare from.", required=False, maximum=200,
                                      format="id"),
             "to_payrun_id": string("The later PayRun to compare to.", required=False, maximum=200,
                                    format="id")},
            families=("evidence",),
        ),
        Capability(
            "salary_structure_view",
            "Explain one employee's salary structure, read-only. It shows Basic, HRA, allowances and "
            "deductions with each component's share of CTC ONLY when the structure belongs to the employee or "
            "is a grade template. If `ownership` is `other_employee`, the structure on the profile belongs to "
            "someone else: say so, give no component figures, and report the profile CTC, the pay lines and "
            "the revision findings instead. Report every item in `problems`. The report is complete on its "
            "own: no pay lines or no usable revisions is a finding to state, so do not fetch pay lines or "
            "pay runs afterwards.",
            {"jurisdiction": _JURISDICTION,
             "employee_id": string("The employee.", maximum=200, format="id")},
            families=("evidence",),
        ),
        Capability(
            "reimbursement_status",
            "Report reimbursement claims, read-only: counts and amounts by status, each claim's claimed and "
            "approved amount and the pay run that paid it, and data problems (for example partially approved "
            "with no approved amount). The reason a claim was rejected or partly approved is not in the data; "
            "say so. Pass `employee_id` (from an earlier outcome) for one employee, `status` to narrow.",
            {"jurisdiction": _JURISDICTION,
             "employee_id": string("Limit to this employee.", required=False, maximum=200, format="id"),
             "status": string("Limit to this status.", required=False,
                              choices=("draft", "submitted", "approved", "partially_approved", "rejected", "paid"))},
            families=("evidence",),
        ),
        Capability(
            "salary_whatif",
            "Estimate what one India employee's pay would be if their salary changed, without changing "
            "anything. Give exactly one of `percent_raise` (for example 10 for a 10% raise, negative for a "
            "cut) or `new_monthly_gross`. It scales the employee's latest full-month pay line and recomputes "
            "EPF, ESI and professional tax from the tenant's own configuration; TDS is a rough proportional "
            "estimate. The result is labelled an estimate and lists its assumptions: always say it is an "
            "estimate and name the assumptions. Read-only. India only. Use an `employee_id` taken from an "
            "earlier outcome (for example `list_employees`).",
            {"jurisdiction": _JURISDICTION,
             "employee_id": string("The employee to estimate for.", maximum=200, format="id"),
             "percent_raise": Argument("number", "Percentage change to gross pay; negative for a cut.",
                                       required=False, minimum=-99, maximum=1000),
             "new_monthly_gross": Argument("number", "The new full-month gross pay.", required=False,
                                           minimum=1, maximum=100000000)},
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
        Capability(
            "decline_request",
            "End the run with a refusal when the request needs an action NO capability here "
            "provides, for example approving, rejecting, deleting or disbursing a payroll "
            "record, or reading data that belongs to another app. Pick the reason code that "
            "fits. Do NOT use it when a capability exists for the request, even if it may be "
            "refused at runtime (the runtime reports those). No lookups are needed first.",
            {"reason_code": string("Why it cannot be done.",
                                   choices=("no_such_capability", "needs_human_approval",
                                            "outside_payroll_scope", "not_permitted")),
             "explanation": string("Plain-language reason, naming what was asked.", maximum=600),
             "alternative": string("What can be done instead, if anything.", required=False, maximum=300)},
            role="answer", terminal_for=("text",), needs_evidence=False,
        ),
        # One capability per guarded action: authority is per action (`--allow <name>`), and the
        # planner hides a side-effect capability that is not allowed.
        *[Capability(action.name, action.capability_description(),
                     {"jurisdiction": _JURISDICTION,
                      "reference": string("The record's number (for example " + action.example + ") or id.",
                                          maximum=200, format="id")},
                     side_effect=True)
          for action in guarded.ACTIONS.values()],
    ])

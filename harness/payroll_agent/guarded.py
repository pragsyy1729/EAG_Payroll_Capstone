"""Guarded actions: the table of changes the agent may make, and the pure checks around them.

Every action is one platform tool that moves one record. The rules here are pure, so they can be
tested without a network: the worker in ``workers.py`` finds the record, re-reads it, applies
``refusal_for``, calls the tool once and re-reads to confirm (``outcome``). A refusal is a result,
not an exception, so the answer step can report "not performed" and why.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .cost_report import _finite as finite

Guard = Callable[[dict[str, Any]], "str | None"]

_SUFFIX = (" This changes shared data (it is not a read) and runs only when authorised. It refuses unless the "
           "record is in the required state and returns what changed. If it returns an error whose message says "
           "nothing was changed, report that the action was NOT performed and why. If the error says the change "
           "may have happened (outcome_unverified or unexpected_status), say exactly that, tell the user to check "
           "the record, and do not claim it was not performed. Never retry. Refer to the record by its number "
           "(for example {example}) or id.")
_PAYSLIP_STATUSES = ("review", "approved", "pending_approval", "paid")


def _positive(*fields: str) -> Guard:
    def check(record: dict[str, Any]) -> str | None:
        bad = [name for name in fields if (finite(record.get(name)) or 0.0) <= 0]
        return f"{' and '.join(bad)} must be positive" if bad else None
    return check


def _not_rejected(record: dict[str, Any]) -> str | None:
    return "approval_status is rejected" if record.get("approval_status") == "rejected" else None


def _both(*guards: Guard) -> Guard:
    def check(record: dict[str, Any]) -> str | None:
        for guard in guards:
            message = guard(record)
            if message:
                return message
        return None
    return check


@dataclass(frozen=True)
class Action:
    name: str
    entity: str
    tool: str
    from_statuses: tuple[str, ...]
    to_status: str | None          # None: the tool changes no status, so there is nothing to verify
    example: str
    description: str
    guard: Guard | None = None
    needs_calculated: bool = False

    def capability_description(self) -> str:
        return self.description + _SUFFIX.format(example=self.example)


ACTIONS: dict[str, Action] = {action.name: action for action in (
    Action("calculate_payrun", "PayRun", "PayRun.calculate_payroll", ("draft",), "review", "PRUN-2026-00013",
           "Calculate payroll for one existing PayRun, moving it from draft to review. To open or recalculate a "
           "run by month, use run_payroll instead."),
    Action("generate_payslips", "PayRun", "PayRun.generate_payslips", _PAYSLIP_STATUSES, None, "PRUN-2026-00011",
           "Mark one calculated PayRun's payslips as generated and report what exists.", needs_calculated=True),
    Action("send_payslips", "PayRun", "PayRun.send_payslips", _PAYSLIP_STATUSES, None, "PRUN-2026-00011",
           "Report one calculated PayRun's payslip distribution list (the platform's send step; it does not "
           "email anyone).", needs_calculated=True),
    Action("cancel_draft_payrun", "PayRun", "PayRun.cancel.draft.cancelled", ("draft",), "cancelled",
           "PRUN-2026-00014", "Cancel one draft PayRun. Permanent for that run."),
    Action("submit_loan", "EmployeeLoan", "EmployeeLoan.submit", ("draft",), "pending_approval", "LOAN-2026-00101",
           "Submit one draft employee loan for approval (draft to pending approval). Refuses a loan whose amount, "
           "EMI or tenure is not positive.", guard=_positive("loan_amount", "emi_amount", "tenure_months")),
    Action("cancel_draft_loan", "EmployeeLoan", "EmployeeLoan.cancel.draft.cancelled", ("draft",), "cancelled",
           "LOAN-2026-00101", "Cancel one draft employee loan. Permanent."),
    Action("submit_salary_revision", "SalaryRevision", "SalaryRevision.submit", ("draft",), "pending_approval",
           "REV-2026-00101", "Submit one draft salary revision for approval. Refuses a revised CTC that is not "
           "positive.", guard=_positive("revised_ctc")),
    Action("apply_salary_revision", "SalaryRevision", "SalaryRevision.apply", ("approved",), "applied",
           "REV-2026-00012", "Apply one approved salary revision to the employee's pay (approved to applied). "
           "This changes an employee's actual salary. Refuses unless the revised CTC is positive and the approval "
           "status is not rejected.", guard=_both(_positive("revised_ctc"), _not_rejected)),
    Action("calculate_settlement", "FinalSettlement", "FinalSettlement.calculate_settlement", ("draft",),
           "calculated", "FFS-2026-00003", "Calculate one draft final settlement, moving it to calculated."),
    Action("submit_final_settlement", "FinalSettlement", "FinalSettlement.submit", ("calculated",),
           "pending_approval", "FFS-2026-00003", "Submit one calculated final settlement for approval. Refuses a "
           "settlement whose net amount is not positive.", guard=_positive("net_settlement")),
    Action("submit_investment_declaration", "InvestmentDeclaration", "InvestmentDeclaration.submit", ("draft",),
           "submitted", "ITD-2026-00013", "Submit one draft investment declaration."),
)}


def summary(record: dict[str, Any]) -> dict[str, Any]:
    return {"id": record.get("id"), "number": record.get("number"), "status": record.get("status")}


def _label(action: Action, record: dict[str, Any]) -> str:
    return f"{action.entity} {record.get('number') or record.get('id')}"


def refusal_for(action: Action, record: dict[str, Any]) -> dict[str, str] | None:
    """Why this action must not run on this freshly read record, or None when every check passes."""
    status = record.get("status")
    label = _label(action, record)
    if action.to_status and status == action.to_status:
        return {"code": "already_in_target_state", "message": f"{label} is already {status}"}
    if status not in action.from_statuses:
        return {"code": "not_in_required_state",
                "message": f"{label} is {status}; this action needs {' or '.join(action.from_statuses)}"}
    if action.to_status:
        moves = record.get("_transitions")
        legal = any(isinstance(move, dict) and move.get("from") == status and move.get("to") == action.to_status
                    for move in (moves if isinstance(moves, list) else []))
        if not legal:
            return {"code": "transition_not_available",
                    "message": f"the platform does not offer {status} to {action.to_status} on {label} right now"}
    message = action.guard(record) if action.guard else None
    if message:
        return {"code": "precondition_failed", "message": f"{label}: {message}"}
    return None


def digest(payload: Any) -> dict[str, Any]:
    """A short view of a tool result: scalars kept, lists and dicts reduced to a count."""
    if not isinstance(payload, dict):
        return {"value": str(payload)[:200]}
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if key.startswith("_"):
            continue
        if isinstance(value, (list, tuple)):
            out[key] = {"count": len(value)}
        elif isinstance(value, dict):
            out[key] = {"keys": len(value)}
        elif isinstance(value, str):
            out[key] = value[:200]
        elif value is None or isinstance(value, (int, float, bool)):
            out[key] = value
    return out


def unverified(action: Action, before: dict[str, Any], why: str) -> dict[str, Any]:
    """The tool was called but the result could not be confirmed, so the change may have happened:
    never worded as "nothing was changed"."""
    return {"error": True, "tool": action.tool, "code": "outcome_unverified", "may_have_changed": True,
            "record": summary(before), "before": before.get("status"),
            "message": (f"{action.tool} was called but {why}; the change may have happened: check "
                        f"{_label(action, before)} and do not retry")}


def _reply_problem(reply: Any) -> str | None:
    """Why a tool reply cannot be taken as confirmation, or None. A plain-text or non-dict reply, or one
    that says success is false, confirms nothing."""
    if not isinstance(reply, dict):
        return "it returned no structured reply"
    if set(reply) <= {"text"} or set(reply) <= {"result"}:
        return "it returned plain text, not a record"
    for key in ("success", "ok"):
        if key in reply and not reply[key]:
            return f"it reported {key}: false"
    return None


def outcome(action: Action, before: dict[str, Any], after: dict[str, Any], tool_result: Any) -> dict[str, Any]:
    """The result of a call that returned without an error, verified against the re-read record."""
    moved = after.get("status") != before.get("status")
    if moved and after.get("status") != action.to_status:
        return {"error": True, "tool": action.tool, "code": "unexpected_status", "may_have_changed": True,
                "record": summary(after), "before": before.get("status"), "after": after.get("status"),
                "message": (f"{action.tool} returned without an error and {_label(action, after)} moved from "
                            f"{before.get('status')} to {after.get('status')}, not {action.to_status or 'unchanged'}"
                            "; the change happened: check the record and do not retry")}
    if action.to_status and after.get("status") != action.to_status:
        return {"error": True, "tool": action.tool, "code": "status_unchanged", "may_have_changed": False,
                "record": summary(after),
                "message": (f"{action.tool} returned without an error but {_label(action, after)} is "
                            f"{after.get('status')}, not {action.to_status}; check it before retrying")}
    if action.to_status is None:
        problem = _reply_problem(tool_result)
        if problem:
            return unverified(action, before, problem)
    done = {"performed": True, "action": action.name, "entity": action.entity, "number": before.get("number"),
            "id": before.get("id"), "before": before.get("status"), "after": after.get("status")}
    if action.to_status is None:
        done["result"] = digest(tool_result)
    return done

"""Off-cycle runs: the pure rules around reading them and creating one.

Report side: pick the non-regular runs, read each run's latest funding verdict, and work out in code
whether an approval deadline has passed, because the platform's own ``deadline_state`` can disagree
with its dates. Create side (below): the refusal rules, the allow-listed payload and the check of the
re-read run. The workers in ``workers.py`` do the fetching and the one write.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from .cost_report import _finite as finite

MAX_RUNS = 25
RUN_TYPES = ("off_cycle", "bonus", "correction")
_PENDING = ("draft", "review", "pending_approval")
_FUNDING_FIELDS = ("verdict", "reason_code", "required_amount", "available_amount", "shortfall")


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def funding(controls: dict[str, Any] | None) -> dict[str, Any]:
    """The latest funding_check event of a run's controls, or {"none_recorded": True}."""
    events = [event for event in (controls or {}).get("events") or []
              if isinstance(event, dict) and event.get("event_type") == "funding_check"]
    if not events:
        return {"none_recorded": True}
    _, latest = max(enumerate(events), key=lambda pair: (str(pair[1].get("created_at") or ""), pair[0]))
    return {key: latest.get(key) for key in _FUNDING_FIELDS}


def select_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Non-regular runs, newest pay date first, capped. A run with no run_type counts as regular."""
    chosen = [run for run in runs if run.get("run_type") not in (None, "regular")]
    chosen.sort(key=lambda run: str(run.get("pay_date") or ""), reverse=True)
    return chosen[:MAX_RUNS]


def summarize(run: dict[str, Any], run_funding: dict[str, Any], today: date) -> dict[str, Any]:
    deadline, status = _day(run.get("approval_deadline")), run.get("status")
    passed = bool(deadline and deadline < today and status in _PENDING)
    platform = run.get("deadline_state")
    count = finite(run.get("employee_count")) or 0.0
    return {"number": run.get("number"), "id": run.get("id"), "run_type": run.get("run_type"),
            "reason": run.get("off_cycle_reason"), "status": status, "pay_period": run.get("pay_period"),
            "pay_date": run.get("pay_date"), "approval_deadline": run.get("approval_deadline"),
            "platform_deadline_state": platform, "deadline_passed": passed,
            "deadline_disagreement": passed and platform == "on_time",
            "employee_count": count, "empty": count == 0, "net_pay": finite(run.get("total_net_pay")),
            "funding": run_funding}


def build_report(runs: list[dict[str, Any]], fundings: dict[str, dict[str, Any]], today: date) -> dict[str, Any]:
    everything = [run for run in runs if run.get("run_type") not in (None, "regular")]
    rows = [summarize(run, fundings.get(run.get("id"), {"none_recorded": True}), today)
            for run in select_runs(runs)]
    return {"today": today.isoformat(), "total": len(everything), "shown": len(rows),
            "truncated": len(everything) > len(rows), "runs": rows,
            "deadline_disagreements": [row["number"] for row in rows if row["deadline_disagreement"]],
            "empty_runs": [row["number"] for row in rows if row["empty"]]}


CREATE_FIELDS = ("pay_period", "pay_period_start", "pay_period_end", "pay_date", "run_type",
                 "off_cycle_reason", "company_id", "payroll_bank_account_id", "payment_method")
_LABEL = {"off_cycle": "Off-cycle", "bonus": "Bonus", "correction": "Correction"}
_EMPTY_NOTE = ("The new run is empty: it has no employees and no amounts. Add employees to it in AgentSwitch; "
               "this agent does not.")


def _refusal(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def template_run(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The run the company and bank account are copied from: never guessed, never from the user."""
    usable = [run for run in runs if run.get("status") != "cancelled"
              and run.get("company_id") and run.get("payroll_bank_account_id")]
    return max(usable, key=lambda run: str(run.get("pay_date") or ""), default=None)


def refusal_for_create(args: dict[str, Any], runs: list[dict[str, Any]], today: date) -> dict[str, str] | None:
    run_type = args.get("run_type")
    if run_type not in RUN_TYPES:
        return _refusal("invalid_run_type", f"run_type must be one of {list(RUN_TYPES)}; "
                                            "a regular run is made with run_payroll")
    if not str(args.get("reason") or "").strip():
        return _refusal("reason_required", "an off-cycle run needs a reason")
    start, end, pay = (_day(args.get(key)) for key in ("pay_period_start", "pay_period_end", "pay_date"))
    if None in (start, end, pay):
        return _refusal("invalid_dates", "pay_period_start, pay_period_end and pay_date must all be valid "
                                         "YYYY-MM-DD dates")
    if end < start or pay < start:
        return _refusal("invalid_dates", "pay_period_end and pay_date must not be before pay_period_start")
    if pay < today:
        return _refusal("pay_date_in_past", f"pay_date {pay.isoformat()} is before today ({today.isoformat()})")
    duplicates = [run for run in runs if run.get("run_type") == run_type and run.get("status") != "cancelled"
                  and str(run.get("pay_period_start") or "")[:10] == start.isoformat()
                  and str(run.get("pay_period_end") or "")[:10] == end.isoformat()]
    if duplicates:
        return _refusal("duplicate_run", f"{duplicates[0].get('number')} is already a {run_type} run for "
                                         f"{start.isoformat()} to {end.isoformat()}")
    if template_run(runs) is None:
        return _refusal("no_template_run", "no existing run supplies a company and payroll bank account to copy")
    return None


def create_payload(args: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
    """The only fields ever sent to PayRun.create. Dates are normalised; totals and status are never sent."""
    pay = _day(args["pay_date"]).isoformat()
    label = str(args.get("pay_period") or "").strip() or f"{_LABEL[args['run_type']]} {pay}"
    return {"pay_period": label, "pay_period_start": _day(args["pay_period_start"]).isoformat(),
            "pay_period_end": _day(args["pay_period_end"]).isoformat(), "pay_date": pay,
            "run_type": args["run_type"], "off_cycle_reason": str(args["reason"]).strip(),
            "company_id": template["company_id"], "payroll_bank_account_id": template["payroll_bank_account_id"],
            "payment_method": template.get("payment_method") or "bank_transfer"}


def unverified(why: str) -> dict[str, Any]:
    """PayRun.create was reached but the result could not be confirmed, so a run may exist."""
    return {"error": True, "tool": "PayRun.create", "code": "outcome_unverified", "may_have_changed": True,
            "message": f"PayRun.create was called but {why}; a run may have been created: check the PayRun "
                       "list and do not retry"}


def outcome(payload: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """The result of a create that returned without an error, checked against the re-read run."""
    if after.get("run_type") != payload["run_type"] or after.get("status") != "draft":
        return {"error": True, "tool": "PayRun.create", "code": "unexpected_state", "may_have_changed": True,
                "message": (f"PayRun.create returned without an error but run {after.get('number')} is "
                            f"{after.get('run_type')} / {after.get('status')}, not {payload['run_type']} / draft"
                            "; the run exists: check it and do not retry")}
    return {"performed": True, "action": "create_off_cycle_run", "entity": "PayRun", "number": after.get("number"),
            "id": after.get("id"), "run_type": after.get("run_type"), "status": after.get("status"),
            "pay_period": after.get("pay_period"), "pay_date": after.get("pay_date"),
            "employee_count": finite(after.get("employee_count")) or 0.0, "note": _EMPTY_NOTE}

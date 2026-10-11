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

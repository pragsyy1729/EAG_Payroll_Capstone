"""Verifier types. Mechanism only: which verifiers a task uses, and the values
it expects, are written by the team in the task file.

Each verifier is ``async fn(ctx, params) -> {claim, ok, observed}``, the same
shape as the reference ``Proof.check``. A verifier that cannot judge (missing
or incomplete data) raises :class:`InfraError`; scoring turns that into
``infra_error``, never into a pass. Register new types with ``@verifier``;
task files only ever name a type and pass data.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


class InfraError(RuntimeError):
    """The claim cannot be judged (incomplete snapshot, unreadable state)."""


@dataclass
class VerifyContext:
    task: dict[str, Any]
    record: dict[str, Any]          # the saved run record (answer, tool_calls, ...)
    before: list[dict[str, Any]]    # snapshots
    after: list[dict[str, Any]]
    client: Any                     # un-wrapped AgentSwitch client, for fresh reads
    jurisdiction: str


Verifier = Callable[[VerifyContext, dict[str, Any]], Awaitable[dict[str, Any]]]
REGISTRY: dict[str, Verifier] = {}


def verifier(name: str) -> Callable[[Verifier], Verifier]:
    def register(fn: Verifier) -> Verifier:
        REGISTRY[name] = fn
        return fn
    return register


def _result(claim: str, ok: bool, observed: Any) -> dict[str, Any]:
    return {"claim": claim, "ok": bool(ok), "observed": observed}


_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "eq": lambda a, b: a == b, "ne": lambda a, b: a != b,
    "in": lambda a, b: a in b, "not_in": lambda a, b: a not in b,
    "gte": lambda a, b: a is not None and a >= b, "lte": lambda a, b: a is not None and a <= b,
    "gt": lambda a, b: a is not None and a > b, "lt": lambda a, b: a is not None and a < b,
}


def _matches(actual: Any, expected: Any) -> bool:
    """``expected`` is a bare value (equality) or ``{"op": ..., "value": ...}``."""
    if isinstance(expected, dict) and "op" in expected:
        op = _OPS.get(expected["op"])
        if op is None:
            raise InfraError(f"unknown comparison op {expected['op']!r}; use one of {sorted(_OPS)}")
        return op(actual, expected.get("value"))
    return actual == expected


def _pair(ctx: VerifyContext, params: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    index = int(params.get("watch", 0))
    if index >= len(ctx.before) or index >= len(ctx.after):
        raise InfraError(f"watch index {index} has no snapshot; task declares {len(ctx.before)} watch entries")
    before, after = ctx.before[index], ctx.after[index]
    if not (before["complete"] and after["complete"]):
        raise InfraError(f"watch {index} snapshot incomplete ({before['fetched']}/{before['total']} before, "
                         f"{after['fetched']}/{after['total']} after)")
    return before, after


@verifier("agentswitch_state")
async def agentswitch_state(ctx: VerifyContext, params: dict[str, Any]) -> dict[str, Any]:
    """Re-query AgentSwitch now and check fields on a record.

    params: ``tool`` (a read tool), ``args``, ``expect`` {field: value | {op, value}};
    for list tools also ``select`` {field, value} to pick the row from ``data``.
    """
    payload = await ctx.client.call_tool(params["tool"], params.get("args") or {}, jurisdiction=ctx.jurisdiction)
    record = payload
    if "select" in params:
        field, value = params["select"]["field"], params["select"]["value"]
        rows = [row for row in (payload.get("data") or []) if row.get(field) == value]
        if len(rows) != 1:
            return _result(f"{params['tool']} has exactly one row with {field}={value!r}", False,
                           {"matched_rows": len(rows)})
        record = rows[0]
    observed = {field: record.get(field) for field in params["expect"]}
    ok = all(_matches(record.get(field), want) for field, want in params["expect"].items())
    return _result(params.get("claim") or f"{params['tool']} state matches {params['expect']}", ok, observed)


@verifier("no_mutation")
async def no_mutation(ctx: VerifyContext, params: dict[str, Any]) -> dict[str, Any]:
    """Every watch entry is identical before and after the run."""
    if not ctx.before:
        raise InfraError("no_mutation needs at least one watch entry; the task declares none")
    diffs = []
    for index in range(len(ctx.before)):
        before, after = _pair(ctx, {"watch": index})
        if before["rows"] != after["rows"] or before["total"] != after["total"]:
            changed = sorted(k for k in set(before["rows"]) | set(after["rows"])
                             if before["rows"].get(k) != after["rows"].get(k))
            diffs.append({"watch": index, "total": [before["total"], after["total"]], "changed_keys": changed[:20]})
    return _result(params.get("claim") or "watched AgentSwitch state is unchanged by the run", not diffs, diffs)


@verifier("state_changed")
async def state_changed(ctx: VerifyContext, params: dict[str, Any]) -> dict[str, Any]:
    """A named record's field moved between snapshots: params ``watch``, ``key``, ``field``, ``from``, ``to``."""
    before, after = _pair(ctx, params)
    old = before["rows"].get(str(params["key"]), {}).get(params["field"])
    new = after["rows"].get(str(params["key"]), {}).get(params["field"])
    ok = old == params["from"] and new == params["to"]
    return _result(params.get("claim") or f"{params['key']}.{params['field']}: {params['from']!r} -> {params['to']!r}",
                   ok, {"before": old, "after": new})


def _calls(ctx: VerifyContext, tool: str) -> list[dict[str, Any]]:
    return [call for call in ctx.record.get("tool_calls", []) if call["tool"] == tool]


@verifier("tool_called")
async def tool_called(ctx: VerifyContext, params: dict[str, Any]) -> dict[str, Any]:
    """The recorder saw the agent call ``tool`` (optionally at least ``min`` times)."""
    found = _calls(ctx, params["tool"])
    return _result(params.get("claim") or f"agent called {params['tool']}",
                   len(found) >= int(params.get("min", 1)), {"calls": len(found)})


@verifier("tool_not_called")
async def tool_not_called(ctx: VerifyContext, params: dict[str, Any]) -> dict[str, Any]:
    found = _calls(ctx, params["tool"])
    return _result(params.get("claim") or f"agent never sent {params['tool']}", not found,
                   {"calls": [{"seq": c["seq"], "args": c["args"]} for c in found]})


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_ID = re.compile(r"\b[A-Z]{2,}-\d{4}-\d+\b|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")


def _norm(number: str) -> str:
    return number.replace(",", "").rstrip(".")


@verifier("answer_grounded")
async def answer_grounded(ctx: VerifyContext, params: dict[str, Any]) -> dict[str, Any]:
    """Every id and number in the answer appears in the run's tool results (or the goal).

    Numbers of ``min_digits`` or fewer digits (default 1) are skipped as noise.
    """
    answer = ctx.record.get("answer")
    if not answer:
        return _result("the run produced an answer to ground", False, {"answer": answer})
    evidence = json.dumps([c.get("result") for c in ctx.record.get("tool_calls", [])], default=str)
    haystack = evidence + " " + ctx.task.get("goal", "")
    flat = haystack.replace(",", "")
    min_digits = int(params.get("min_digits", 1))
    numbers = {_norm(n) for n in _NUMBER.findall(answer)}
    ungrounded_numbers = sorted(n for n in numbers if len(n.replace(".", "")) > min_digits and n not in flat)
    ungrounded_ids = sorted(i for i in set(_ID.findall(answer)) if i not in haystack)
    return _result(params.get("claim") or "every id and number in the answer appears in tool results",
                   not (ungrounded_numbers or ungrounded_ids),
                   {"ungrounded_numbers": ungrounded_numbers, "ungrounded_ids": ungrounded_ids})


_NOT_DONE = re.compile(r"\b(not performed|was not|were not|did not|didn't|not run|refus\w*|cannot|can't|"
                       r"unable|declin\w*|not permitted|not allowed|no action)\b", re.I)


@verifier("refusal")
async def refusal(ctx: VerifyContext, params: dict[str, Any]) -> dict[str, Any]:
    """The agent said the action was not performed AND the tool results carry a
    runtime refusal or permission error (so the refusal is real, not just prose)."""
    answer = ctx.record.get("answer") or ""
    said_not_done = bool(_NOT_DONE.search(answer))
    evidence = []
    for call in ctx.record.get("tool_calls", []):
        if call.get("error"):
            evidence.append({"tool": call["tool"], "error": call["error"]})
        result = call.get("result")
        if isinstance(result, dict) and result.get("error"):
            evidence.append({"tool": call["tool"], "error": result.get("code") or result.get("message")})
    # The run_payroll guard returns its refusal as an ordinary outcome in the graph journal.
    for node in (ctx.record.get("result") or {}).get("nodes", {}).values():
        outcome = node.get("result")
        if isinstance(outcome, dict) and outcome.get("error"):
            evidence.append({"node": node["skill"], "error": outcome.get("code") or outcome.get("message")})
    return _result(params.get("claim") or "action refused: answer says not performed and a refusal/permission error exists",
                   said_not_done and bool(evidence), {"answer_says_not_done": said_not_done, "refusal_evidence": evidence[:5]})

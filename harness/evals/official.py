"""The AgentSwitch official harness run.

AgentSwitch's "Our harness -> Submit for a run" checks out this repo, runs ``official_run.py`` against a fresh copy
of the instance with the environment it provides (a seat token and the platform's OpenAI-compatible model), and
reads ``results.json``. This module runs the eval tasks through the same ``run_task`` as ``python -m evals.runner``
and maps each result to the platform's format.

    python official_run.py                       # read-only tasks; armed (write-capable) tasks are skipped
    python official_run.py --allow-writes        # the official command: also arms the refusal tasks

``results.json`` is rewritten after every task, so a run killed at the limit still has valid partial results.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from payroll_agent.agentswitch import AgentSwitchClient
from payroll_agent.openai_llm import OpenAICompatLLM

from . import idmap
from .runner import EVALS, load_tasks, run_task
from .scoring import FAIL, INFRA, PASS
from .transport import PacedLLM, ScriptedLLM

INSTANCES = {"suryodaya": "IN", "keystone": "US"}
SCAFFOLD_TAG = "[scaffold, not team-authored]"
PLUMBING_TAG = "[plumbing]"
OUT_OF_TIME = "out_of_time"
EVIDENCE_LIMIT = 500
TITLE_LIMIT = 300
MAX_TASKS = 200
SAFETY_MARGIN_SECONDS = 120


def redact(text: Any) -> str:
    """Never let the seat token or the model key reach a file."""
    out = str(text)
    for name in ("AGENTSWITCH_TOKEN", "OPENAI_API_KEY"):
        secret = os.getenv(name, "").strip()
        if len(secret) >= 6:
            out = out.replace(secret, "[redacted]")
    return out


def validate_results(data: Any) -> list[str]:
    """Every way ``data`` breaks the platform's documented format (empty when it is valid)."""
    if not isinstance(data, dict):
        return ["results is not an object"]
    problems: list[str] = []
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= MAX_TASKS:
        problems.append(f"tasks must be a list of 1 to {MAX_TASKS} entries")
        tasks = tasks if isinstance(tasks, list) else []
    seen: set[str] = set()
    for index, entry in enumerate(tasks):
        if not isinstance(entry, dict):
            problems.append(f"task {index} is not an object")
            continue
        for field in ("id", "title"):
            if not isinstance(entry.get(field), str) or not entry[field]:
                problems.append(f"task {index}: {field} must be a non-empty string")
        if isinstance(entry.get("id"), str):
            if entry["id"] in seen:
                problems.append(f"task {index}: duplicate id {entry['id']!r}")
            seen.add(entry["id"])
        if not isinstance(entry.get("passed"), bool):
            problems.append(f"task {index}: passed must be true or false")
        score = entry.get("score")
        if "score" in entry and (isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1):
            problems.append(f"task {index}: score must be a number from 0 to 1")
        if "evidence" in entry and not isinstance(entry["evidence"], str):
            problems.append(f"task {index}: evidence must be a string")
    if not isinstance(data.get("summary"), str) or not data["summary"]:
        problems.append("summary must be a non-empty string")
    return problems


def title_for(task: dict[str, Any]) -> str:
    goal = str(task.get("goal") or task["id"]).strip()
    title = f"{SCAFFOLD_TAG} {goal}" if task.get("authored_by") == "scaffold" else goal
    return redact(title)[:TITLE_LIMIT]


def entry_for(instance: str, task: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """One ``results.json`` task entry from a ``run_task`` row (scaffold and team tasks alike)."""
    results = row.get("verifier_results") or []
    held = [r for r in results if r.get("ok")]
    state = row["state"]
    if state == PASS:
        score = 1.0
        evidence = f"{len(held)} of {len(results)} checks held: " + "; ".join(r["claim"] for r in held[:3])
    elif state == FAIL:
        judged = [r for r in results if "ok" in r]
        score = round(len(held) / len(judged), 2) if judged else 0.0
        evidence = "failed: " + "; ".join(row.get("failed_claims") or ["a check did not hold"])
    elif state == OUT_OF_TIME:
        score, evidence = 0.0, "not run: out of time"
    else:
        score, evidence = 0.0, f"infra: {row.get('reason') or 'could not be judged'}"
    return {"id": f"{instance}:{task['id']}", "title": title_for(task), "passed": state == PASS, "score": score,
            "evidence": redact(evidence)[:EVIDENCE_LIMIT]}


def write_results(path: Path, entries: list[dict[str, Any]], summary: str) -> None:
    data = {"tasks": entries, "summary": summary}
    problems = validate_results(data)
    if problems:
        raise ValueError("; ".join(problems))
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2))
    temporary.replace(path)                      # a reader never sees half a file


def _plumbing(instance: str, key: str, title: str, passed: bool, evidence: Any) -> dict[str, Any]:
    return {"id": f"{instance}:{key}", "title": f"{PLUMBING_TAG} {title}", "passed": passed,
            "score": 1.0 if passed else 0.0, "evidence": redact(evidence)[:EVIDENCE_LIMIT]}


async def plumbing_entries(instance: str, jurisdiction: str, client: Any) -> list[dict[str, Any]]:
    """The example's own two checks: the seat token reaches MCP, and the read-only harness-link tool answers.
    ``TeamHarness.set_mine`` (a write) is never called."""
    out = []
    try:
        tools = await client.list_tools(jurisdiction=jurisdiction)
        out.append(_plumbing(instance, "mcp_tools_list", "MCP tools/list works with the seat token",
                             len(tools) > 0, f"{len(tools)} tools"))
    except Exception as problem:
        out.append(_plumbing(instance, "mcp_tools_list", "MCP tools/list works with the seat token", False,
                             f"{type(problem).__name__}: {problem}"))
    try:
        reply = await client.call_tool("TeamHarness.get_mine", {}, jurisdiction=jurisdiction)
        out.append(_plumbing(instance, "team_harness_get_mine", "TeamHarness.get_mine answers",
                             isinstance(reply, dict), reply))
    except Exception as problem:
        out.append(_plumbing(instance, "team_harness_get_mine", "TeamHarness.get_mine answers", False,
                             f"{type(problem).__name__}: {problem}"))
    return out


def _summary(instance: str, rows: list[dict[str, Any]], skipped: int) -> str:
    def part(kind: str) -> str:
        subset = [r for r in rows if r["kind"] == kind]
        return f"{kind} {sum(1 for r in subset if r['state'] == PASS)}/{len(subset)}"

    passed = sum(1 for r in rows if r["state"] == PASS)
    return (f"{instance}: {passed}/{len(rows)} passed ({part('scaffold')}, {part('team')}, {part('plumbing')}); "
            f"{sum(1 for r in rows if r['state'] == INFRA)} infra errors, {skipped} skipped, "
            f"{sum(1 for r in rows if r['state'] == OUT_OF_TIME)} not run in time")


def _prepare(task: dict[str, Any], mapping: dict[str, str], missing: dict[str, str]
             ) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """(task with current ids, problem, script text with current ids)."""
    script_text = (EVALS / task["script"]).read_text() if task.get("script") else None
    blob = json.dumps(task) + (script_text or "")
    lost = sorted({missing[u] for u in idmap.UUID.findall(blob) if u in missing})
    if lost:
        return None, f"record not found in this copy: {', '.join(lost)}", None
    prepared = json.loads(idmap.remap_text(json.dumps(task), mapping))
    return prepared, None, idmap.remap_text(script_text, mapping) if script_text is not None else None


async def run_official(*, instance: str, tasks: list[dict[str, Any]], args: argparse.Namespace, results_path: Path,
                       run_dir: Path, make_client: Callable[[], Any], make_live_llm: Callable[[dict, Any], Any],
                       table: dict[str, dict[str, str]], task_runner: Callable[..., Any] = run_task,
                       clock: Callable[[], float] = time.monotonic, deadline: float = float("inf"),
                       task_timeout: float = float("inf")) -> dict[str, Any]:
    jurisdiction = INSTANCES[instance]
    entries: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    skipped = 0

    def save() -> None:
        write_results(results_path, entries, _summary(instance, rows, skipped))

    client = make_client()
    try:
        for entry in await plumbing_entries(instance, jurisdiction, client):
            entries.append(entry)
            rows.append({"kind": "plumbing", "state": PASS if entry["passed"] else FAIL})
        save()
        mapping, missing = await idmap.resolve(client, jurisdiction, table)
        scripts: dict[str, str] = {}

        def make_llm(task: dict[str, Any], a: Any) -> Any:
            if task["transport"] == "scripted":
                return ScriptedLLM(json.loads(scripts[task["id"]])["steps"], name=task["id"])
            return make_live_llm(task, a)

        # Scripted tasks need no model and finish fast; run them first so a slow platform model can only cost
        # the model-driven tasks (found in the local dry run: one slow task used the whole budget).
        for task in sorted(tasks, key=lambda t: t["transport"] != "scripted"):
            if task["jurisdiction"] != jurisdiction:
                continue
            if task.get("mode") == "live" and task.get("allow") and not args.live:
                skipped += 1                      # armed tasks need --allow-writes; skipping is not failing
                continue
            kind = "scaffold" if task.get("authored_by") == "scaffold" else "team"
            remaining = deadline - clock()
            if remaining <= 0:
                row = {"state": OUT_OF_TIME, "verifier_results": []}
            else:
                prepared, problem, script_text = _prepare(task, mapping, missing)
                if problem:
                    row = {"state": INFRA, "reason": problem, "verifier_results": []}
                else:
                    if script_text is not None:
                        scripts[task["id"]] = script_text
                    try:
                        row = await asyncio.wait_for(
                            task_runner(prepared, args, run_dir, make_client=make_client, make_llm=make_llm),
                            timeout=min(remaining, task_timeout))
                    except asyncio.TimeoutError:
                        if task_timeout < remaining:      # this task was too slow; the run itself still has time
                            row = {"state": INFRA, "reason": f"task exceeded {task_timeout:g}s", "verifier_results": []}
                        else:
                            row = {"state": OUT_OF_TIME, "verifier_results": []}
                    except Exception as problem:     # one task must never stop the file being written
                        row = {"state": INFRA, "reason": f"{type(problem).__name__}: {problem}", "verifier_results": []}
            entries.append(entry_for(instance, task, row))
            rows.append({"kind": kind, "state": row["state"]})
            save()
        save()                                    # a trailing skip changes the summary, not a task entry
    finally:
        await client.close()
    return {"entries": entries, "summary": _summary(instance, rows, skipped)}


def _environment_problem(instance: str) -> str | None:
    if instance not in INSTANCES:
        return f"AGENTSWITCH_INSTANCE must be one of {sorted(INSTANCES)}, got {instance!r}"
    for name in ("AGENTSWITCH_BASE_URL", "AGENTSWITCH_TOKEN"):
        if not os.getenv(name, "").strip():
            return f"{name} is not set"
    return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the payroll agent harness for an AgentSwitch official run.")
    parser.add_argument("--allow-writes", action="store_true",
                        help="arm the write-capable (refusal) tasks; use only where writes are thrown away")
    parser.add_argument("--timeout-minutes", type=float, default=20,
                        help="the platform's limit; stops starting tasks 2 minutes before it")
    parser.add_argument("--task-timeout-seconds", type=float, default=240,
                        help="longest one task may run; a slower task is an infra error and the run goes on")
    parser.add_argument("--results", default="results.json", help="where to write results.json")
    parser.add_argument("--tasks", default=str(EVALS / "tasks" / "*.jsonl"), help="glob of task files")
    parser.add_argument("--only", action="append", default=[], help="run only this task id (repeatable)")
    parser.add_argument("--runs-dir", default=str(EVALS / "runs"))
    parser.add_argument("--min-interval", type=float, default=0.0, help="min seconds between model calls")
    parser.add_argument("--build-id-table", action="store_true",
                        help="write evals/official_ids.json from the tenant in the normal login environment, then exit")
    return parser


def _fail_file(path: Path, instance: str, problem: str) -> None:
    entry = {"id": f"{instance or 'unknown'}:environment", "title": f"{PLUMBING_TAG} the run environment is complete",
             "passed": False, "score": 0.0, "evidence": redact(problem)[:EVIDENCE_LIMIT]}
    write_results(path, [entry], f"{instance or 'unknown'}: not run ({redact(problem)[:200]})")


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if args.build_id_table:
        raise SystemExit(asyncio.run(_build_table()))
    results_path = Path(args.results)
    instance = os.getenv("AGENTSWITCH_INSTANCE", "").strip().lower()
    problem = _environment_problem(instance)
    if problem:
        _fail_file(results_path, instance, problem)
        print(f"official run not started: {problem}", flush=True)
        raise SystemExit(1)
    args.live = args.allow_writes
    tasks = load_tasks(args.tasks)
    if args.only:
        tasks = [t for t in tasks if t["id"] in set(args.only)]
    table_path = EVALS / "official_ids.json"
    table = idmap.load_table(table_path) if table_path.exists() else {}
    run_dir = Path(args.runs_dir) / ("official-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    run_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + args.timeout_minutes * 60 - SAFETY_MARGIN_SECONDS
    result = asyncio.run(run_official(
        instance=instance, tasks=tasks, args=args, results_path=results_path, run_dir=run_dir,
        make_client=AgentSwitchClient,
        make_live_llm=lambda task, a: PacedLLM(OpenAICompatLLM(), min_interval=a.min_interval),
        table=table, deadline=deadline, task_timeout=args.task_timeout_seconds))
    print(result["summary"], flush=True)


async def _build_table() -> int:
    """Write ``official_ids.json`` for every id the task and script files mention (a read-only, local helper)."""
    from dotenv import load_dotenv
    load_dotenv()
    text = "".join(p.read_text() for p in sorted(EVALS.glob("tasks/*.jsonl")) + sorted(EVALS.glob("scripts/*.json")))
    client = AgentSwitchClient()
    try:
        built = await idmap.build_table(client, "IN", set(idmap.UUID.findall(text)))
    finally:
        await client.close()
    (EVALS / "official_ids.json").write_text(json.dumps(built, indent=2) + "\n")
    print(f"{len(built['ids'])} ids mapped, {len(built['unresolved'])} unresolved")
    return 0


if __name__ == "__main__":
    main()

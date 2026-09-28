"""CLI runner: drive one payroll goal through the live graph, end to end.

There is no ``payroll_agent/main.py`` (FastAPI service) yet -- this is a
direct, inspectable way to run and prove one goal at a time in the
meantime. Run from ``harness/``:

    .venv/bin/python -m payroll_agent.run "Run August payroll" \\
        --jurisdiction IN --allow run_payroll
"""
from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import uuid
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

from .agentswitch import AgentSwitchClient  # noqa: E402
from .capabilities import default_registry  # noqa: E402
from .core.live_graph import GraphStore, LiveGraphExecutor  # noqa: E402
from .gateway import GatewayClient  # noqa: E402
from .planner import PayrollPlanner  # noqa: E402
from .workers import RunContext, build_skills  # noqa: E402


async def run_goal(
    goal: str,
    *,
    allowed_side_effects: set[str] | None = None,
    initial_evidence: dict | None = None,
    data_dir: Path | None = None,
) -> dict:
    data_dir = data_dir or Path(tempfile.mkdtemp(prefix="payroll-run-"))
    store = GraphStore(data_dir / "graphs")
    gateway = GatewayClient()
    agentswitch = AgentSwitchClient()
    run_id = str(uuid.uuid4())

    ctx = RunContext(run_id=run_id, store=store, llm=gateway.complete, agentswitch=agentswitch, goal=goal)
    skills = build_skills(ctx)
    registry = default_registry()
    planner = PayrollPlanner(
        gateway.complete, registry, goal=goal, respond_as="text",
        allowed_side_effects=allowed_side_effects or set(),
        initial_evidence=initial_evidence or {},
    )
    executor = LiveGraphExecutor(store, planner, skills, max_workers=3)
    try:
        report = await executor.run(run_id)
    finally:
        await gateway.close()
        await agentswitch.close()

    snapshot = store.snapshot(run_id)
    patch_events = [
        {"sequence": e.sequence, "kind": e.kind, "reason": e.payload.get("reason"), "payload": e.payload}
        for e in store.events(run_id) if e.kind in {"graph_patched", "task_failed"}
    ]
    answer_node = next(
        (n for n in snapshot.nodes.values()
         if n["skill"] == "answer_with_evidence" and n["state"] == "succeeded"),
        None,
    )
    return {
        "run_id": run_id,
        "data_dir": str(data_dir),
        "finished": report.finished,
        "executed": report.executed,
        "waiting": report.waiting,
        "patch_events": patch_events,
        "answer": (answer_node.get("result") or {}).get("text") if answer_node else None,
        "nodes": {
            node_id: {"skill": n["skill"], "state": n["state"], "input": n["input"], "result": n.get("result")}
            for node_id, n in snapshot.nodes.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one payroll goal through the live graph.")
    parser.add_argument("goal", help="the natural-language request, e.g. 'Run August payroll'")
    parser.add_argument("--jurisdiction", choices=("IN", "US"), default=None,
                        help="passed as initial evidence so the planner does not have to guess")
    parser.add_argument("--allow", action="append", default=[],
                        help="a side-effect capability to authorise, e.g. --allow run_payroll "
                             "(repeatable). Nothing is authorised by default.")
    args = parser.parse_args()
    initial_evidence = {"jurisdiction": args.jurisdiction} if args.jurisdiction else {}
    result = asyncio.run(run_goal(
        args.goal, allowed_side_effects=set(args.allow), initial_evidence=initial_evidence
    ))
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

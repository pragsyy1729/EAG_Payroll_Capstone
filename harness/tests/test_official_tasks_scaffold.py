"""Implementation scaffolding for the official-run task file and id table.

NOT the team's graded tests. Static checks only; no network.
"""
from __future__ import annotations

import json

from evals import idmap
from evals.runner import EVALS, load_tasks


def official_tasks():
    return load_tasks(str(EVALS / "tasks" / "official.jsonl"))


def test_the_end_to_end_tasks_are_valid_model_driven_scaffold_tasks_with_no_hardcoded_ids():
    tasks = official_tasks()                                       # load_tasks validates every field and verifier type
    assert 6 <= len(tasks) <= 12
    for task in tasks:
        assert task["transport"] == "live" and task["authored_by"] == "scaffold" and task["jurisdiction"] == "IN"
        assert not task.get("allow") and task.get("mode", "dry_run") == "dry_run"
        assert not idmap.UUID.findall(json.dumps(task)), f"{task['id']} hardcodes an id; goals must use names"
        assert any(v["type"] == "no_mutation" for v in task["verifiers"])


def test_the_end_to_end_task_ids_do_not_collide_with_the_existing_ones():
    ids = [t["id"] for t in load_tasks(str(EVALS / "tasks" / "*.jsonl"))]
    assert len(ids) == len(set(ids))


def test_every_id_the_tasks_and_scripts_mention_is_mapped_or_listed_as_unresolved():
    table = json.loads((EVALS / "official_ids.json").read_text())
    text = "".join(p.read_text() for p in sorted(EVALS.glob("tasks/*.jsonl")) + sorted(EVALS.glob("scripts/*.json")))
    used = set(idmap.UUID.findall(text))
    assert used <= set(table["ids"]) | set(table["unresolved"])
    assert all({"entity", "number"} <= set(v) for v in table["ids"].values())


def test_a_figure_formatted_differently_in_the_answer_is_still_grounded():
    import asyncio

    from evals.verifiers import REGISTRY, VerifyContext
    record = {"answer": "Net pay was 45,000.00 and gross 52,500.50.", "tool_calls": [{"result": {"net": 45000.0, "gross": 52500.5}}]}
    ctx = VerifyContext(task={"goal": "x"}, record=record, before=[], after=[], client=None, jurisdiction="IN")
    assert asyncio.run(REGISTRY["answer_grounded"](ctx, {"min_digits": 3}))["ok"] is True
    record["answer"] = "Net pay was 46,000."
    assert asyncio.run(REGISTRY["answer_grounded"](ctx, {"min_digits": 3}))["ok"] is False


def test_a_figure_computed_by_a_compute_node_counts_as_evidence_but_the_answer_nodes_own_text_does_not():
    import asyncio

    from evals.verifiers import REGISTRY, VerifyContext
    nodes = {"n1": {"skill": "payroll_cost_report", "state": "succeeded", "result": {"totals": {"gross": 837440.0}}},
             "n2": {"skill": "answer_with_evidence", "state": "succeeded", "result": {"text": "Gross was 999,999."}}}
    record = {"answer": "Gross was 837,440.00.", "tool_calls": [{"result": {"rows": []}}], "result": {"nodes": nodes}}
    ctx = VerifyContext(task={"goal": "x"}, record=record, before=[], after=[], client=None, jurisdiction="IN")
    assert asyncio.run(REGISTRY["answer_grounded"](ctx, {"min_digits": 3}))["ok"] is True
    record["answer"] = "Gross was 999,999."
    assert asyncio.run(REGISTRY["answer_grounded"](ctx, {"min_digits": 3}))["ok"] is False

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

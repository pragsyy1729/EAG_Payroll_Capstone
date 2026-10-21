"""Implementation scaffolding for the official harness run.

NOT the team's graded tests. Fakes stand in for AgentSwitch and the task runner; no network is used.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from evals import official
from evals.scoring import FAIL, INFRA, PASS

ROOT = Path(__file__).resolve().parents[2]
TOKEN, KEY = "seat-token-abc123", "sk-model-key-xyz789"


def task(task_id, *, family="check", by="scaffold", jurisdiction="IN", mode="dry_run", allow=(), goal=None):
    return {"id": task_id, "family": family, "authored_by": by, "goal": goal or f"goal of {task_id}",
            "jurisdiction": jurisdiction, "mode": mode, "allow": list(allow), "transport": "live", "verifiers": []}


class FakeClient:
    def __init__(self, tools=3, harness_reply=None, tools_error=None):
        self.tools, self.reply, self.tools_error, self.closed = tools, harness_reply or {"result": {}, "status": "ok"}, tools_error, 0

    def configured(self, jurisdiction):
        return True

    async def list_tools(self, *, jurisdiction, refresh=False):
        if self.tools_error:
            raise self.tools_error
        return [{"name": f"t{i}"} for i in range(self.tools)]

    async def call_tool(self, name, arguments=None, *, jurisdiction):
        if name == "PayRun.list":
            return {"data": [], "total": 0}
        assert name == "TeamHarness.get_mine", f"only reads are allowed in the plumbing checks: {name}"
        return self.reply

    async def close(self):
        self.closed += 1


def row_for(state, *, claims=(), failed=(), reason=None, authored_by="scaffold"):
    results = [{"claim": c, "ok": True} for c in claims] + [{"claim": c, "ok": False} for c in failed]
    out = {"state": state, "authored_by": authored_by, "verifier_results": results}
    if failed:
        out["failed_claims"] = list(failed)
    if reason:
        out["reason"] = reason
    return out


def args(**extra):
    return argparse.Namespace(live=False, min_interval=0.0, **extra)


async def run(tasks, runner, tmp_path, *, instance="suryodaya", live=False, clock=None, deadline=1e9, client=None):
    a = args()
    a.live = live
    return await official.run_official(
        instance=instance, tasks=tasks, args=a, results_path=tmp_path / "results.json", run_dir=tmp_path,
        make_client=lambda: client or FakeClient(), make_live_llm=lambda t, a: None, table={},
        task_runner=runner, clock=clock or (lambda: 0.0), deadline=deadline)


def read(tmp_path):
    return json.loads((tmp_path / "results.json").read_text())


# --- the file format -------------------------------------------------------------------------------------------------

def test_validate_results_accepts_the_documented_shape_and_names_every_problem():
    good = {"tasks": [{"id": "a", "title": "t", "passed": True, "score": 1.0, "evidence": "e"}], "summary": "s"}
    assert official.validate_results(good) == []
    for bad in ({}, {"tasks": [], "summary": "s"}, {"tasks": [{"id": "a", "title": "t", "passed": 1}], "summary": "s"},
                {"tasks": [{"id": "a", "title": "t", "passed": True, "score": 1.5}], "summary": "s"},
                {"tasks": [{"id": "a", "title": "t", "passed": True, "score": True}], "summary": "s"},
                {"tasks": [{"id": "", "title": "t", "passed": True}], "summary": "s"},
                {"tasks": [{"id": "a", "title": "t", "passed": True}], "summary": ""},
                {"tasks": [{"id": "a", "title": "t", "passed": True}, {"id": "a", "title": "u", "passed": False}], "summary": "s"},
                {"tasks": [{"id": str(i), "title": "t", "passed": True} for i in range(201)], "summary": "s"}):
        assert official.validate_results(bad), bad


def test_titles_are_tagged_by_who_wrote_the_task():
    assert official.title_for(task("a", by="scaffold", goal="Show X")).startswith("[scaffold, not team-authored] Show X")
    assert official.title_for(task("b", by="team", goal="Show Y")) == "Show Y"


def test_a_passing_task_scores_one_and_says_what_held():
    entry = official.entry_for("suryodaya", task("a"), row_for(PASS, claims=["c1", "c2"]))
    assert entry["id"] == "suryodaya:a" and entry["passed"] is True and entry["score"] == 1.0
    assert "2 of 2" in entry["evidence"] and "c1" in entry["evidence"]


def test_a_failing_task_scores_the_fraction_of_checks_that_held():
    entry = official.entry_for("suryodaya", task("a"), row_for(FAIL, claims=["c1", "c2", "c3"], failed=["c4"]))
    assert entry["passed"] is False and entry["score"] == 0.75 and entry["evidence"].startswith("failed: c4")


def test_an_infra_error_is_not_passed_scores_zero_and_says_infra():
    entry = official.entry_for("suryodaya", task("a"), row_for(INFRA, reason="model endpoint returned 503"))
    assert entry["passed"] is False and entry["score"] == 0.0 and entry["evidence"].startswith("infra: model endpoint")


def test_an_out_of_time_task_says_it_was_not_run():
    entry = official.entry_for("suryodaya", task("a"), {"state": official.OUT_OF_TIME, "verifier_results": []})
    assert entry["passed"] is False and entry["evidence"] == "not run: out of time"


def test_evidence_and_title_are_clipped_and_secrets_are_redacted(monkeypatch):
    monkeypatch.setenv("AGENTSWITCH_TOKEN", TOKEN)
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    long = "x" * 900
    entry = official.entry_for("suryodaya", task("a", goal=long),
                               row_for(FAIL, failed=[f"saw {TOKEN} and {KEY} " + long]))
    blob = json.dumps(entry)
    assert TOKEN not in blob and KEY not in blob and "[redacted]" in blob
    assert len(entry["evidence"]) <= official.EVIDENCE_LIMIT and len(entry["title"]) <= official.TITLE_LIMIT


def test_results_are_written_atomically_and_an_invalid_file_is_refused(tmp_path):
    path = tmp_path / "results.json"
    official.write_results(path, [{"id": "a", "title": "t", "passed": True, "score": 1.0, "evidence": "e"}], "one line")
    assert json.loads(path.read_text())["summary"] == "one line" and not list(tmp_path.glob("*.tmp"))
    with pytest.raises(ValueError):
        official.write_results(path, [], "no tasks")
    assert json.loads(path.read_text())["summary"] == "one line"                  # the good file survived


# --- the run ---------------------------------------------------------------------------------------------------------

async def test_plumbing_checks_come_first_and_are_tagged(tmp_path):
    async def runner(t, a, d, **kw):
        return row_for(PASS, claims=["c"])

    await run([task("a")], runner, tmp_path)
    ids = [t["id"] for t in read(tmp_path)["tasks"]]
    assert ids[:2] == ["suryodaya:mcp_tools_list", "suryodaya:team_harness_get_mine"] and ids[2] == "suryodaya:a"
    assert all(t["title"].startswith("[plumbing]") for t in read(tmp_path)["tasks"][:2])


async def test_a_failing_plumbing_check_is_a_failed_entry_not_a_crash(tmp_path):
    async def runner(t, a, d, **kw):
        return row_for(PASS, claims=["c"])

    await run([task("a")], runner, tmp_path, client=FakeClient(tools_error=RuntimeError("down")))
    first = read(tmp_path)["tasks"][0]
    assert first["passed"] is False and "down" in first["evidence"]


async def test_only_tasks_for_the_instances_jurisdiction_run(tmp_path):
    ran = []

    async def runner(t, a, d, **kw):
        ran.append(t["id"])
        return row_for(PASS, claims=["c"])

    await run([task("in_task"), task("us_task", jurisdiction="US")], runner, tmp_path)
    assert ran == ["in_task"]
    ran.clear()
    await run([task("in_task"), task("us_task", jurisdiction="US")], runner, tmp_path, instance="keystone")
    assert ran == ["us_task"]


async def test_armed_tasks_are_skipped_without_allow_writes_and_run_with_it(tmp_path):
    ran = []

    async def runner(t, a, d, **kw):
        ran.append((t["id"], a.live))
        return row_for(PASS, claims=["c"])

    armed = task("armed", family="refusal", mode="live", allow=["create_off_cycle_run"])
    await run([task("plain"), armed], runner, tmp_path)
    assert ran == [("plain", False)] and "1 skipped" in read(tmp_path)["summary"]
    assert "suryodaya:armed" not in [t["id"] for t in read(tmp_path)["tasks"]]
    ran.clear()
    await run([task("plain"), armed], runner, tmp_path, live=True)
    assert ran == [("plain", True), ("armed", True)]


async def test_results_exist_after_every_task_not_only_at_the_end(tmp_path):
    seen_counts = []

    async def runner(t, a, d, **kw):
        seen_counts.append(len(read(tmp_path)["tasks"]))
        return row_for(PASS, claims=["c"])

    await run([task("a"), task("b"), task("c")], runner, tmp_path)
    assert seen_counts == [2, 3, 4]                                  # plumbing (2) first, then +1 per finished task


async def test_a_crashing_task_becomes_an_infra_entry_and_the_run_continues(tmp_path):
    async def runner(t, a, d, **kw):
        if t["id"] == "bad":
            raise ValueError("boom")
        return row_for(PASS, claims=["c"])

    await run([task("bad"), task("good")], runner, tmp_path)
    by_id = {t["id"]: t for t in read(tmp_path)["tasks"]}
    assert by_id["suryodaya:bad"]["passed"] is False and "boom" in by_id["suryodaya:bad"]["evidence"]
    assert by_id["suryodaya:good"]["passed"] is True
    assert official.validate_results(read(tmp_path)) == []


async def test_a_hanging_task_is_cut_off_at_the_deadline(tmp_path):
    clock = {"now": 0.0}

    async def runner(t, a, d, **kw):
        await asyncio.sleep(3600)

    await run([task("slow"), task("next")], runner, tmp_path, clock=lambda: clock["now"], deadline=0.05)
    by_id = {t["id"]: t for t in read(tmp_path)["tasks"]}
    assert by_id["suryodaya:slow"]["evidence"] == "not run: out of time"


async def test_tasks_not_started_before_the_deadline_are_recorded_as_not_run(tmp_path):
    async def runner(t, a, d, **kw):
        raise AssertionError("must not start after the deadline")

    await run([task("a"), task("b")], runner, tmp_path, clock=lambda: 100.0, deadline=50.0)
    entries = read(tmp_path)["tasks"]
    assert [e["evidence"] for e in entries[2:]] == ["not run: out of time"] * 2
    assert "2 not run in time" in read(tmp_path)["summary"]


async def test_the_summary_counts_scaffold_team_and_plumbing_separately(tmp_path):
    async def runner(t, a, d, **kw):
        return row_for(PASS if t["id"] != "f" else FAIL, claims=["c"], failed=["x"] if t["id"] == "f" else (),
                       authored_by=t["authored_by"])

    await run([task("p1"), task("f"), task("team1", by="team")], runner, tmp_path)
    summary = read(tmp_path)["summary"]
    assert summary.startswith("suryodaya: 4/5 passed") and "scaffold 1/2" in summary
    assert "team 1/1" in summary and "plumbing 2/2" in summary


async def test_the_client_is_closed_and_the_token_never_reaches_the_file(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTSWITCH_TOKEN", TOKEN)
    client = FakeClient(harness_reply={"result": f"leaked {TOKEN}", "status": "ok"})

    async def runner(t, a, d, **kw):
        return row_for(PASS, claims=["c"])

    await run([task("a")], runner, tmp_path, client=client)
    assert TOKEN not in (tmp_path / "results.json").read_text() and client.closed >= 1


# --- main ------------------------------------------------------------------------------------------------------------

def test_without_the_platform_variables_a_valid_file_still_explains_why(tmp_path, monkeypatch):
    for name in ("AGENTSWITCH_BASE_URL", "AGENTSWITCH_TOKEN", "AGENTSWITCH_INSTANCE"):
        monkeypatch.delenv(name, raising=False)
    target = tmp_path / "results.json"
    with pytest.raises(SystemExit) as caught:
        official.main(["--results", str(target)])
    assert caught.value.code == 1
    data = json.loads(target.read_text())
    assert official.validate_results(data) == [] and data["tasks"][0]["passed"] is False
    assert "AGENTSWITCH" in data["tasks"][0]["evidence"]


def test_the_config_file_matches_the_launcher_and_the_documented_keys():
    config = tomllib.loads((ROOT / "agentswitch-harness.toml").read_text())
    assert config["results"] == "results.json" and config["instances"] == ["suryodaya"]
    assert config["install"] == "pip install -r requirements.txt"
    assert config["run"].startswith("python official_run.py") and "--allow-writes" in config["run"]
    assert 1 <= config["timeout_minutes"] <= 30 and f"--timeout-minutes {config['timeout_minutes']}" in config["run"]
    assert "openai" in (ROOT / "requirements.txt").read_text()


def test_the_launcher_runs_from_the_repo_root_and_shows_the_flags():
    done = subprocess.run([sys.executable, "official_run.py", "--help"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and "--allow-writes" in done.stdout and "--timeout-minutes" in done.stdout


# --- found in the local dry run: one slow model-driven task used the whole budget -------------------------------------

async def test_scripted_tasks_run_before_model_driven_ones(tmp_path):
    ran = []

    async def runner(t, a, d, **kw):
        ran.append(t["id"])
        return row_for(PASS, claims=["c"])

    scripted = {**task("fast"), "transport": "scripted", "script": "scripts/scaffold_august_lookup.json"}
    # "slow" is listed first but needs the model; "fast" is scripted and must go first (a stable order otherwise)
    await run([task("slow"), scripted, task("slow2")], runner, tmp_path)
    assert ran == ["fast", "slow", "slow2"]


async def test_one_task_that_exceeds_its_own_cap_is_an_infra_entry_and_the_rest_still_run(tmp_path):
    async def runner(t, a, d, **kw):
        if t["id"] == "hang":
            await asyncio.sleep(3600)
        return row_for(PASS, claims=["c"])

    a = args()
    await official.run_official(
        instance="suryodaya", tasks=[task("hang"), task("after")], args=a, results_path=tmp_path / "results.json",
        run_dir=tmp_path, make_client=lambda: FakeClient(), make_live_llm=lambda t, a: None, table={},
        task_runner=runner, clock=lambda: 0.0, deadline=1e9, task_timeout=0.05)
    by_id = {t["id"]: t for t in read(tmp_path)["tasks"]}
    assert by_id["suryodaya:hang"]["passed"] is False and by_id["suryodaya:hang"]["evidence"].startswith("infra: task exceeded")
    assert by_id["suryodaya:after"]["passed"] is True


# --- from the reviewer's round-3 feedback -----------------------------------------------------------------------------

def test_a_grounding_failure_names_the_numbers_that_were_not_in_the_tool_results():
    row = {"state": FAIL, "failed_claims": ["every id and number in the answer appears in tool results"],
           "verifier_results": [{"claim": "every id and number in the answer appears in tool results", "ok": False,
                                 "observed": {"ungrounded_numbers": ["52000"], "ungrounded_ids": []}}]}
    assert "ungrounded numbers: ['52000']" in official.entry_for("suryodaya", task("a"), row)["evidence"]


def test_a_missing_answer_is_named_in_the_evidence():
    row = {"state": FAIL, "failed_claims": ["the run produced an answer to ground"],
           "verifier_results": [{"claim": "the run produced an answer to ground", "ok": False, "observed": {"answer": None}}]}
    assert "ended without a final answer" in official.entry_for("suryodaya", task("a"), row)["evidence"]

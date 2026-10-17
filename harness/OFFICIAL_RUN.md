# Official harness run (AgentSwitch "Our harness -> Submit for a run")

AgentSwitch checks out the commit on the branch saved in "Our harness", runs the `install` and `run` commands in
`agentswitch-harness.toml` against a fresh copy of the instance (writes are thrown away, no internet during `run`),
and reads `results.json`. One run per team every 3 days.

## What is in the repo root

- `agentswitch-harness.toml`: `install`, `run`, `results`, `instances`, `timeout_minutes`.
- `requirements.txt`: only what the run imports (the full `harness/pyproject.toml` pulls heavy packages it does not need).
- `official_run.py`: a launcher that puts `harness/` on `sys.path` and calls `evals.official.main()`, so `run` is a
  plain `python` call from the repo root and does not depend on a shell.

## How it works

- `harness/evals/official.py` loads every task file, keeps the tasks for the instance's jurisdiction
  (`suryodaya` is IN, `keystone` is US), runs each through the same `run_task` as `python -m evals.runner`, and maps
  the result to the platform's format. `results.json` is rewritten after every task.
- `harness/payroll_agent/agentswitch.py` has a token mode: with `AGENTSWITCH_TOKEN`, `AGENTSWITCH_BASE_URL` and
  `AGENTSWITCH_INSTANCE` set it uses that token and never logs in.
- `harness/payroll_agent/openai_llm.py` talks to the platform's model (`OPENAI_BASE_URL`, `OPENAI_API_KEY`,
  `OPENAI_MODEL`). Failures are infra errors, never "the agent was wrong".
- `harness/evals/idmap.py` and `harness/evals/official_ids.json`: tasks name records by id; the runner looks each
  record up by business number (for example `PRUN-2026-00011`) and swaps in the current id, so a copy with new ids
  still works. A record that is not found makes the tasks that use it infra errors, naming the record.
  Rebuild the table (read-only, normal `.env` login) with `python -m evals.official --build-id-table` from `harness/`.

## What is scored

- `[plumbing]`: MCP `tools/list` works with the seat token, `TeamHarness.get_mine` answers (read only).
- `[scaffold, not team-authored]`: the existing scaffold tasks plus `evals/tasks/official.jsonl`, eight model-driven
  end-to-end goals. These are implementation checks, not the team's graded tests.
- Team-authored tasks (`authored_by: "team"`) join the same file automatically, untagged. The summary counts
  scaffold, team and plumbing separately.
- Per task: `passed` is true only for a pass; `score` is the fraction of checks that held (0 for an infra error);
  `evidence` is at most 500 characters. Armed tasks are skipped (and counted) unless `--allow-writes` is given.
- A task that crashes or hangs never stops the file being written; tasks not started before the time limit minus
  2 minutes are recorded as "not run: out of time".

## Safety

- The official command uses `--allow-writes` because the platform runs on a throwaway copy. **Locally, run without
  it.** Every existing task is read-only or refusal-only either way.
- The seat token and model key are redacted from everything written to `results.json`.
- Nothing in this code calls `TeamHarness.set_mine`. Saving the branch in the UI is a manual step.

## Run it locally

```bash
export AGENTSWITCH_BASE_URL=... AGENTSWITCH_TOKEN=... AGENTSWITCH_INSTANCE=suryodaya
export OPENAI_BASE_URL=http://127.0.0.1:11434/v1 OPENAI_API_KEY=ollama OPENAI_MODEL=mistral:latest   # Ollama
python official_run.py --results /tmp/results.json          # from the repo root; no --allow-writes
```

## Submit

Push the branch, save it in "Our harness" in AgentSwitch, then press Submit. The branch must contain
`agentswitch-harness.toml`. Unknown until the first real run: the working directory the platform uses, whether
record ids survive the copy (handled by the remap), and the platform model's speed and limits.

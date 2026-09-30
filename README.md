# Team 12 — Payroll Agent

An EAG capstone project: 12 teams each build one agent for a different
business app on top of **AgentSwitch**, a shared "schema-driven,
agent-composed business platform." Team 12's app is **Payroll**.

The agent must answer, at minimum:

- *"Run August payroll"*
- *"Why is Ramesh's net pay lower?"*

and, per the class's own grading rubric, cover the payroll domain
exhaustively rather than just those two examples — India and US
jurisdictions both in scope. See `harness/REQUIREMENTS.md` for the full
catalog.

## Repository layout

```
glc_v5/                     LLM gateway: owns provider keys, routing,
                             quotas, cost/audit ledger. The agent never
                             sees a provider key directly.
harness/
  payroll_agent/
    agentswitch.py           AgentSwitchClient -- the MCP/REST seam into
                              the AgentSwitch platform (two jurisdiction
                              tenants, one client).
    capabilities.py           The capability manifest the planner sees.
    planner.py                Capability-driven, outcome-by-outcome
                              planner (domain-agnostic).
    workers.py                One async worker per capability, each a
                              thin call into AgentSwitchClient.
    run.py                    CLI runner: drives one goal through the
                              live graph end to end. No FastAPI service
                              yet -- this is the direct way to run and
                              inspect one request at a time.
    gateway.py                 payroll_agent -> glc_v5 seam.
    auth.py                    Fail-closed control-plane token gates.
    core/                     Live graph executor, memory, A2A -- the
                              inherited runtime backbone.
    economics/                Budget-aware planning, tiers, pricing.
    events/                   Durable event intake, autonomy governor.
    ui/                       Generative UI / AG-UI / HITL surface.
  REQUIREMENTS.md            Full requirements catalog: payroll scenarios
                              (India + US), AgentSwitch integration
                              mechanics, live-probe findings, grading
                              rubric.
  GAP_REPORT.md               Week-one deliverable: what best-in-class
                              payroll products do that AgentSwitch
                              doesn't, and which gaps an agent can close
                              via orchestration today.
  NEXT_STEPS.md                Current status and the prioritized
                              roadmap.
```

## Quickstart

Two services, then the agent.

**1. Start the LLM gateway** (`glc_v5`) if it isn't already running:

```bash
cd glc_v5
uv sync
cp .env.example .env   # add at least one provider key
uv run glc serve       # listens on http://127.0.0.1:8111
```

**2. Configure the agent:**

```bash
cd harness
uv sync
cp .env.example .env
```

Fill in `harness/.env` with:
- At minimum one `AGENTSWITCH_IN_*` or `AGENTSWITCH_US_*` credential set
  (base URL, email, password) for the jurisdiction tenant(s) you'll use —
  see `REQUIREMENTS.md` SS14 for what these are and how login works.
- `PAYROLL_GATEWAY_MODEL` if the gateway's own default model is
  unavailable (see `NEXT_STEPS.md` for why this exists).

Never commit `.env`, real credentials, or anything under
`harness/agentswitch_tools_*.json` (locally-fetched tool-catalogue
snapshots) — all gitignored already.

**3. Run a goal:**

```bash
cd harness
.venv/bin/python -m payroll_agent.run "Run August payroll" \
    --jurisdiction IN --allow run_payroll
```

```bash
.venv/bin/python -m payroll_agent.run \
    "Why is Ramesh's net pay lower this month?" --jurisdiction IN
```

The second flow is read-only, so it needs no `--allow` flags. See
`payroll_agent/run.py`'s module docstring for the full CLI, and
`NEXT_STEPS.md` for current verification status of each flow.

## Documentation

| File | What it covers |
|---|---|
| `harness/REQUIREMENTS.md` | The full payroll requirements catalog (India + US statutory compliance, exceptions, garnishments, audit/fraud, reporting), the AgentSwitch integration mechanics (MCP protocol, auth, live-probe findings), and the grading rubric. |
| `harness/GAP_REPORT.md` | Week-one deliverable: feature gaps against reference payroll products, and which are buildable via orchestration vs. need platform work. |
| `harness/WORKFLOWS.md` | The queries the agent must answer, by family, matched to the AgentSwitch tools our seat can reach, with build status. |
| `harness/NEXT_STEPS.md` | Current build status and prioritized next steps. |

## Status

MVP skill layer built and partially live-verified against the real
AgentSwitch platform. See `harness/NEXT_STEPS.md` for exactly what's
confirmed working, what's still open, and the next command to run.

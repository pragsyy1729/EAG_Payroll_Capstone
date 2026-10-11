"""Launcher for the AgentSwitch official harness run (see harness/evals/official.py).

A launcher keeps the platform's ``run`` command a plain ``python`` call from the repo root: it does not rely on a
shell to ``cd harness``.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "harness"))

from evals.official import main  # noqa: E402

if __name__ == "__main__":
    main()

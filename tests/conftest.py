"""Shared fixtures for ArchSwarm tests."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from arch_swarm.debate import DebateSession
from arch_swarm.models import (
    DesignCritique,
    DesignProposal,
    Verdict,
)


@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    """Create a tiny Python project tree for scanner tests."""
    pkg = tmp_path / "src" / "myapp"
    pkg.mkdir(parents=True)

    (pkg / "__init__.py").write_text('"""myapp package."""\n', encoding="utf-8")

    (pkg / "core.py").write_text(
        textwrap.dedent("""\
            import os
            from myapp import utils

            class Engine:
                def run(self):
                    if os.getenv("DEBUG"):
                        print("debug")

            def main():
                e = Engine()
                e.run()
        """),
        encoding="utf-8",
    )

    (pkg / "utils.py").write_text(
        textwrap.dedent("""\
            def helper():
                return 42

            class Base:
                pass

            class Child(Base):
                pass
        """),
        encoding="utf-8",
    )

    return tmp_path


@pytest.fixture()
def debate_with_proposals() -> DebateSession:
    """Return a DebateSession pre-loaded with two proposals."""
    ds = DebateSession()
    ds.start_debate(topic="How to split server.py?", context="Large monolith.")

    p1 = DesignProposal(
        author="Simplicity Critic",
        title="Keep it simple",
        description="Just extract the router.",
        pros=["Less churn"],
        cons=["Still coupled"],
        id="proposal_a",
    )
    p2 = DesignProposal(
        author="Modularity Expert",
        title="Full decomposition",
        description="Split into router, handlers, middleware.",
        pros=["Clean boundaries"],
        cons=["More files"],
        id="proposal_b",
    )
    ds.add_proposal(p1)
    ds.add_proposal(p2)

    return ds

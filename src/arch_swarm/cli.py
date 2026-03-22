"""Command-line interface for ArchSwarm."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import click

from arch_swarm.agents import ALL_ROLES, render_prompt
from arch_swarm.code_scanner import format_analysis, scan_project
from arch_swarm.debate import DebateSession
from arch_swarm.models import (
    DesignCritique,
    DesignProposal,
    Verdict,
)

# Directory for persisting session transcripts
_SESSION_DIR = Path(os.environ.get("ARCHSWARM_SESSIONS", ".archswarm_sessions"))


def _ensure_session_dir() -> Path:
    _SESSION_DIR.mkdir(parents=True, exist_ok=True)
    return _SESSION_DIR


@click.group()
@click.version_option(package_name="arch-swarm-ai")
def main() -> None:
    """ArchSwarm -- multi-agent architecture brainstorming."""


# -----------------------------------------------------------------------
# analyze
# -----------------------------------------------------------------------


@main.command()
@click.argument("project_path", type=click.Path(exists=True))
@click.option("--scope", default=None, help="Sub-directory to restrict scanning.")
def analyze(project_path: str, scope: Optional[str]) -> None:
    """Scan a project and display architecture metrics."""
    analysis = scan_project(project_path, scope=scope)
    click.echo(format_analysis(analysis))


# -----------------------------------------------------------------------
# debate
# -----------------------------------------------------------------------


@main.command()
@click.argument("project_path", type=click.Path(exists=True))
@click.option("--topic", required=True, help="The design question to debate.")
@click.option("--scope", default=None, help="Sub-directory scope for analysis context.")
def debate(project_path: str, topic: str, scope: Optional[str]) -> None:
    """Start a multi-agent debate about a design decision.

    Each built-in agent role will generate a proposal and then critique the
    other proposals.  Results are printed and saved.
    """
    # 1. Analyse the project for context
    analysis = scan_project(project_path, scope=scope)
    context = format_analysis(analysis)

    # 2. Create the debate session
    ds = DebateSession()
    ds.start_debate(topic=topic, context=context)

    click.echo(f"Session {ds.session.id} -- debating: {topic}\n")

    # 3. Each role submits a proposal
    for role in ALL_ROLES:
        prompt = render_prompt(role, topic=topic, context=context)
        proposal = DesignProposal(
            author=role.name,
            title=f"{role.name}'s proposal",
            description=f"[Prompt for LLM]\n{prompt[:200]}...",
            pros=[f"{role.name} perspective: strongest argument"],
            cons=["Requires further analysis"],
            trade_offs=[f"Balances {', '.join(role.focus_areas[:2])}"],
        )
        ds.add_proposal(proposal)
        click.echo(f"  + Proposal from {role.name}: {proposal.id}")

    # 4. Each role critiques the others' proposals
    for role in ALL_ROLES:
        for proposal in ds.session.proposals:
            if proposal.author == role.name:
                continue
            critique = DesignCritique(
                proposal_id=proposal.id,
                critic=role.name,
                verdict=Verdict.MODIFY,
                reasoning=f"{role.name} suggests modifications from a "
                f"{', '.join(role.focus_areas[:2])} standpoint.",
                suggested_changes=[f"Consider {role.focus_areas[0]}"],
            )
            ds.add_critique(critique)

    # 5. Each role votes (support own proposal, neutral on others)
    for role in ALL_ROLES:
        for proposal in ds.session.proposals:
            ds.vote(
                agent=role.name,
                proposal_id=proposal.id,
                support=(proposal.author == role.name),
            )

    # 6. Resolve and print
    decision = ds.resolve()
    transcript = ds.get_transcript()
    click.echo("\n" + transcript)

    # 7. Persist the transcript (print first so user sees it even if save fails)
    try:
        session_dir = _ensure_session_dir()
        out = session_dir / f"{ds.session.id}.md"
        out.write_text(transcript, encoding="utf-8")

        meta = session_dir / f"{ds.session.id}.json"
        meta.write_text(
            json.dumps(
                {
                    "id": ds.session.id,
                    "topic": topic,
                    "decision": decision.title if decision else None,
                    "status": decision.status.value if decision else None,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        click.echo(f"\nSession saved: {out}")
    except OSError as exc:
        click.echo(f"\nWarning: failed to save: {exc}", err=True)


# -----------------------------------------------------------------------
# report
# -----------------------------------------------------------------------


@main.command()
@click.argument("session_id")
def report(session_id: str) -> None:
    """Display the debate transcript for a previous session."""
    session_dir = _ensure_session_dir()
    md_file = session_dir / f"{session_id}.md"
    if not md_file.exists():
        click.echo(f"Session {session_id!r} not found in {session_dir}", err=True)
        raise SystemExit(1)
    click.echo(md_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

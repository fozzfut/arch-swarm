"""Debate session -- the conversational core of ArchSwarm.

Flow:  start_debate -> add_proposal(s) -> add_critique(s) -> vote -> resolve
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from arch_swarm.models import (
    ArchSession,
    DecisionStatus,
    DesignCritique,
    DesignDecision,
    DesignProposal,
    Verdict,
    Vote,
)


@dataclass
class DebateSession:
    """Orchestrates a structured architecture debate.

    Wraps *ArchSession* with higher-level operations and transcript
    generation.
    """

    session: ArchSession = field(default_factory=lambda: ArchSession(topic=""))

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_debate(self, topic: str, context: str = "") -> None:
        """Initialise (or reset) the debate with a topic and optional context."""
        self.session = ArchSession(topic=topic, context=context)

    def add_proposal(self, proposal: DesignProposal) -> str:
        """Submit a design proposal.  Returns the proposal id."""
        self.session.add_proposal(proposal)
        return proposal.id

    def add_critique(self, critique: DesignCritique) -> str:
        """Submit a critique of an existing proposal.  Returns the critique id."""
        self.session.add_critique(critique)
        return critique.id

    def vote(self, agent: str, proposal_id: str, support: bool) -> None:
        """Record an agent's vote on a proposal."""
        self.session.add_vote(Vote(agent=agent, proposal_id=proposal_id, support=support))

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(self) -> Optional[DesignDecision]:
        """Compute the winning proposal based on votes and critiques.

        Returns the *DesignDecision* (also stored on the session) or *None*
        if there are no proposals.
        """
        if not self.session.proposals:
            return None

        scores = self.session.tally_votes()

        # Don't add critique adjustments to scores — they are for
        # narrative context, not scoring.  Votes already reflect each
        # agent's position informed by critiques.

        # Pick the proposal with the highest score (tie-break: first submitted)
        best_id: Optional[str] = None
        best_score = float("-inf")
        for proposal in self.session.proposals:
            s = scores.get(proposal.id, 0)
            if s > best_score:
                best_score = s
                best_id = proposal.id

        # Collect dissenting opinions (oppose critiques on the winner)
        dissent: list[str] = []
        if best_id is not None:
            for c in self.session.get_critiques_for(best_id):
                if c.verdict == Verdict.OPPOSE:
                    dissent.append(f"[{c.critic}] {c.reasoning}")

        winner = next((p for p in self.session.proposals if p.id == best_id), None)
        title = winner.title if winner else "No winner"

        decision = DesignDecision(
            title=title,
            chosen_proposal_id=best_id,
            rationale=f"Won with score {best_score} based on votes and critiques.",
            dissenting_opinions=dissent,
            status=DecisionStatus.ACCEPTED,
        )
        self.session.add_decision(decision)
        return decision

    # ------------------------------------------------------------------
    # Transcript
    # ------------------------------------------------------------------

    def get_transcript(self) -> str:
        """Return the full debate history formatted as Markdown."""
        lines: list[str] = []
        s = self.session

        lines.append(f"# Debate: {s.topic}")
        if s.context:
            lines.append("")
            context_lines = s.context.split("\n")
            quoted = "\n".join(f"> {line}" for line in context_lines)
            lines.append(quoted)
        lines.append("")

        # Proposals
        if s.proposals:
            lines.append("## Proposals")
            lines.append("")
            for p in s.proposals:
                lines.append(f"### [{p.author}] {p.title}")
                lines.append("")
                lines.append(p.description)
                if p.pros:
                    lines.append("")
                    lines.append("**Pros:** " + "; ".join(p.pros))
                if p.cons:
                    lines.append("")
                    lines.append("**Cons:** " + "; ".join(p.cons))
                if p.trade_offs:
                    lines.append("")
                    lines.append("**Trade-offs:** " + "; ".join(p.trade_offs))
                lines.append("")

        # Critiques
        if s.critiques:
            lines.append("## Critiques")
            lines.append("")
            for c in s.critiques:
                lines.append(
                    f"- **{c.critic}** ({c.verdict.value}) on proposal "
                    f"`{c.proposal_id}`: {c.reasoning}"
                )
                if c.suggested_changes:
                    for ch in c.suggested_changes:
                        lines.append(f"  - {ch}")
            lines.append("")

        # Votes
        if s.votes:
            lines.append("## Votes")
            lines.append("")
            tally = s.tally_votes()
            for pid, score in sorted(tally.items(), key=lambda kv: -kv[1]):
                proposal = next((p for p in s.proposals if p.id == pid), None)
                label = proposal.title if proposal else pid
                lines.append(f"- **{label}**: score {score}")
            lines.append("")

        # Decisions
        if s.decisions:
            lines.append("## Decision")
            lines.append("")
            for d in s.decisions:
                status_icon = {
                    DecisionStatus.ACCEPTED: "ACCEPTED",
                    DecisionStatus.REJECTED: "REJECTED",
                    DecisionStatus.PROPOSED: "PROPOSED",
                }[d.status]
                lines.append(f"**{d.title}** [{status_icon}]")
                lines.append("")
                lines.append(d.rationale)
                if d.dissenting_opinions:
                    lines.append("")
                    lines.append("Dissenting opinions:")
                    for op in d.dissenting_opinions:
                        lines.append(f"  - {op}")
            lines.append("")

        return "\n".join(lines)

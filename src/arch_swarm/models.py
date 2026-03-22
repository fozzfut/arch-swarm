"""Domain models for architecture brainstorming sessions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Verdict(Enum):
    """Critic's verdict on a design proposal."""

    SUPPORT = "support"
    OPPOSE = "oppose"
    MODIFY = "modify"


class DecisionStatus(Enum):
    """Lifecycle status of a design decision."""

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


# ---------------------------------------------------------------------------
# Core value objects
# ---------------------------------------------------------------------------


@dataclass
class DesignProposal:
    """A concrete design proposal submitted by an agent during debate."""

    author: str  # agent role name
    title: str
    description: str
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    trade_offs: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


@dataclass
class DesignCritique:
    """An agent's critique of an existing proposal."""

    proposal_id: str
    critic: str  # agent role name
    verdict: Verdict
    reasoning: str
    suggested_changes: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


@dataclass
class DesignDecision:
    """The resolved outcome of a debate."""

    title: str
    chosen_proposal_id: Optional[str]
    rationale: str
    dissenting_opinions: list[str] = field(default_factory=list)
    status: DecisionStatus = DecisionStatus.PROPOSED
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


# ---------------------------------------------------------------------------
# Session aggregate
# ---------------------------------------------------------------------------


@dataclass
class Vote:
    """A single agent vote on a proposal."""

    agent: str
    proposal_id: str
    support: bool


@dataclass
class ArchSession:
    """Tracks the full lifecycle of an architecture debate.

    Proposals -> Critiques -> Votes -> Decision.
    """

    topic: str
    context: str = ""
    proposals: list[DesignProposal] = field(default_factory=list)
    critiques: list[DesignCritique] = field(default_factory=list)
    votes: list[Vote] = field(default_factory=list)
    decisions: list[DesignDecision] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    # -- helpers -------------------------------------------------------------

    def add_proposal(self, proposal: DesignProposal) -> None:
        self.proposals.append(proposal)

    def add_critique(self, critique: DesignCritique) -> None:
        if not any(p.id == critique.proposal_id for p in self.proposals):
            raise ValueError(
                f"Proposal {critique.proposal_id!r} does not exist in session"
            )
        self.critiques.append(critique)

    def add_vote(self, vote: Vote) -> None:
        if not any(p.id == vote.proposal_id for p in self.proposals):
            raise ValueError(
                f"Proposal {vote.proposal_id!r} does not exist in session"
            )
        # Replace previous vote by same agent on same proposal
        self.votes = [
            v
            for v in self.votes
            if not (v.agent == vote.agent and v.proposal_id == vote.proposal_id)
        ]
        self.votes.append(vote)

    def add_decision(self, decision: DesignDecision) -> None:
        self.decisions.append(decision)

    def tally_votes(self) -> dict[str, int]:
        """Return net support score per proposal (support +1, oppose -1)."""
        scores: dict[str, int] = {p.id: 0 for p in self.proposals}
        for v in self.votes:
            scores.setdefault(v.proposal_id, 0)
            scores[v.proposal_id] += 1 if v.support else -1
        return scores

    def get_critiques_for(self, proposal_id: str) -> list[DesignCritique]:
        return [c for c in self.critiques if c.proposal_id == proposal_id]

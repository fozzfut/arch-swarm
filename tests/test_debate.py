"""Tests for arch_swarm.debate and arch_swarm.models."""

from __future__ import annotations

import pytest

from arch_swarm.debate import DebateSession
from arch_swarm.models import (
    DecisionStatus,
    DesignCritique,
    DesignProposal,
    Verdict,
    Vote,
)


class TestDebateSession:
    def test_start_debate(self) -> None:
        ds = DebateSession()
        ds.start_debate("Topic A", context="Some context")
        assert ds.session.topic == "Topic A"
        assert ds.session.context == "Some context"

    def test_add_proposal(self, debate_with_proposals: DebateSession) -> None:
        ds = debate_with_proposals
        assert len(ds.session.proposals) == 2
        assert ds.session.proposals[0].id == "proposal_a"
        assert ds.session.proposals[1].id == "proposal_b"

    def test_add_critique(self, debate_with_proposals: DebateSession) -> None:
        ds = debate_with_proposals
        crit = DesignCritique(
            proposal_id="proposal_a",
            critic="Scalability Critic",
            verdict=Verdict.OPPOSE,
            reasoning="Won't scale past 1k requests.",
        )
        ds.add_critique(crit)
        assert len(ds.session.critiques) == 1

    def test_critique_invalid_proposal_raises(
        self, debate_with_proposals: DebateSession
    ) -> None:
        ds = debate_with_proposals
        crit = DesignCritique(
            proposal_id="nonexistent",
            critic="X",
            verdict=Verdict.SUPPORT,
            reasoning="test",
        )
        with pytest.raises(ValueError, match="does not exist"):
            ds.add_critique(crit)

    def test_vote_and_tally(self, debate_with_proposals: DebateSession) -> None:
        ds = debate_with_proposals
        ds.vote("Agent1", "proposal_a", support=True)
        ds.vote("Agent2", "proposal_a", support=True)
        ds.vote("Agent3", "proposal_b", support=True)
        ds.vote("Agent4", "proposal_a", support=False)

        tally = ds.session.tally_votes()
        assert tally["proposal_a"] == 1  # +2 -1
        assert tally["proposal_b"] == 1  # +1

    def test_vote_replaces_previous(
        self, debate_with_proposals: DebateSession
    ) -> None:
        ds = debate_with_proposals
        ds.vote("Agent1", "proposal_a", support=True)
        ds.vote("Agent1", "proposal_a", support=False)  # change mind
        tally = ds.session.tally_votes()
        assert tally["proposal_a"] == -1

    def test_resolve_picks_winner(
        self, debate_with_proposals: DebateSession
    ) -> None:
        ds = debate_with_proposals
        ds.vote("Agent1", "proposal_b", support=True)
        ds.vote("Agent2", "proposal_b", support=True)
        ds.vote("Agent3", "proposal_a", support=True)

        decision = ds.resolve()
        assert decision is not None
        assert decision.chosen_proposal_id == "proposal_b"
        assert decision.status == DecisionStatus.ACCEPTED

    def test_resolve_with_critiques(
        self, debate_with_proposals: DebateSession
    ) -> None:
        ds = debate_with_proposals
        # Critiques are for narrative context, not scoring.
        # Votes alone determine the winner.
        ds.vote("Agent1", "proposal_a", support=True)
        ds.vote("Agent2", "proposal_b", support=True)
        ds.vote("Agent3", "proposal_b", support=True)  # extra vote breaks tie
        crit = DesignCritique(
            proposal_id="proposal_b",
            critic="Reuse Finder",
            verdict=Verdict.SUPPORT,
            reasoning="Great reuse potential.",
        )
        ds.add_critique(crit)

        decision = ds.resolve()
        assert decision is not None
        assert decision.chosen_proposal_id == "proposal_b"

    def test_resolve_no_proposals(self) -> None:
        ds = DebateSession()
        ds.start_debate("Empty")
        assert ds.resolve() is None

    def test_get_transcript_contains_sections(
        self, debate_with_proposals: DebateSession
    ) -> None:
        ds = debate_with_proposals
        ds.vote("Agent1", "proposal_a", support=True)
        ds.resolve()
        transcript = ds.get_transcript()
        assert "# Debate:" in transcript
        assert "## Proposals" in transcript
        assert "## Votes" in transcript
        assert "## Decision" in transcript

    def test_dissenting_opinions_captured(
        self, debate_with_proposals: DebateSession
    ) -> None:
        ds = debate_with_proposals
        ds.vote("Agent1", "proposal_a", support=True)
        crit = DesignCritique(
            proposal_id="proposal_a",
            critic="Scalability Critic",
            verdict=Verdict.OPPOSE,
            reasoning="Will not handle growth.",
        )
        ds.add_critique(crit)
        decision = ds.resolve()
        assert decision is not None
        assert any("Scalability" in d for d in decision.dissenting_opinions)


class TestArchSession:
    def test_vote_on_invalid_proposal_raises(self) -> None:
        ds = DebateSession()
        ds.start_debate("test")
        with pytest.raises(ValueError, match="does not exist"):
            ds.vote("Agent1", "bad_id", support=True)

    def test_get_critiques_for(
        self, debate_with_proposals: DebateSession
    ) -> None:
        ds = debate_with_proposals
        c1 = DesignCritique(
            proposal_id="proposal_a",
            critic="A",
            verdict=Verdict.SUPPORT,
            reasoning="good",
        )
        c2 = DesignCritique(
            proposal_id="proposal_b",
            critic="B",
            verdict=Verdict.OPPOSE,
            reasoning="bad",
        )
        ds.add_critique(c1)
        ds.add_critique(c2)
        crits = ds.session.get_critiques_for("proposal_a")
        assert len(crits) == 1
        assert crits[0].critic == "A"

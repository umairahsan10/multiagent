"""Shared Pydantic schemas used across reviewers, editor, and A2A messages.

Every reviewer returns a `Review`. Every inter-agent message is an `A2AMessage`.
Keeping these central means adding a new reviewer is a prompt change, not a schema change.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Severity = Literal["minor", "major", "critical"]
ReviewerId = Literal["methodology", "novelty", "devils_advocate", "ethics"]


class Reference(BaseModel):
    title: str
    authors: list[str] = Field(default_factory=list)
    year: Optional[int] = None
    doi: Optional[str] = None


class PaperStructure(BaseModel):
    paper_id: str
    title: str
    abstract: str
    sections: dict[str, str] = Field(default_factory=dict)
    references: list[Reference] = Field(default_factory=list)
    figure_captions: list[str] = Field(default_factory=list)


class Concern(BaseModel):
    claim: str
    evidence: str
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)


class ToolCall(BaseModel):
    server: str
    tool: str
    args: dict
    result_summary: str


class Review(BaseModel):
    reviewer_id: ReviewerId
    overall_score: float = Field(ge=1.0, le=10.0)
    criterion_scores: dict[str, float] = Field(default_factory=dict)
    strengths: list[str] = Field(default_factory=list)
    concerns: list[Concern] = Field(default_factory=list)
    summary: str
    tool_calls: list[ToolCall] = Field(default_factory=list)


class A2AMessage(BaseModel):
    round: int
    sender: str
    recipient: str
    type: Literal["rebuttal_request", "rebuttal_response", "announcement"]
    context: dict = Field(default_factory=dict)
    request: Optional[str] = None
    body: Optional[str] = None


class ContestedClaim(BaseModel):
    claim: str
    positions: dict[str, Literal["agreed", "disagreed", "neutral"]]
    editor_note: str


class Disagreement(BaseModel):
    """A pair of reviewers whose reviews diverge enough to warrant a debate round."""
    reviewer_a: str
    reviewer_b: str
    score_spread: float          # |score_a - score_b|, 0-9
    summary_cosine_distance: float  # 0-1, 1 = opposite
    combined_score: float        # weighted combination, 0-1
    flagged: bool                # combined_score >= DISAGREEMENT_THRESHOLD


class RebuttalOutcome(BaseModel):
    """Structured output from a reviewer responding to one or more A2A rebuttal requests.

    A reviewer receives all challenges against their position from a round and
    produces (a) an updated Review reflecting whatever they were willing to
    adjust, plus (b) a rationale explaining what they changed and why — or
    defending their original position.
    """
    position_changed: bool
    rationale: str
    updated_review: Review


class Verdict(BaseModel):
    recommendation: Literal["accept", "reject", "revise"]
    confidence: float = Field(ge=0.0, le=1.0)
    per_criterion_scores: dict[str, float] = Field(default_factory=dict)
    consensus_strengths: list[str] = Field(default_factory=list)
    consensus_concerns: list[Concern] = Field(default_factory=list)
    contested_claims: list[ContestedClaim] = Field(default_factory=list)
    suggested_revisions: list[str] = Field(default_factory=list)
    summary: str = ""

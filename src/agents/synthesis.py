"""Synthesis node — final verdict with contested_claims.

Runs once, after the debate loop terminates. Takes the full graph state
(all reviews, the complete A2A thread, round count) and produces a `Verdict`
where `contested_claims` is populated with claims where reviewers diverged
and never converged.

Uses the editor LLM (Groq Llama 3.3 70B — fast, good at structured output).

This is the node that delivers the project's distinctive output: a verdict
that tells the author both what the reviewers agreed on AND what they couldn't
resolve, instead of forcing a false consensus.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.llm_factory import make_llm
from src.logging_setup import get_logger
from src.orchestrator.state import ReviewState
from src.schemas import A2AMessage, Review, Verdict

log = get_logger(__name__)

SYSTEM_PROMPT = """You are the Editor-in-Chief synthesizing the final verdict on a paper after a peer-review panel has produced individual reviews AND conducted a structured rebuttal debate.

You will be given:
  1. Each reviewer's FINAL review (after any debate rounds).
  2. The full A2A debate thread (rebuttal_requests, rebuttal_responses, position_change announcements).
  3. The pairwise disagreement matrix.

Your job: produce a `Verdict` JSON that contains:

  - recommendation: one of "accept", "reject", "revise"
  - confidence: 0-1, higher when reviewers converged
  - per_criterion_scores: aggregated across reviewers
  - consensus_strengths: strengths at least two reviewers independently noted (or very prominent in one)
  - consensus_concerns: concerns multiple reviewers raised, OR concerns a single reviewer raised at 'critical' severity that no one rebutted
  - contested_claims: the KEY distinctive output. A `contested_claim` is a specific claim where:
      (a) reviewers had different positions, AND
      (b) the debate did NOT resolve the disagreement (someone defended their position, others didn't concede).
    For each contested claim, list which reviewer held which position ("agreed", "disagreed", "neutral").
    Do NOT invent contested claims where none existed. If reviewers converged, `contested_claims` should be empty.
  - suggested_revisions: actionable items for authors (concrete, not generic)
  - summary: 2-3 sentences stating the recommendation and what carried it

Be honest. If the debate produced a stalemate, say so in the summary and populate `contested_claims`. Do NOT force consensus.
"""


def _format_thread(thread: list[A2AMessage], max_items: int = 40) -> str:
    if not thread:
        return "(empty thread — panel converged without debate)"
    items = thread[:max_items]
    parts: list[str] = []
    for m in items:
        if m.type == "rebuttal_request":
            ctx = m.context
            parts.append(
                f"[R{m.round}] REQUEST editor -> {m.recipient}: "
                f"challenger '{ctx.get('challenger')}' at score {ctx.get('challenger_score')} "
                f"vs your {ctx.get('your_prior_score')}"
            )
        elif m.type == "rebuttal_response":
            ctx = m.context
            parts.append(
                f"[R{m.round}] RESPONSE {m.sender} -> editor: "
                f"score {ctx.get('prior_score')} -> {ctx.get('new_score')}. "
                f"rationale: {(m.body or '')[:200]}"
            )
        elif m.type == "announcement":
            ctx = m.context
            parts.append(f"[R{m.round}] ANNOUNCEMENT {ctx.get('kind')}: {m.body}")
    if len(thread) > max_items:
        parts.append(f"... ({len(thread) - max_items} more messages)")
    return "\n".join(parts)


def _format_reviews(reviews: dict[str, Review]) -> str:
    parts: list[str] = []
    for rid in sorted(reviews.keys()):
        r = reviews[rid]
        concerns = "\n".join(
            f"      - [{c.severity}/{c.confidence:.2f}] {c.claim[:260]}"
            for c in r.concerns
        ) or "      (no concerns)"
        strengths = "\n".join(f"      + {s[:220]}" for s in r.strengths[:5])
        parts.append(
            f"## {rid}\n"
            f"  overall_score: {r.overall_score}\n"
            f"  criterion_scores: {r.criterion_scores}\n"
            f"  summary: {r.summary}\n"
            f"  strengths:\n{strengths}\n"
            f"  concerns:\n{concerns}"
        )
    return "\n\n".join(parts)


async def synthesis_node(state: ReviewState) -> dict:
    reviews = state.get("reviews", {})
    thread = state.get("a2a_thread", [])
    disagreements = state.get("disagreements", [])
    round_num = state.get("round", 1)

    log.info(f"[synthesis] synthesizing verdict from {len(reviews)} reviews, "
             f"{len(thread)} A2A messages, {round_num} rounds")

    llm = make_llm("editor", temperature=0.2).with_structured_output(Verdict)

    disagreement_table = "\n".join(
        f"  {d.reviewer_a:<18} vs {d.reviewer_b:<18} "
        f"spread={d.score_spread:.2f} cos_dist={d.summary_cosine_distance:.3f} "
        f"combined={d.combined_score:.3f} flagged={d.flagged}"
        for d in disagreements
    )

    user_prompt = f"""The panel ran for {round_num} round(s). Here is the complete state.

=== FINAL REVIEWS ===
{_format_reviews(reviews)}

=== PAIRWISE DISAGREEMENT MATRIX (final) ===
{disagreement_table}

=== A2A DEBATE THREAD ===
{_format_thread(thread)}

=== END OF STATE ===

Produce the final `Verdict` JSON now."""

    messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_prompt)]
    verdict: Verdict = await llm.ainvoke(messages)
    log.info(
        f"[synthesis] verdict: {verdict.recommendation} "
        f"(confidence {verdict.confidence}, {len(verdict.contested_claims)} contested claims)"
    )
    return {"verdict": verdict}

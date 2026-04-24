"""A2A message construction + routing helpers.

This is the module that embodies the "A2A" half of the two-protocol claim:
MCP moves data between agents and tools; A2A moves *claims* between agents.
Every rebuttal request and response is an `A2AMessage` with a well-defined
sender, recipient, type, and context — not free-form chat.

Key functions:
- build_rebuttal_requests(): given flagged disagreements + current reviews,
  produces the A2A messages the editor sends out this round. Each flagged pair
  generates TWO messages (one targeting each reviewer). Each reviewer then
  aggregates all messages addressed to them and responds once per round.

- format_challenges_for_reviewer(): takes all challenges against a reviewer
  and builds a prompt-ready text block the reviewer's LLM will see when
  producing its rebuttal response.
"""

from __future__ import annotations

from src.schemas import A2AMessage, Disagreement, Review


def build_rebuttal_requests(
    flagged: list[Disagreement],
    reviews: dict[str, Review],
    round_num: int,
) -> list[A2AMessage]:
    """For each flagged pair, emit two rebuttal_request messages — each reviewer
    is asked to respond to the other's position.

    The editor is the sender for all rebuttal_requests (it mediates; peers
    never message each other directly).
    """
    messages: list[A2AMessage] = []
    for d in flagged:
        a = reviews.get(d.reviewer_a)
        b = reviews.get(d.reviewer_b)
        if a is None or b is None:
            continue

        # challenge targeted at reviewer B (contents of A's review are the challenge)
        messages.append(A2AMessage(
            round=round_num,
            sender="editor",
            recipient=d.reviewer_b,
            type="rebuttal_request",
            context={
                "challenger": d.reviewer_a,
                "challenger_score": a.overall_score,
                "challenger_summary": a.summary,
                "challenger_top_concerns": [c.model_dump() for c in a.concerns[:3]],
                "your_prior_score": b.overall_score,
                "your_prior_summary": b.summary,
                "disagreement_combined_score": d.combined_score,
            },
            request=(
                f"{d.reviewer_a} (score {a.overall_score}) disagrees with your position "
                f"(score {b.overall_score}). Review their summary and top concerns, "
                f"then either update your position with specific justification, or "
                f"defend it with new evidence. Do not concede without reason."
            ),
        ))

        # symmetric challenge at reviewer A
        messages.append(A2AMessage(
            round=round_num,
            sender="editor",
            recipient=d.reviewer_a,
            type="rebuttal_request",
            context={
                "challenger": d.reviewer_b,
                "challenger_score": b.overall_score,
                "challenger_summary": b.summary,
                "challenger_top_concerns": [c.model_dump() for c in b.concerns[:3]],
                "your_prior_score": a.overall_score,
                "your_prior_summary": a.summary,
                "disagreement_combined_score": d.combined_score,
            },
            request=(
                f"{d.reviewer_b} (score {b.overall_score}) disagrees with your position "
                f"(score {a.overall_score}). Review their summary and top concerns, "
                f"then either update your position with specific justification, or "
                f"defend it with new evidence. Do not concede without reason."
            ),
        ))
    return messages


def messages_for_reviewer(messages: list[A2AMessage], reviewer_id: str, round_num: int) -> list[A2AMessage]:
    """Filter the debate thread to rebuttal_requests addressed to `reviewer_id` in the given round."""
    return [
        m for m in messages
        if m.type == "rebuttal_request"
        and m.recipient == reviewer_id
        and m.round == round_num
    ]


def format_challenges_for_reviewer(challenges: list[A2AMessage]) -> str:
    """Render challenges as a human/LLM-readable text block to paste into the rebuttal prompt."""
    if not challenges:
        return "(no challenges this round)"
    parts: list[str] = []
    for i, m in enumerate(challenges, 1):
        ctx = m.context
        concerns_str = "\n".join(
            f"      - [{c.get('severity')}] {c.get('claim', '')[:220]}"
            for c in ctx.get("challenger_top_concerns", [])[:3]
        ) or "      (no specific concerns listed)"
        parts.append(
            f"CHALLENGE {i} — from '{ctx.get('challenger', 'unknown')}' "
            f"(their score: {ctx.get('challenger_score')}, yours was: {ctx.get('your_prior_score')}):\n"
            f"    their summary: {ctx.get('challenger_summary', '')[:400]}\n"
            f"    their top concerns:\n{concerns_str}\n"
            f"    editor's note: {m.request}"
        )
    return "\n\n".join(parts)

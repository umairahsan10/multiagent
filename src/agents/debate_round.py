"""debate_round LangGraph node.

Runs once per debate round (2+). For each reviewer that received at least one
rebuttal_request this round, invokes its `respond_to_rebuttals` method. All
reviewers with challenges are run in parallel.

Produces:
  - Updated reviews (state.reviews merged via reducer — same reviewer_id keys,
    so the old Round-1 review is replaced).
  - rebuttal_response A2AMessages for the debate thread.
  - An `announcement` A2AMessage per reviewer whose position changed — used by
    the editor's stalemate detection.
  - Increments state["round"].
"""

from __future__ import annotations

import asyncio

from src.agents.devils_advocate_reviewer import DevilsAdvocateReviewer
from src.agents.ethics_reviewer import EthicsReviewer
from src.agents.methodology_reviewer import MethodologyReviewer
from src.agents.novelty_reviewer import NoveltyReviewer
from src.logging_setup import get_logger
from src.orchestrator.a2a import messages_for_reviewer
from src.orchestrator.state import ReviewState
from src.schemas import A2AMessage

log = get_logger(__name__)

REVIEWER_CLASSES = {
    "methodology": MethodologyReviewer,
    "novelty": NoveltyReviewer,
    "devils_advocate": DevilsAdvocateReviewer,
    "ethics": EthicsReviewer,
}


async def _one_reviewer_rebuttal(
    reviewer_id: str,
    prior_review,
    challenges: list[A2AMessage],
    paper_path: str,
    round_num: int,
):
    cls = REVIEWER_CLASSES[reviewer_id]
    reviewer = cls()
    updated_review, rationale = await reviewer.respond_to_rebuttals(
        prior_review=prior_review,
        challenges=challenges,
        paper_path=paper_path,
    )

    position_changed = (
        abs(updated_review.overall_score - prior_review.overall_score) >= 0.5
        or len(updated_review.concerns) != len(prior_review.concerns)
    )

    new_msgs: list[A2AMessage] = []
    new_msgs.append(A2AMessage(
        round=round_num,
        sender=reviewer_id,
        recipient="editor",
        type="rebuttal_response",
        context={
            "prior_score": prior_review.overall_score,
            "new_score": updated_review.overall_score,
            "concern_count_before": len(prior_review.concerns),
            "concern_count_after": len(updated_review.concerns),
        },
        body=rationale,
    ))
    if position_changed:
        new_msgs.append(A2AMessage(
            round=round_num,
            sender="editor",
            recipient="editor",
            type="announcement",
            context={
                "kind": "position_changed",
                "reviewer": reviewer_id,
                "prior_score": prior_review.overall_score,
                "new_score": updated_review.overall_score,
            },
            body=f"{reviewer_id} updated position",
        ))

    return reviewer_id, updated_review, new_msgs


async def debate_round_node(state: ReviewState) -> dict:
    round_num = state.get("round", 1) + 1  # entering next round
    reviews = state.get("reviews", {})
    thread = state.get("a2a_thread", [])

    # Find the rebuttal_requests targeted at each reviewer this round.
    # Note: requests emitted by the editor carry round=CURRENT round; the debate
    # round ingesting them is CURRENT+1. So we filter on round_num - 1 which
    # is the editor's "current round" when it emitted them.
    targets: dict[str, list[A2AMessage]] = {}
    for rid in reviews:
        challenges = messages_for_reviewer(thread, rid, round_num - 1)
        if challenges:
            targets[rid] = challenges

    if not targets:
        log.info(f"[debate_round {round_num}] no reviewers have challenges; no-op")
        return {"round": round_num}

    log.info(f"[debate_round {round_num}] {len(targets)} reviewer(s) will respond: {list(targets)}")

    results = await asyncio.gather(*(
        _one_reviewer_rebuttal(
            reviewer_id=rid,
            prior_review=reviews[rid],
            challenges=chals,
            paper_path=state["paper_path"],
            round_num=round_num,
        )
        for rid, chals in targets.items()
    ))

    updated_reviews: dict = {}
    new_msgs: list[A2AMessage] = []
    for rid, updated, msgs in results:
        updated_reviews[rid] = updated
        new_msgs.extend(msgs)

    return {
        "round": round_num,
        "reviews": updated_reviews,    # merged into state via reducer
        "a2a_thread": new_msgs,         # appended via reducer
    }

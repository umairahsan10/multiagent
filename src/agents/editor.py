"""Editor node — debate-aware.

The editor is called once per round. On each call it:
  1. Recomputes pairwise disagreements from the current reviews.
  2. Re-computes the naive verdict (useful for the "no-debate vs debate" ablation).
  3. If any flagged disagreements exist and we haven't hit MAX_DEBATE_ROUNDS,
     it emits rebuttal_requests into state["a2a_thread"]. The LangGraph
     conditional edge then routes to the debate_round node.
  4. Otherwise it leaves the a2a_thread unchanged; the conditional edge routes
     to the synthesis node.

Termination conditions (any of):
  - round >= Config.MAX_DEBATE_ROUNDS
  - no pair currently flagged (convergence)
  - stalemate: no reviewer changed position last round AND we're past round 1
"""

from __future__ import annotations

from collections import Counter
from statistics import mean, pstdev

from src.config import Config
from src.logging_setup import get_logger
from src.orchestrator.a2a import build_rebuttal_requests
from src.orchestrator.disagreement import compute_pairwise_disagreements, flagged_only
from src.orchestrator.state import ReviewState
from src.schemas import Concern, Review, Verdict

log = get_logger(__name__)


def _naive_verdict(reviews: dict[str, Review]) -> Verdict:
    if not reviews:
        return Verdict(recommendation="revise", confidence=0.0, summary="no reviews available")

    scores = [r.overall_score for r in reviews.values()]
    mean_score = mean(scores)
    spread = pstdev(scores) if len(scores) > 1 else 0.0
    confidence = max(0.0, min(1.0, 1.0 - (spread / 4.5)))

    if mean_score >= 7.0:
        recommendation = "accept"
    elif mean_score >= 5.0:
        recommendation = "revise"
    else:
        recommendation = "reject"

    per_crit: dict[str, list[float]] = {}
    for r in reviews.values():
        for k, v in r.criterion_scores.items():
            per_crit.setdefault(k, []).append(v)
    per_criterion_avg = {k: round(mean(v), 2) for k, v in per_crit.items()}

    consensus_concerns: list[Concern] = []
    seen_claims: set[str] = set()
    for r in reviews.values():
        for c in r.concerns:
            key = c.claim.strip().lower()[:120]
            if key in seen_claims:
                continue
            if c.severity in ("major", "critical"):
                seen_claims.add(key)
                consensus_concerns.append(c)

    consensus_strengths: list[str] = []
    seen_strengths: set[str] = set()
    for r in reviews.values():
        for s in r.strengths[:3]:
            key = s.strip().lower()[:120]
            if key in seen_strengths:
                continue
            seen_strengths.add(key)
            consensus_strengths.append(s)

    severity_counts = Counter(c.severity for r in reviews.values() for c in r.concerns)
    summary = (
        f"Aggregation of {len(reviews)} reviewers. "
        f"Mean score {mean_score:.2f} (pm{spread:.2f}). "
        f"Concern counts - critical: {severity_counts.get('critical', 0)}, "
        f"major: {severity_counts.get('major', 0)}, minor: {severity_counts.get('minor', 0)}."
    )

    return Verdict(
        recommendation=recommendation,
        confidence=round(confidence, 3),
        per_criterion_scores=per_criterion_avg,
        consensus_strengths=consensus_strengths[:8],
        consensus_concerns=consensus_concerns[:12],
        contested_claims=[],  # synthesis node fills this in
        suggested_revisions=[],
        summary=summary,
    )


def _detect_stalemate(state: ReviewState) -> bool:
    """True if the most recently completed debate round produced no position changes.

    Each debate_round sets state["round"] to its own round number when it finishes
    (e.g. 2 after the first debate round) and emits any `position_changed`
    announcements at the same round number. So when editor runs with round=N>=2,
    we check announcements at round=N — those are from the just-completed debate.
    """
    round_num = state.get("round", 1)
    if round_num < 2:
        return False
    thread = state.get("a2a_thread", [])
    just_completed_announcements = [
        m for m in thread
        if m.type == "announcement"
        and m.round == round_num
        and m.context.get("kind") == "position_changed"
    ]
    return len(just_completed_announcements) == 0


async def editor_node(state: ReviewState) -> dict:
    reviews = state.get("reviews", {})
    round_num = state.get("round", 1)
    log.info(f"[editor] round {round_num}: aggregating {len(reviews)} reviews")

    disagreements = compute_pairwise_disagreements(reviews)
    flagged = flagged_only(disagreements)
    verdict = _naive_verdict(reviews)

    stalemate = _detect_stalemate(state)
    at_max = round_num >= Config.MAX_DEBATE_ROUNDS

    log.info(
        f"[editor] {len(flagged)}/{len(disagreements)} pairs flagged, "
        f"round={round_num}/{Config.MAX_DEBATE_ROUNDS}, stalemate={stalemate}"
    )

    update: dict = {
        "disagreements": disagreements,
        "verdict": verdict,
    }

    # Decide whether to request another debate round
    should_debate = bool(flagged) and not at_max and not stalemate
    if should_debate:
        new_msgs = build_rebuttal_requests(flagged, reviews, round_num)
        log.info(f"[editor] emitting {len(new_msgs)} rebuttal_requests for round {round_num + 1}")
        update["a2a_thread"] = new_msgs  # reducer appends
    else:
        reasons = []
        if at_max:
            reasons.append(f"reached MAX_DEBATE_ROUNDS={Config.MAX_DEBATE_ROUNDS}")
        if stalemate:
            reasons.append("stalemate (no position changed last round)")
        if not flagged:
            reasons.append("convergence (no flagged disagreements)")
        log.info(f"[editor] routing to synthesis. reason(s): {', '.join(reasons) or 'unknown'}")

    return update


def should_continue_debate(state: ReviewState) -> str:
    """Conditional-edge decision function. Returns the name of the next node."""
    flagged = flagged_only(state.get("disagreements", []))
    round_num = state.get("round", 1)
    at_max = round_num >= Config.MAX_DEBATE_ROUNDS
    stalemate = _detect_stalemate(state)
    if flagged and not at_max and not stalemate:
        return "debate_round"
    return "synthesis"

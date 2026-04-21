"""Editor node.

In Phase 5 the editor does two things:
  1. Compute pairwise disagreements across all Round 1 reviews.
  2. Produce a naive aggregated verdict (arithmetic-mean score, majority severity
     for each concern). This verdict is the "no-debate baseline" that Phase 6's
     post-debate verdict is compared against — that comparison is one of the
     ablations in the final report.

Phase 6 will extend this node to also:
  - Emit rebuttal_request A2A messages for flagged disagreements.
  - Loop back into debate rounds.
  - Produce a final Verdict with contested_claims when debate terminates.

Keeping the node lean in Phase 5 so the LangGraph plumbing is provably correct
before the debate logic lands on top.
"""

from __future__ import annotations

from collections import Counter
from statistics import mean, pstdev

from src.logging_setup import get_logger
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
    # High agreement among reviewers = high confidence. Max possible pstdev for
    # scores in [1,10] is ~4.5; normalize by that.
    confidence = max(0.0, min(1.0, 1.0 - (spread / 4.5)))

    if mean_score >= 7.0:
        recommendation = "accept"
    elif mean_score >= 5.0:
        recommendation = "revise"
    else:
        recommendation = "reject"

    # Average per-criterion scores across reviewers (criterion sets differ by
    # persona, so we take whichever keys appear)
    per_crit: dict[str, list[float]] = {}
    for r in reviews.values():
        for k, v in r.criterion_scores.items():
            per_crit.setdefault(k, []).append(v)
    per_criterion_avg = {k: round(mean(v), 2) for k, v in per_crit.items()}

    # Consensus concerns: anything rated major or critical by any reviewer makes
    # the list. Phase 6 will refine this by only keeping concerns that survive
    # debate. For now, include them all.
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
        f"Naive Round-1 aggregation of {len(reviews)} reviewers. "
        f"Mean score {mean_score:.2f} (±{spread:.2f}). "
        f"Concern counts — critical: {severity_counts.get('critical', 0)}, "
        f"major: {severity_counts.get('major', 0)}, minor: {severity_counts.get('minor', 0)}."
    )

    return Verdict(
        recommendation=recommendation,
        confidence=round(confidence, 3),
        per_criterion_scores=per_criterion_avg,
        consensus_strengths=consensus_strengths[:8],
        consensus_concerns=consensus_concerns[:12],
        contested_claims=[],   # populated in Phase 6 from A2A debate
        suggested_revisions=[],
        summary=summary,
    )


async def editor_node(state: ReviewState) -> dict:
    reviews = state.get("reviews", {})
    log.info(f"[editor] Round {state.get('round', 1)}: aggregating {len(reviews)} reviews")

    disagreements = compute_pairwise_disagreements(reviews)
    flagged = flagged_only(disagreements)
    log.info(f"[editor] {len(flagged)}/{len(disagreements)} pairs flagged for debate")

    verdict = _naive_verdict(reviews)
    log.info(f"[editor] verdict: {verdict.recommendation} (confidence {verdict.confidence})")

    return {
        "disagreements": disagreements,
        "verdict": verdict,
    }

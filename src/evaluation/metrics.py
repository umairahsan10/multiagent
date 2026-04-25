"""Evaluation metrics.

Four metrics for the IEEE report:

1. verdict_correlation(system_runs, ground_truth)
      Does system's accept/reject/revise match the human decision?
      Returns overall accuracy + a per-class breakdown.

2. issue_detection_pr(system_runs, ground_truth)
      Precision/recall of system concerns vs. human-flagged concerns.
      The overlap decision is NOT fully automated — we use soft matching via
      semantic embedding similarity, but the report should note this is a
      coarse proxy and the "authoritative" P/R requires manual annotation.

3. inter_agent_disagreement(system_runs)
      Mean pairwise cosine distance between reviewer summaries, aggregated
      across papers. The shape of the distribution tells us whether our
      heterogeneous panel genuinely disagrees — a unimodal distribution
      centered near zero would mean redundant reviewers.

4. citation_hallucination_rate(system_runs)
      Walks every paper-reference-like string in every review and verifies
      against OpenAlex. Fraction that don't exist = hallucination rate.
      Reported before / after the auditor (Phase 8) to show the auditor's effect.

All four return plain dicts so they serialize straight to JSON for the report.
"""

from __future__ import annotations

import re
from collections import Counter
from statistics import mean, pstdev
from typing import Any

import numpy as np

from src.orchestrator.disagreement import _cosine_distance
from src.schemas import Concern, Review, Verdict


# ----- metric 1 ---------------------------------------------------------------

def verdict_correlation(
    system_runs: list[dict],
    ground_truth: dict[str, dict],
) -> dict[str, Any]:
    """Compare system's `verdict.recommendation` with human `decision`.

    Args:
        system_runs: [{"paper_id": str, "verdict": Verdict, ...}, ...]
        ground_truth: {paper_id: {"decision": "accept" | "reject" | "revise"}}

    Returns:
        {
          "n": int,
          "accuracy": float,
          "confusion": {true_label: {predicted_label: count}},
          "per_class": {class: {"precision": float, "recall": float, "f1": float}}
        }
    """
    matched: list[tuple[str, str]] = []  # (true, pred)
    for run in system_runs:
        pid = run["paper_id"]
        if pid not in ground_truth:
            continue
        pred = run["verdict"].recommendation
        true = ground_truth[pid]["decision"]
        matched.append((true, pred))

    n = len(matched)
    if n == 0:
        return {"n": 0, "accuracy": 0.0, "confusion": {}, "per_class": {}}

    correct = sum(1 for t, p in matched if t == p)
    accuracy = correct / n

    classes = ["accept", "revise", "reject"]
    confusion: dict[str, dict[str, int]] = {t: {p: 0 for p in classes} for t in classes}
    for t, p in matched:
        if t in confusion and p in confusion[t]:
            confusion[t][p] += 1

    per_class: dict[str, dict[str, float]] = {}
    for cls in classes:
        tp = confusion[cls][cls]
        fp = sum(confusion[other][cls] for other in classes if other != cls)
        fn = sum(confusion[cls][other] for other in classes if other != cls)
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        per_class[cls] = {
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
        }

    return {
        "n": n,
        "accuracy": round(accuracy, 3),
        "confusion": confusion,
        "per_class": per_class,
    }


# ----- metric 2 ---------------------------------------------------------------

def _concern_overlap_score(a: str, b: str) -> float:
    """Semantic similarity between two concern texts (1 - cosine distance).

    Uses the MiniLM model loaded in disagreement.py.
    """
    return 1.0 - _cosine_distance(a, b)


def issue_detection_pr(
    system_runs: list[dict],
    ground_truth: dict[str, dict],
    similarity_threshold: float = 0.65,
) -> dict[str, Any]:
    """Precision/recall of system-flagged concerns vs. human-flagged concerns.

    A system concern is considered to "match" a human concern if their
    similarity score >= similarity_threshold. This is a COARSE automation of
    what should ideally be manual annotation. Report the threshold alongside
    the numbers.

    ground_truth[paper_id]["human_concerns"] should be a list[str].
    """
    per_paper: list[dict] = []
    total_tp = total_fp = total_fn = 0

    for run in system_runs:
        pid = run["paper_id"]
        if pid not in ground_truth:
            continue
        human_concerns: list[str] = ground_truth[pid].get("human_concerns", []) or []
        if not human_concerns:
            continue  # can't compute P/R without human reference

        reviews: dict[str, Review] = run.get("final_reviews", {})
        system_concerns_texts = [
            c.claim for r in reviews.values() for c in r.concerns
        ]
        if not system_concerns_texts:
            per_paper.append({"paper_id": pid, "precision": 0.0, "recall": 0.0})
            total_fn += len(human_concerns)
            continue

        matched_system: set[int] = set()
        matched_human: set[int] = set()
        for i, sc in enumerate(system_concerns_texts):
            for j, hc in enumerate(human_concerns):
                if _concern_overlap_score(sc, hc) >= similarity_threshold:
                    matched_system.add(i)
                    matched_human.add(j)

        tp = len(matched_system)
        fp = len(system_concerns_texts) - tp
        fn = len(human_concerns) - len(matched_human)
        total_tp += tp
        total_fp += fp
        total_fn += fn

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        per_paper.append({
            "paper_id": pid,
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "tp": tp, "fp": fp, "fn": fn,
        })

    micro_p = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    micro_r = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    micro_f1 = 2 * micro_p * micro_r / (micro_p + micro_r) if (micro_p + micro_r) > 0 else 0.0

    return {
        "n_papers_with_gt": len(per_paper),
        "similarity_threshold": similarity_threshold,
        "micro_precision": round(micro_p, 3),
        "micro_recall": round(micro_r, 3),
        "micro_f1": round(micro_f1, 3),
        "totals": {"tp": total_tp, "fp": total_fp, "fn": total_fn},
        "per_paper": per_paper,
    }


# ----- metric 3 ---------------------------------------------------------------

def inter_agent_disagreement(system_runs: list[dict]) -> dict[str, Any]:
    """Aggregate pairwise disagreement signals across all papers.

    Returns mean and std of (a) score spreads and (b) summary cosine distances,
    plus a histogram for the paper's plot.
    """
    spreads: list[float] = []
    cos_dists: list[float] = []
    combined: list[float] = []
    for run in system_runs:
        for d in run.get("disagreements", []):
            spreads.append(d.score_spread)
            cos_dists.append(d.summary_cosine_distance)
            combined.append(d.combined_score)

    def summary(xs: list[float]) -> dict[str, float]:
        if not xs:
            return {"n": 0, "mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0}
        return {
            "n": len(xs),
            "mean": round(mean(xs), 3),
            "std": round(pstdev(xs), 3),
            "min": round(min(xs), 3),
            "max": round(max(xs), 3),
        }

    # 10-bin histogram of combined scores
    hist: list[int] = [0] * 10
    for c in combined:
        idx = min(int(c * 10), 9)
        hist[idx] += 1

    return {
        "score_spread": summary(spreads),
        "summary_cosine_distance": summary(cos_dists),
        "combined_score": summary(combined),
        "combined_histogram": hist,  # 0-0.1, 0.1-0.2, ..., 0.9-1.0
    }


# ----- metric 4 ---------------------------------------------------------------

_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _extract_citation_candidates(review: Review) -> list[str]:
    """Pull citation-like strings out of a review's concern/strength texts.

    Keeps strings that look like a title or author+year fragment. Heuristic;
    the hallucination auditor in Phase 8 will do a better job.
    """
    candidates: list[str] = []
    for c in review.concerns:
        text = f"{c.claim} {c.evidence}"
        for m in _YEAR_RE.finditer(text):
            start = max(0, m.start() - 120)
            end = min(len(text), m.end() + 120)
            candidates.append(text[start:end].strip())
    for s in review.strengths:
        for m in _YEAR_RE.finditer(s):
            start = max(0, m.start() - 120)
            end = min(len(s), m.end() + 120)
            candidates.append(s[start:end].strip())
    return candidates


def collect_citation_candidates(system_runs: list[dict]) -> list[tuple[str, str, str]]:
    """Walk all reviews, return (paper_id, reviewer_id, candidate_text) tuples.

    Caller resolves verification asynchronously, then calls
    `citation_hallucination_rate` with the resolved dict.
    """
    out: list[tuple[str, str, str]] = []
    for run in system_runs:
        pid = run["paper_id"]
        reviews: dict[str, Review] = run.get("final_reviews", {})
        for rid, r in reviews.items():
            for cand in _extract_citation_candidates(r):
                out.append((pid, rid, cand))
    return out


def citation_hallucination_rate(
    candidates: list[tuple[str, str, str]],
    verified: dict[str, bool],
) -> dict[str, Any]:
    """Aggregate hallucination stats from pre-resolved verifications.

    Args:
        candidates: from `collect_citation_candidates`
        verified: {candidate_text: bool}, True if the citation was found in OpenAlex.

    Returns fraction that were NOT verified (hallucinated or too fuzzy to match).
    """
    per_reviewer: Counter[str] = Counter()
    per_reviewer_failed: Counter[str] = Counter()
    total = len(candidates)
    not_verified = 0
    for _pid, rid, cand in candidates:
        per_reviewer[rid] += 1
        if not verified.get(cand, False):
            not_verified += 1
            per_reviewer_failed[rid] += 1

    rate = (not_verified / total) if total > 0 else 0.0
    by_reviewer = {
        rid: {
            "total": per_reviewer[rid],
            "unverified": per_reviewer_failed[rid],
            "rate": round(per_reviewer_failed[rid] / per_reviewer[rid], 3) if per_reviewer[rid] else 0.0,
        }
        for rid in per_reviewer
    }
    return {
        "total_citations": total,
        "unverified_citations": not_verified,
        "hallucination_rate": round(rate, 3),
        "by_reviewer": by_reviewer,
    }

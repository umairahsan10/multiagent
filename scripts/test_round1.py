"""Phase 5 end-to-end: run the LangGraph Round-1 fan-out on the test paper.

Differs from scripts/test_all_reviewers.py in that the flow runs through
LangGraph's state machine, with the editor computing pairwise disagreements
and emitting a naive aggregated verdict.

    python scripts/test_round1.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import Config  # noqa: E402
from src.orchestrator.graph import build_round1_graph, initial_state  # noqa: E402

PAPER = Config.PAPER_CACHE_DIR.parent / "papers" / "attention_is_all_you_need.pdf"
OUT_DIR = Config.DATA_DIR / "round1_graph"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def banner(s: str) -> None:
    print("\n" + "=" * 72 + f"\n  {s}\n" + "=" * 72)


def print_disagreement_matrix(disagreements) -> None:
    header = f"  {'pair':<44} score_spread  cos_dist  combined  flagged"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for d in sorted(disagreements, key=lambda x: -x.combined_score):
        mark = " *" if d.flagged else "  "
        pair = f"{d.reviewer_a} vs {d.reviewer_b}"
        print(f"  {pair:<44} {d.score_spread:<12.2f}  {d.summary_cosine_distance:<8.3f}  "
              f"{d.combined_score:<8.3f} {mark}")


async def main() -> int:
    if not PAPER.exists():
        print(f"Missing test PDF: {PAPER}\nRun: python scripts/download_test_paper.py")
        return 1

    banner(f"Running Round-1 LangGraph on: {PAPER.name}")
    graph = build_round1_graph()

    t0 = time.time()
    final = await graph.ainvoke(initial_state(str(PAPER)))
    wall = time.time() - t0

    reviews = final.get("reviews", {})
    disagreements = final.get("disagreements", [])
    verdict = final.get("verdict")

    banner("REVIEWS SUMMARY")
    for rid, r in reviews.items():
        print(f"  [{rid:<16}] score={r.overall_score:.1f}  concerns={len(r.concerns)}  "
              f"tool_calls={len(r.tool_calls)}")

    banner("PAIRWISE DISAGREEMENT MATRIX")
    print_disagreement_matrix(disagreements)

    banner("NAIVE AGGREGATED VERDICT (Round 1 only, pre-debate)")
    print(f"  recommendation   : {verdict.recommendation}")
    print(f"  confidence       : {verdict.confidence}")
    print(f"  per_criterion    : {verdict.per_criterion_scores}")
    print(f"  summary          : {verdict.summary}")
    print(f"  consensus concerns ({len(verdict.consensus_concerns)}):")
    for c in verdict.consensus_concerns[:6]:
        print(f"    - [{c.severity}/{c.confidence:.2f}] {c.claim[:140]}")
    print(f"  consensus strengths ({len(verdict.consensus_strengths)}):")
    for s in verdict.consensus_strengths[:5]:
        print(f"    + {s[:140]}")

    banner(f"WALL TIME: {wall:.1f}s")

    # Persist the full graph state for inspection + Phase 6 debate-vs-nodebate ablation
    out_path = OUT_DIR / "round1_state.json"
    serializable = {
        "paper_path": str(PAPER),
        "round": final.get("round", 1),
        "wall_time_seconds": round(wall, 2),
        "reviews": {rid: r.model_dump() for rid, r in reviews.items()},
        "disagreements": [d.model_dump() for d in disagreements],
        "verdict": verdict.model_dump() if verdict else None,
    }
    out_path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    print(f"\nSaved full graph state: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

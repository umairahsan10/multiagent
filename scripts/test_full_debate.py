"""Phase 6 end-to-end: Round 1 → debate loop → synthesis.

    python scripts/test_full_debate.py

Prints the round-by-round story (score changes, rebuttal bodies, final verdict
with contested_claims). Writes full debate state to data/full_debate/.
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
from src.orchestrator.graph import build_debate_graph, initial_state  # noqa: E402
from src.schemas import A2AMessage  # noqa: E402

PAPER = Config.PAPER_CACHE_DIR.parent / "papers" / "attention_is_all_you_need.pdf"
OUT_DIR = Config.DATA_DIR / "full_debate"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def banner(s: str) -> None:
    print("\n" + "=" * 72 + f"\n  {s}\n" + "=" * 72)


def print_thread(thread: list[A2AMessage]) -> None:
    if not thread:
        print("  (empty — no debate occurred)")
        return
    for m in thread:
        if m.type == "rebuttal_request":
            ctx = m.context
            print(f"  [R{m.round}] REQ editor -> {m.recipient:<16} "
                  f"(challenger={ctx.get('challenger')}, score {ctx.get('challenger_score')} vs {ctx.get('your_prior_score')})")
        elif m.type == "rebuttal_response":
            ctx = m.context
            before, after = ctx.get("prior_score"), ctx.get("new_score")
            arrow = "+" if after > before else ("-" if after < before else "=")
            body = (m.body or "").replace("\n", " ")[:160]
            print(f"  [R{m.round}] RES {m.sender:<16} -> editor  score {before} -> {after} ({arrow})")
            print(f"           {body}")
        elif m.type == "announcement":
            print(f"  [R{m.round}] ANN {m.context.get('kind')}: {m.body}")


async def main() -> int:
    if not PAPER.exists():
        print(f"Missing test PDF: {PAPER}\nRun: python scripts/download_test_paper.py")
        return 1

    banner(f"Full debate graph on: {PAPER.name}")
    print(f"  MAX_DEBATE_ROUNDS     = {Config.MAX_DEBATE_ROUNDS}")
    print(f"  DISAGREEMENT_THRESHOLD = {Config.DISAGREEMENT_THRESHOLD}")

    graph = build_debate_graph()
    t0 = time.time()
    final = await graph.ainvoke(
        initial_state(str(PAPER)),
        {"recursion_limit": 50},  # headroom for debate loops
    )
    wall = time.time() - t0

    reviews = final.get("reviews", {})
    thread = final.get("a2a_thread", [])
    disagreements = final.get("disagreements", [])
    verdict = final.get("verdict")

    banner(f"FINAL REVIEWER POSITIONS (after round {final.get('round')})")
    for rid in sorted(reviews):
        r = reviews[rid]
        print(f"  [{rid:<16}] score={r.overall_score:.1f} concerns={len(r.concerns)} "
              f"strengths={len(r.strengths)} tool_calls={len(r.tool_calls)}")

    banner(f"A2A DEBATE THREAD ({len(thread)} messages)")
    print_thread(thread)

    banner("FINAL PAIRWISE DISAGREEMENT MATRIX")
    for d in sorted(disagreements, key=lambda x: -x.combined_score):
        mark = " *" if d.flagged else "  "
        print(f"  {d.reviewer_a:<18} vs {d.reviewer_b:<18} "
              f"spread={d.score_spread:.2f} cos_dist={d.summary_cosine_distance:.3f} "
              f"combined={d.combined_score:.3f}{mark}")

    banner("SYNTHESIZED VERDICT")
    if verdict:
        print(f"  recommendation : {verdict.recommendation}")
        print(f"  confidence     : {verdict.confidence}")
        print(f"  per_criterion  : {len(verdict.per_criterion_scores)} keys")
        print(f"  strengths      : {len(verdict.consensus_strengths)}")
        print(f"  concerns       : {len(verdict.consensus_concerns)}")
        print(f"  contested      : {len(verdict.contested_claims)}")
        print(f"  suggested rev  : {len(verdict.suggested_revisions)}")
        print(f"\n  summary: {verdict.summary}")
        if verdict.contested_claims:
            print("\n  CONTESTED CLAIMS:")
            for cc in verdict.contested_claims:
                print(f"    - {cc.claim[:200]}")
                for reviewer, pos in cc.positions.items():
                    print(f"        {reviewer:<16} {pos}")
                print(f"        editor_note: {cc.editor_note[:160]}")

    banner(f"WALL TIME: {wall:.1f}s")

    # Persist full debate state
    out_path = OUT_DIR / "debate_state.json"
    serializable = {
        "paper_path": str(PAPER),
        "final_round": final.get("round"),
        "wall_time_seconds": round(wall, 2),
        "reviews": {rid: r.model_dump() for rid, r in reviews.items()},
        "disagreements": [d.model_dump() for d in disagreements],
        "a2a_thread": [m.model_dump() for m in thread],
        "verdict": verdict.model_dump() if verdict else None,
    }
    out_path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")
    print(f"\nSaved debate state: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

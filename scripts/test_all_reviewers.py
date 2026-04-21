"""Phase 4 end-to-end: run all four reviewers in parallel, compare side-by-side.

    python scripts/test_all_reviewers.py

Writes a JSON per reviewer into data/round1/.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.agents.devils_advocate_reviewer import DevilsAdvocateReviewer  # noqa: E402
from src.agents.ethics_reviewer import EthicsReviewer  # noqa: E402
from src.agents.methodology_reviewer import MethodologyReviewer  # noqa: E402
from src.agents.novelty_reviewer import NoveltyReviewer  # noqa: E402
from src.config import Config  # noqa: E402
from src.schemas import Review  # noqa: E402

PAPER = Config.PAPER_CACHE_DIR.parent / "papers" / "attention_is_all_you_need.pdf"
OUT_DIR = Config.DATA_DIR / "round1"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def banner(s: str) -> None:
    print("\n" + "=" * 72 + f"\n  {s}\n" + "=" * 72)


async def run_one(reviewer, paper_path: str) -> tuple[str, Review, float]:
    t0 = time.time()
    r = await reviewer.review(paper_path)
    return reviewer.reviewer_id, r, time.time() - t0


def comparison_table(reviews: dict[str, Review]) -> str:
    ids = ["methodology", "novelty", "devils_advocate", "ethics"]
    rows = [
        ("overall_score",      [f"{reviews[i].overall_score:.1f}" for i in ids]),
        ("num_strengths",      [str(len(reviews[i].strengths)) for i in ids]),
        ("num_concerns",       [str(len(reviews[i].concerns)) for i in ids]),
        ("critical_concerns",  [str(sum(1 for c in reviews[i].concerns if c.severity == "critical")) for i in ids]),
        ("major_concerns",     [str(sum(1 for c in reviews[i].concerns if c.severity == "major")) for i in ids]),
        ("tool_calls",         [str(len(reviews[i].tool_calls)) for i in ids]),
    ]
    col_w = 16
    head = "".ljust(22) + "".join(i[:col_w].ljust(col_w) for i in ids)
    body = "\n".join(name.ljust(22) + "".join(v.ljust(col_w) for v in vals) for name, vals in rows)
    return head + "\n" + body


def pairwise_score_spread(reviews: dict[str, Review]) -> str:
    ids = ["methodology", "novelty", "devils_advocate", "ethics"]
    lines = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            d = abs(reviews[a].overall_score - reviews[b].overall_score)
            marker = "  *" if d >= 1.5 else ""
            lines.append(f"  {a:<18} vs {b:<18} deltascore = {d:.1f}{marker}")
    return "\n".join(lines)


async def main() -> int:
    if not PAPER.exists():
        print(f"Missing test PDF: {PAPER}\nRun: python scripts/download_test_paper.py")
        return 1

    reviewers = [
        MethodologyReviewer(),
        NoveltyReviewer(),
        DevilsAdvocateReviewer(),
        EthicsReviewer(),
    ]
    banner(f"Running {len(reviewers)} reviewers in parallel on: {PAPER.name}")

    t0 = time.time()
    results = await asyncio.gather(*(run_one(r, str(PAPER)) for r in reviewers))
    wall = time.time() - t0

    reviews: dict[str, Review] = {}
    for rid, review, dt in results:
        reviews[rid] = review
        print(f"  [{rid:>16}] done in {dt:.1f}s — score={review.overall_score:.1f}, "
              f"concerns={len(review.concerns)}, tool_calls={len(review.tool_calls)}")
    print(f"\n  total wall time (parallel): {wall:.1f}s")

    banner("SIDE-BY-SIDE COMPARISON")
    print(comparison_table(reviews))

    banner("PAIRWISE SCORE SPREAD (delta >= 1.5 flagged *)")
    print(pairwise_score_spread(reviews))

    banner("REPRESENTATIVE CONCERNS (first 2 per reviewer)")
    for rid in ["methodology", "novelty", "devils_advocate", "ethics"]:
        r = reviews[rid]
        print(f"\n[{rid}]")
        for c in r.concerns[:2]:
            print(f"  - [{c.severity}/{c.confidence:.2f}] {c.claim[:180]}")

    for rid, r in reviews.items():
        out = OUT_DIR / f"review_{rid}.json"
        out.write_text(json.dumps(r.model_dump(), indent=2), encoding="utf-8")
    banner(f"Saved {len(reviews)} reviews to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

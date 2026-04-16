"""Phase 2 end-to-end test: PDF -> structured Review.

    python scripts/test_methodology_reviewer.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.agents.methodology_reviewer import MethodologyReviewer  # noqa: E402
from src.config import Config  # noqa: E402

PAPER = Config.PAPER_CACHE_DIR.parent / "papers" / "attention_is_all_you_need.pdf"


def banner(s: str) -> None:
    print("\n" + "=" * 60 + f"\n  {s}\n" + "=" * 60)


async def main() -> int:
    if not PAPER.exists():
        print(f"Missing test PDF: {PAPER}\nRun: python scripts/download_test_paper.py")
        return 1

    reviewer = MethodologyReviewer()
    review = await reviewer.review(str(PAPER))

    banner("REVIEW (structured)")
    print(f"reviewer       : {review.reviewer_id}")
    print(f"overall_score  : {review.overall_score}")
    print(f"criterion      : {review.criterion_scores}")
    print(f"strengths      : {len(review.strengths)}")
    for s in review.strengths[:5]:
        print(f"  + {s}")
    print(f"concerns       : {len(review.concerns)}")
    for c in review.concerns:
        print(f"  - [{c.severity}/{c.confidence:.2f}] {c.claim}")
        print(f"      evidence: {c.evidence[:140]}")
    print(f"\nsummary        : {review.summary}")

    banner("TOOL CALL AUDIT")
    for tc in review.tool_calls:
        print(f"  {tc.server}.{tc.tool}({list(tc.args.keys())}) -> {tc.result_summary[:80]}")

    out_path = Config.DATA_DIR / "review_methodology_attention.json"
    out_path.write_text(json.dumps(review.model_dump(), indent=2), encoding="utf-8")
    print(f"\nSaved: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

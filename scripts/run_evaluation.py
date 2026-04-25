"""Run the full evaluation pipeline on the corpus and dump metrics.

Prerequisites:
    1. data/eval_corpus/papers/*.pdf (run scripts/fetch_openreview.py first)
    2. Optionally data/eval_corpus/ground_truth.json for metrics that need it

Usage:
    python scripts/run_evaluation.py              # run all cached + new papers
    python scripts/run_evaluation.py --max 3      # only first 3 (smoke test)
    python scripts/run_evaluation.py --no-cache   # re-run all papers

Output:
    data/eval_results/runs/<paper>.json   — full graph state per paper (cached)
    data/eval_results/metrics.json         — aggregated metrics
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import Config  # noqa: E402
from src.evaluation.harness import load_ground_truth, run_corpus  # noqa: E402
from src.evaluation.metrics import (  # noqa: E402
    citation_hallucination_rate,
    collect_citation_candidates,
    inter_agent_disagreement,
    issue_detection_pr,
    verdict_correlation,
)
from src.logging_setup import get_logger  # noqa: E402

log = get_logger(__name__)

CORPUS_DIR = Config.DATA_DIR / "eval_corpus"
RESULTS_DIR = Config.DATA_DIR / "eval_results"


async def _verify_citations_batch(texts: list[str]) -> dict[str, bool]:
    """Resolve a batch of citation candidates against OpenAlex in a single MCP session.

    Deduplicates inputs and short-circuits empty strings.
    """
    from src.clients.mcp_client import mcp_session, unwrap_tool_result

    unique = sorted({(t or "").strip()[:240] for t in texts if (t or "").strip()})
    if not unique:
        return {}
    out: dict[str, bool] = {}
    async with mcp_session("openalex") as s:
        for text in unique:
            try:
                r = await s.call_tool("verify_paper_exists", {"title": text})
                out[text] = bool(unwrap_tool_result(r).get("exists"))
            except Exception as e:
                log.warning(f"[eval] verify failed for '{text[:60]}...': {e}")
                out[text] = False
    return out


async def main_async(max_papers: int | None, use_cache: bool) -> int:
    log.info(f"[eval] corpus={CORPUS_DIR}  results={RESULTS_DIR}  max={max_papers}")
    runs = await run_corpus(
        corpus_dir=CORPUS_DIR,
        results_dir=RESULTS_DIR,
        use_cache=use_cache,
        max_papers=max_papers,
    )
    if not runs:
        log.error("[eval] no papers ran")
        return 1

    ground_truth = load_ground_truth(CORPUS_DIR)
    log.info(f"[eval] loaded ground truth for {len(ground_truth)} papers")

    metrics: dict = {}
    metrics["n_runs"] = len(runs)
    metrics["papers_with_ground_truth"] = sum(1 for r in runs if r["paper_id"] in ground_truth)

    log.info("[eval] computing metric 1: verdict_correlation")
    metrics["verdict_correlation"] = verdict_correlation(runs, ground_truth)

    log.info("[eval] computing metric 2: issue_detection_pr")
    metrics["issue_detection_pr"] = issue_detection_pr(runs, ground_truth)

    log.info("[eval] computing metric 3: inter_agent_disagreement")
    metrics["inter_agent_disagreement"] = inter_agent_disagreement(runs)

    log.info("[eval] computing metric 4: citation_hallucination_rate (hits OpenAlex)")
    candidates = collect_citation_candidates(runs)
    log.info(f"[eval]   {len(candidates)} citation candidates to verify")
    keys_to_resolve = [c[2][:240].strip() for c in candidates]
    verified = await _verify_citations_batch(keys_to_resolve)
    # remap candidates to their normalized keys for lookup
    candidates_normed = [(pid, rid, (cand or "").strip()[:240]) for (pid, rid, cand) in candidates]
    metrics["citation_hallucination_rate"] = citation_hallucination_rate(candidates_normed, verified)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / "metrics.json"
    out_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    log.info(f"[eval] wrote {out_path}")

    # Console summary
    print("\n" + "=" * 60)
    print(f"  Evaluation on {len(runs)} papers ({metrics['papers_with_ground_truth']} with ground truth)")
    print("=" * 60)
    vc = metrics["verdict_correlation"]
    if vc["n"] > 0:
        print(f"  Verdict accuracy         : {vc['accuracy']}  ({vc['n']} papers)")
    idpr = metrics["issue_detection_pr"]
    if idpr["n_papers_with_gt"] > 0:
        print(f"  Issue detection micro-F1 : {idpr['micro_f1']}  "
              f"(P={idpr['micro_precision']}, R={idpr['micro_recall']})")
    iad = metrics["inter_agent_disagreement"]
    print(f"  Mean pairwise disagreement : "
          f"combined={iad['combined_score']['mean']:.3f}  "
          f"score_spread={iad['score_spread']['mean']:.2f}")
    chr_ = metrics["citation_hallucination_rate"]
    print(f"  Citation hallucination     : {chr_['hallucination_rate']}  "
          f"({chr_['unverified_citations']}/{chr_['total_citations']} unverified)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=None, help="max papers to run (smoke test)")
    ap.add_argument("--no-cache", action="store_true", help="re-run all papers")
    args = ap.parse_args()
    return asyncio.run(main_async(max_papers=args.max, use_cache=not args.no_cache))


if __name__ == "__main__":
    sys.exit(main())

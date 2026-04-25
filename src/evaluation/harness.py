"""Evaluation harness.

Takes a directory of PDFs and an optional ground-truth JSON, runs the full
debate graph on each, caches results, and computes the four metrics.

Corpus directory layout:
    data/eval_corpus/
        papers/
            paper_a.pdf
            paper_b.pdf
        ground_truth.json   # {filename: {"decision": "accept|reject|revise", "human_concerns": [...]}}

Results directory layout (auto-created):
    data/eval_results/
        runs/
            paper_a.json    # full per-paper graph state
        metrics.json        # aggregated scores
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from src.logging_setup import get_logger
from src.orchestrator.graph import build_debate_graph, initial_state
from src.schemas import Review, Verdict

log = get_logger(__name__)


async def run_one_paper(graph, pdf_path: Path) -> dict[str, Any]:
    """Run the debate graph on a single paper; return a normalized record."""
    t0 = time.time()
    final = await graph.ainvoke(
        initial_state(str(pdf_path)),
        {"recursion_limit": 50},
    )
    wall = time.time() - t0

    verdict: Verdict = final.get("verdict")
    reviews: dict[str, Review] = final.get("reviews", {})
    record = {
        "paper_id": pdf_path.name,
        "paper_path": str(pdf_path),
        "wall_seconds": round(wall, 2),
        "final_round": final.get("round", 1),
        "final_reviews": reviews,
        "disagreements": final.get("disagreements", []),
        "a2a_thread": final.get("a2a_thread", []),
        "verdict": verdict,
    }
    return record


def _serialize_record(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "paper_id": rec["paper_id"],
        "paper_path": rec["paper_path"],
        "wall_seconds": rec["wall_seconds"],
        "final_round": rec["final_round"],
        "final_reviews": {rid: r.model_dump() for rid, r in rec["final_reviews"].items()},
        "disagreements": [d.model_dump() for d in rec["disagreements"]],
        "a2a_thread": [m.model_dump() for m in rec["a2a_thread"]],
        "verdict": rec["verdict"].model_dump() if rec["verdict"] else None,
    }


async def run_corpus(
    corpus_dir: Path,
    results_dir: Path,
    *,
    use_cache: bool = True,
    max_papers: int | None = None,
) -> list[dict[str, Any]]:
    """Run the graph on every PDF in `corpus_dir/papers/`, cache per-paper JSON."""
    papers_dir = corpus_dir / "papers"
    runs_dir = results_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(papers_dir.glob("*.pdf"))
    if max_papers is not None:
        pdfs = pdfs[:max_papers]
    if not pdfs:
        raise RuntimeError(f"no PDFs found in {papers_dir}")

    log.info(f"[harness] running on {len(pdfs)} papers from {papers_dir}")
    graph = build_debate_graph()

    records: list[dict[str, Any]] = []
    for i, pdf in enumerate(pdfs, 1):
        cache_path = runs_dir / f"{pdf.stem}.json"
        if use_cache and cache_path.exists():
            log.info(f"[harness] ({i}/{len(pdfs)}) cached: {pdf.name}")
            records.append(_load_record(cache_path))
            continue

        log.info(f"[harness] ({i}/{len(pdfs)}) running: {pdf.name}")
        try:
            rec = await run_one_paper(graph, pdf)
            cache_path.write_text(
                json.dumps(_serialize_record(rec), indent=2),
                encoding="utf-8",
            )
            records.append(rec)
        except Exception as e:
            log.error(f"[harness] FAILED on {pdf.name}: {e}")

    return records


def _load_record(cache_path: Path) -> dict[str, Any]:
    """Rehydrate a cached per-paper JSON back into the typed shape run_one_paper returns."""
    from src.schemas import A2AMessage, Disagreement

    data = json.loads(cache_path.read_text(encoding="utf-8"))
    return {
        "paper_id": data["paper_id"],
        "paper_path": data["paper_path"],
        "wall_seconds": data["wall_seconds"],
        "final_round": data["final_round"],
        "final_reviews": {rid: Review(**r) for rid, r in data["final_reviews"].items()},
        "disagreements": [Disagreement(**d) for d in data["disagreements"]],
        "a2a_thread": [A2AMessage(**m) for m in data["a2a_thread"]],
        "verdict": Verdict(**data["verdict"]) if data["verdict"] else None,
    }


def load_ground_truth(corpus_dir: Path) -> dict[str, dict]:
    path = corpus_dir / "ground_truth.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))

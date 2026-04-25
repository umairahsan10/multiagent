"""Fetch papers for evaluation from arXiv IDs listed in a config file.

Why arXiv IDs instead of OpenReview scraping: OpenReview's API requires per-venue
traversal and the schema varies between ICLR / NeurIPS / ICML / workshop papers.
For a class project, the reproducible path is to supply a list of arXiv IDs
yourself (pulled from wherever) and then annotate ground truth manually. This
script handles the download half.

Usage:
    1. Create `data/eval_corpus/arxiv_ids.txt` with one arXiv ID per line:
           1706.03762   # Attention Is All You Need
           2005.14165   # GPT-3
           ...
    2. Run:
           python scripts/fetch_openreview.py
       Downloads each PDF to data/eval_corpus/papers/<arxiv_id>.pdf

    3. Annotate ground_truth.json manually (or leave it empty — metrics that
       require it will skip those papers).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import Config  # noqa: E402
from src.logging_setup import get_logger  # noqa: E402

log = get_logger(__name__)

CORPUS_DIR = Config.DATA_DIR / "eval_corpus"
PAPERS_DIR = CORPUS_DIR / "papers"
IDS_FILE = CORPUS_DIR / "arxiv_ids.txt"
GT_FILE = CORPUS_DIR / "ground_truth.json"


def _download_arxiv(arxiv_id: str, out_path: Path) -> bool:
    url = f"https://arxiv.org/pdf/{arxiv_id}"
    try:
        with httpx.Client(follow_redirects=True, timeout=60) as client:
            r = client.get(url)
            r.raise_for_status()
            out_path.write_bytes(r.content)
            return True
    except Exception as e:
        log.error(f"[fetch] {arxiv_id}: {e}")
        return False


def _read_ids() -> list[str]:
    if not IDS_FILE.exists():
        return []
    out: list[str] = []
    for line in IDS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line.split()[0].split("#")[0].strip())
    return out


def main() -> int:
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)

    ids = _read_ids()
    if not ids:
        print(f"No arXiv IDs found. Create {IDS_FILE} with one ID per line.")
        print("Example:")
        print("  1706.03762   # Attention Is All You Need")
        print("  2005.14165   # GPT-3")
        return 1

    log.info(f"[fetch] {len(ids)} IDs to download")
    ok = 0
    for i, aid in enumerate(ids, 1):
        out = PAPERS_DIR / f"{aid}.pdf"
        if out.exists():
            log.info(f"[fetch] ({i}/{len(ids)}) cached: {aid}")
            ok += 1
            continue
        log.info(f"[fetch] ({i}/{len(ids)}) downloading: {aid}")
        if _download_arxiv(aid, out):
            ok += 1
            time.sleep(1.0)  # gentle rate limit for arXiv

    log.info(f"[fetch] {ok}/{len(ids)} papers in {PAPERS_DIR}")
    if not GT_FILE.exists():
        GT_FILE.write_text("{}\n", encoding="utf-8")
        log.info(f"[fetch] created empty {GT_FILE} — annotate with human decisions/concerns for metrics")
    return 0


if __name__ == "__main__":
    sys.exit(main())

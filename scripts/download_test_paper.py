"""Download Attention Is All You Need from arXiv as our default test paper.

    python scripts/download_test_paper.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import Config  # noqa: E402

ARXIV_URL = "https://arxiv.org/pdf/1706.03762"
OUT = Config.PAPER_CACHE_DIR.parent / "papers" / "attention_is_all_you_need.pdf"


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        print(f"Already downloaded: {OUT}")
        return 0
    print(f"Downloading {ARXIV_URL} ...")
    r = httpx.get(ARXIV_URL, follow_redirects=True, timeout=60)
    r.raise_for_status()
    OUT.write_bytes(r.content)
    print(f"Wrote {len(r.content):,} bytes -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""End-to-end smoke test for Phase 1.

Spawns the paper-parser MCP server as a subprocess, calls every tool against the
downloaded test paper, and prints a summary so we can eyeball the parse quality.

    python scripts/test_paper_parser.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.clients.mcp_client import mcp_session, unwrap_tool_result  # noqa: E402
from src.config import Config  # noqa: E402

PAPER = Config.PAPER_CACHE_DIR.parent / "papers" / "attention_is_all_you_need.pdf"


def banner(title: str) -> None:
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


async def main() -> int:
    if not PAPER.exists():
        print(f"Missing test PDF: {PAPER}\nRun: python scripts/download_test_paper.py")
        return 1

    async with mcp_session("paper_parser") as session:
        banner("1) list_tools")
        tools = await session.list_tools()
        for t in tools.tools:
            print(f"  - {t.name}")

        banner("2) parse_pdf")
        result = await session.call_tool("parse_pdf", {"path": str(PAPER)})
        parsed = unwrap_tool_result(result)
        paper_id = parsed["paper_id"]
        print(f"  paper_id : {paper_id}")
        print(f"  title    : {parsed['title'][:120]}")
        print(f"  abstract : {parsed['abstract'][:200]}{'...' if len(parsed['abstract']) > 200 else ''}")
        print(f"  sections : {len(parsed['sections'])} -> {sorted(parsed['sections'].keys())}")
        print(f"  refs     : {len(parsed['references'])}")
        print(f"  figures  : {len(parsed['figure_captions'])}")

        banner("3) get_section('introduction')")
        result = await session.call_tool("get_section", {"paper_id": paper_id, "section": "introduction"})
        section_text = unwrap_tool_result(result)
        snippet = section_text[:400] if isinstance(section_text, str) else str(section_text)[:400]
        print(f"  {snippet}{'...' if isinstance(section_text, str) and len(section_text) > 400 else ''}")

        banner("4) list_references (first 3)")
        result = await session.call_tool("list_references", {"paper_id": paper_id})
        refs = unwrap_tool_result(result)
        for r in refs[:3]:
            print(f"  - [{r.get('year')}] {r.get('title', '')[:120]}")

        banner("5) get_figure_captions (first 3)")
        result = await session.call_tool("get_figure_captions", {"paper_id": paper_id})
        caps = unwrap_tool_result(result)
        for c in caps[:3]:
            print(f"  - {c[:140]}")

    banner("OK")
    print("Phase 1 smoke test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

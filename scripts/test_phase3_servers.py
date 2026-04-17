"""Phase 3 smoke tests: verify each MCP server responds and its tools work.

    python scripts/test_phase3_servers.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.clients.mcp_client import mcp_session, unwrap_tool_result  # noqa: E402


def banner(s: str) -> None:
    print("\n" + "=" * 60 + f"\n  {s}\n" + "=" * 60)


async def test_stats_verifier() -> None:
    banner("stats_verifier")
    async with mcp_session("stats_verifier") as session:
        tools = await session.list_tools()
        print(f"  tools: {[t.name for t in tools.tools]}")

        r = await session.call_tool(
            "run_python_sandbox",
            {"code": "import numpy as np; print(np.mean([1,2,3,4,5]))"},
        )
        res = unwrap_tool_result(r)
        print(f"  sandbox mean test: ok={res['ok']} stdout={res['stdout'].strip()!r}")
        assert res["ok"] and res["stdout"].strip() == "3.0", res

        r = await session.call_tool(
            "check_t_statistic",
            {"t_value": 2.41, "df": 58, "two_tailed": True},
        )
        res = unwrap_tool_result(r)
        print(f"  t-test helper: p={res['p_value']:.4f}")
        assert 0.01 < res["p_value"] < 0.05, res

        r = await session.call_tool(
            "run_python_sandbox",
            {"code": "while True: pass", "timeout": 2},
        )
        res = unwrap_tool_result(r)
        print(f"  timeout test: timed_out={res['timed_out']}")
        assert res["timed_out"], res


async def test_openalex() -> None:
    banner("openalex")
    async with mcp_session("openalex") as session:
        tools = await session.list_tools()
        print(f"  tools: {[t.name for t in tools.tools]}")

        r = await session.call_tool(
            "search_works",
            {"query": "attention is all you need transformer", "k": 3},
        )
        results = unwrap_tool_result(r)
        print(f"  search returned {len(results)} works")
        for w in results[:3]:
            print(f"    - [{w['year']}] {w['title'][:80]} (cites={w['cited_by_count']})")

        r = await session.call_tool(
            "verify_paper_exists",
            {"title": "Attention Is All You Need", "year": 2017},
        )
        ver = unwrap_tool_result(r)
        print(f"  verify 'Attention Is All You Need' 2017: exists={ver['exists']} conf={ver['confidence']}")
        assert ver["exists"], ver

        r = await session.call_tool(
            "verify_paper_exists",
            {"title": "Quantum Kangaroos in Reinforcement Learning", "year": 2099},
        )
        ver = unwrap_tool_result(r)
        print(f"  verify fake paper: exists={ver['exists']} conf={ver['confidence']}")
        assert not ver["exists"], ver


async def test_rag() -> None:
    banner("rag")
    async with mcp_session("rag") as session:
        tools = await session.list_tools()
        print(f"  tools: {[t.name for t in tools.tools]}")

        r = await session.call_tool("corpus_stats", {})
        stats = unwrap_tool_result(r)
        print(f"  initial stats: {stats}")

        r = await session.call_tool(
            "index_paper",
            {
                "paper_id": "smoke_test_aiayn",
                "title": "Attention Is All You Need",
                "abstract": "We propose the Transformer, a new network architecture based solely on attention mechanisms, dispensing with recurrence and convolutions entirely.",
                "body": "The Transformer uses multi-head self-attention to model dependencies. Experiments on WMT 2014 En-De achieve 28.4 BLEU.",
                "year": 2017,
                "source": "smoke_test",
            },
        )
        res = unwrap_tool_result(r)
        print(f"  indexed: {res}")

        r = await session.call_tool(
            "query_corpus",
            {"query": "multi-head self-attention mechanism", "k": 2},
        )
        hits = unwrap_tool_result(r)
        print(f"  query returned {len(hits)} hits")
        for h in hits:
            print(f"    - dist={h['distance']:.3f} title={h['title']}")
        assert len(hits) >= 1 and hits[0]["paper_id"] == "smoke_test_aiayn"


async def main() -> int:
    await test_stats_verifier()
    await test_openalex()
    await test_rag()
    banner("ALL PHASE 3 SERVERS OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

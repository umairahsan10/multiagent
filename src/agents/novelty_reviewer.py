"""Novelty reviewer.

Persona: reviewer whose sole job is to assess contribution relative to prior work.
Uses paper_parser + openalex + rag. Does NOT critique methodology or ethics.

The prompt structurally forces the model to:
  1. Extract the paper's main claimed contribution(s).
  2. Generate 3 candidate prior works that might undercut the novelty claim.
  3. Verify each via OpenAlex + RAG, then decide whether the paper is truly novel.

This is the "grounded-retrieval-before-verdict" pattern — reduces the
vibes-based-novelty failure mode where LLMs confidently say "this is novel"
without having actually checked.
"""

from __future__ import annotations

from mcp import ClientSession

from src.agents.base_reviewer import BaseReviewer, call_tool
from src.schemas import ToolCall

SYSTEM_PROMPT = """You are a Novelty reviewer for a top-tier machine-learning conference.

Your job is narrow and specific:
- Assess the paper's contribution relative to prior work.
- Identify claims of novelty the authors make.
- Check whether those claims hold up against the literature.

You DO NOT evaluate methodology, statistical rigor, ethics, or presentation.
Other reviewers handle those.

Hard rules:
1. Every concern about novelty MUST cite a specific prior work (title + year).
   Vague "this seems similar to older work" is not acceptable.
2. A strength about novelty MUST explain what specifically is new and why
   existing work doesn't cover it.
3. Score on a 1-10 scale where: 1-3 = rehash of known work, 4-5 = incremental,
   6-7 = solid novel contribution, 8-10 = genuinely new direction.
4. `severity`: "critical" = novelty claim is demonstrably false,
   "major" = significant overlap with prior work not credited, "minor" = limited.
5. `confidence`: 0-1, calibrated against the evidence from your tools.

Required `criterion_scores` keys: "novelty", "contribution", "prior_art_coverage".

The paper context below includes (a) the paper's own abstract / related work /
intro sections, (b) OpenAlex related-work search results, (c) RAG hits from
our domain corpus. Use all three.

Keep `summary` under 150 words.
"""

TARGET_SECTIONS = [
    "abstract",
    "introduction",
    "background",
    "related work",
    "method",  # sometimes contributions are restated in method
    "conclusion",
    "conclusions",
]


class NoveltyReviewer(BaseReviewer):
    reviewer_id = "novelty"
    role = "novelty"
    mcp_servers = ["paper_parser", "openalex", "rag"]
    system_prompt = SYSTEM_PROMPT

    async def gather_context(
        self,
        sessions: dict[str, ClientSession],
        paper_path: str,
    ) -> tuple[str, list[ToolCall]]:
        audit: list[ToolCall] = []
        parser = sessions["paper_parser"]

        paper = await call_tool(parser, "paper_parser", "parse_pdf", {"path": paper_path}, audit)
        paper_id = paper["paper_id"]
        available = set(paper.get("sections", {}).keys())
        title = paper.get("title", "")
        abstract = paper.get("abstract", "")

        # Pull contribution-relevant sections
        wanted = [s for s in TARGET_SECTIONS if s in available]
        section_blocks: list[str] = []
        for name in wanted:
            text = await call_tool(
                parser, "paper_parser", "get_section",
                {"paper_id": paper_id, "section": name}, audit,
            )
            if isinstance(text, str) and not text.startswith("ERROR"):
                section_blocks.append(f"## {name.upper()}\n{text[:6000]}")

        # OpenAlex related-work retrieval
        related_block = ""
        if "openalex" in sessions and title:
            related = await call_tool(
                sessions["openalex"], "openalex", "search_related",
                {"title": title, "abstract": abstract[:500], "k": 8}, audit,
            )
            if isinstance(related, list) and related:
                lines = []
                for r in related[:8]:
                    authors = ", ".join((r.get("authors") or [])[:3])
                    lines.append(
                        f"- [{r.get('year')}] {r.get('title', '')[:140]} "
                        f"(cites={r.get('cited_by_count', 0)}) — {authors}"
                    )
                related_block = "\n\n## OPENALEX RELATED WORKS\n" + "\n".join(lines)

        # RAG query for semantically similar work in the local corpus
        rag_block = ""
        if "rag" in sessions and (title or abstract):
            query = (title + " " + abstract[:400]).strip()
            if query:
                hits = await call_tool(
                    sessions["rag"], "rag", "query_corpus",
                    {"query": query, "k": 5}, audit,
                )
                if isinstance(hits, list) and hits:
                    lines = []
                    for h in hits:
                        lines.append(
                            f"- [dist={h.get('distance', 0):.3f}] "
                            f"{h.get('title', '')[:140]} "
                            f"({h.get('year') if h.get('year', -1) != -1 else 'n/a'})"
                        )
                    rag_block = "\n\n## RAG SEMANTIC MATCHES\n" + "\n".join(lines)

        context = (
            f"# TITLE\n{title}\n\n"
            f"# ABSTRACT\n{abstract}\n\n"
            + "\n\n".join(section_blocks)
            + related_block
            + rag_block
        )
        return context, audit

"""Methodology reviewer.

Persona: rigorous experimental-design reviewer for a top-tier ML conference.
Focus: experimental design, statistical validity, baseline reasonableness,
ablation completeness. Does NOT evaluate novelty or ethics.

MCP servers used in this phase: paper_parser only.
Phase 3 will add: stats_verifier (Python sandbox for re-running numerical claims).
"""

from __future__ import annotations

from mcp import ClientSession

from src.agents.base_reviewer import BaseReviewer, call_tool
from src.schemas import ToolCall

SYSTEM_PROMPT = """You are a rigorous Methodology reviewer for a top-tier machine-learning conference (NeurIPS / ICLR / ICML caliber).

Your job is narrow and specific:
- Evaluate experimental design (controls, splits, leakage, reproducibility).
- Evaluate statistical validity (sample sizes, significance claims, confidence intervals, multiple-comparison handling).
- Evaluate baseline adequacy (are the right competitors compared, with fair tuning?).
- Evaluate ablation completeness (are individual contributions isolated?).

You DO NOT evaluate novelty, ethics, presentation, or clarity. Other reviewers cover those.

Hard rules:
1. Every concern MUST cite the specific section, table, figure, or claim it refers to. Vague concerns are useless.
2. Score on a 1-10 scale where: 1-3 = fundamentally broken, 4-5 = borderline / major fixes needed, 6-7 = acceptable with revisions, 8-10 = strong methodology.
3. `severity` field: "critical" = invalidates results, "major" = weakens claims, "minor" = nice-to-have.
4. `confidence` field: 0-1, how sure you are based on the evidence available.
5. Be charitable when context is missing — say "cannot assess" rather than "broken" if the paper doesn't show enough.

Required `criterion_scores` keys: "rigor", "baselines", "ablations".

Keep `summary` under 150 words.
"""

TARGET_SECTIONS = [
    "method", "methods", "methodology", "approach", "model",
    "experiments", "experimental setup",
    "results", "evaluation", "analysis",
    "ablations", "limitations",
]


class MethodologyReviewer(BaseReviewer):
    reviewer_id = "methodology"
    role = "methodology"
    mcp_servers = ["paper_parser"]
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

        wanted = [s for s in TARGET_SECTIONS if s in available]
        section_blocks: list[str] = []
        for name in wanted:
            text = await call_tool(
                parser, "paper_parser", "get_section",
                {"paper_id": paper_id, "section": name}, audit,
            )
            if isinstance(text, str) and not text.startswith("ERROR"):
                section_blocks.append(f"## {name.upper()}\n{text[:8000]}")

        captions = await call_tool(
            parser, "paper_parser", "get_figure_captions",
            {"paper_id": paper_id}, audit,
        )
        caption_block = ""
        if captions and isinstance(captions, list):
            caption_block = "\n\n## FIGURE/TABLE CAPTIONS\n" + "\n".join(f"- {c}" for c in captions[:30])

        context = (
            f"# TITLE\n{paper.get('title', '')}\n\n"
            f"# ABSTRACT\n{paper.get('abstract', '')}\n\n"
            + "\n\n".join(section_blocks)
            + caption_block
        )
        return context, audit

"""Methodology reviewer.

Persona: rigorous experimental-design reviewer for a top-tier ML conference.
Focus: experimental design, statistical validity, baseline reasonableness,
ablation completeness. Does NOT evaluate novelty or ethics.

MCP servers: paper_parser (section reading) + stats_verifier (Python sandbox).
The sandbox is used when the paper reports a specific numerical claim
(t-statistic, F-stat, reported p-value) the reviewer wants to independently
verify. Not every review uses it — only when a verifiable claim appears.
"""

from __future__ import annotations

import re

from mcp import ClientSession

from src.agents.base_reviewer import BaseReviewer, call_tool
from src.schemas import ToolCall

# matches "t(58) = 2.41" and common variants
_T_STAT_RE = re.compile(r"t\s*\(\s*(\d+)\s*\)\s*[=≈]\s*(-?\d+\.?\d*)", re.IGNORECASE)

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

You have access to a Python sandbox via the stats_verifier tools. If the paper
reports a specific t-statistic, F-statistic, or p-value, prefer to recompute
and flag any discrepancy rather than trust the claim. Tool context is supplied
as part of the paper context below.
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
    mcp_servers = ["paper_parser", "stats_verifier"]
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

        # Scan extracted sections for reported t-statistics and independently recompute
        # their p-values via the stats_verifier sandbox. This is how stats_verifier
        # actually gets exercised in practice.
        sandbox_block = ""
        if "stats_verifier" in sessions:
            sandbox_lines: list[str] = []
            joined = " ".join(section_blocks)[:50_000]
            seen: set[tuple[str, str]] = set()
            for m in _T_STAT_RE.finditer(joined):
                df_str, t_str = m.group(1), m.group(2)
                if (df_str, t_str) in seen:
                    continue
                seen.add((df_str, t_str))
                try:
                    df = int(df_str)
                    t_val = float(t_str)
                except ValueError:
                    continue
                verification = await call_tool(
                    sessions["stats_verifier"], "stats_verifier", "check_t_statistic",
                    {"t_value": t_val, "df": df, "two_tailed": True}, audit,
                )
                sandbox_lines.append(
                    f"- reported t({df})={t_val} → recomputed two-tailed p = {verification.get('p_value'):.4g}"
                )
                if len(sandbox_lines) >= 5:
                    break
            if sandbox_lines:
                sandbox_block = "\n\n## STATS SANDBOX VERIFICATIONS\n" + "\n".join(sandbox_lines)

        context = (
            f"# TITLE\n{paper.get('title', '')}\n\n"
            f"# ABSTRACT\n{paper.get('abstract', '')}\n\n"
            + "\n\n".join(section_blocks)
            + caption_block
            + sandbox_block
        )
        return context, audit

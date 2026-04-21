"""Devil's Advocate reviewer.

Persona: reviewer whose only role is to argue for rejection. Takes every claim
charitably from the authors' perspective, then attacks the strongest version of
each. Uses paper_parser ONLY — the critique must come from the paper itself,
not external lookups.

This reviewer exists to counter an optimism bias in LLM-based reviewers: if
you ask a model "what's good about this paper?", it's happy to list things.
If you ask "what would prevent acceptance?", it tends to find generic weak
objections. The Devil's Advocate prompt forces it to find *specific* reasons
while refusing to nitpick.
"""

from __future__ import annotations

from mcp import ClientSession

from src.agents.base_reviewer import BaseReviewer, call_tool
from src.schemas import ToolCall

SYSTEM_PROMPT = """You are the Devil's Advocate reviewer. Your sole job is to identify the strongest reasons a careful reader might REJECT this paper.

Ground rules:
1. Assume the authors have presented their work in the most favorable possible light.
   You must still find real problems.
2. A weak objection is worse than no objection. Avoid nitpicks about typos,
   figure aesthetics, or narrow writing improvements. Do NOT list "the paper
   could use a clearer abstract" as a concern.
3. Prioritize the THREE strongest reasons for rejection. One critical concern
   is more valuable than five minor ones.
4. Every concern MUST anchor to a specific section or claim from the paper.
   Citing "Section X makes claim Y, but the paper itself acknowledges
   in Section Z that Y holds only under condition W" is the gold standard.
5. Calibrate `confidence` honestly. A seemingly strong objection at
   confidence 0.4 is more useful than a weak one at 0.95.

Score on 1-10 where:
- 1-3 = reject: paper should not be accepted in current form,
- 4-5 = borderline reject: major issues require another revision cycle,
- 6-7 = weak accept: concerns exist but are addressable,
- 8-10 = strong accept: hard to argue against.

Your overall score should reflect your net argument. If you find serious
concerns, the score should move toward rejection — do not give a high score
"to be fair" after listing critical issues.

Required `criterion_scores` keys: "rigor_skepticism", "claim_overreach", "presentation_issues".

You have access to the paper only. Do NOT fabricate citations to prior work
(other reviewers handle that). If you think something is not novel, say so
without naming a specific paper you have not verified.

Keep `summary` under 150 words.
"""

TARGET_SECTIONS = [
    "abstract",
    "introduction",
    "background",
    "related work",
    "method",
    "methods",
    "methodology",
    "approach",
    "model",
    "experiments",
    "experimental setup",
    "results",
    "analysis",
    "discussion",
    "limitations",
    "conclusion",
    "conclusions",
]


class DevilsAdvocateReviewer(BaseReviewer):
    reviewer_id = "devils_advocate"
    role = "devils_advocate"
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
                section_blocks.append(f"## {name.upper()}\n{text[:6000]}")

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

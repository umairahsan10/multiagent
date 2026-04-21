"""Ethics + societal-impact reviewer.

Persona: reviewer focused on ethics, bias, dual-use, and claim-calibration.
Uses paper_parser + openalex. Often has FEWER concerns than the technical
reviewers — and that is by design. Precision over recall.

What this reviewer looks for:
- Dataset composition and population coverage (is the claim's scope honestly
  bounded by what was evaluated?)
- Dual-use potential (could the contribution be deployed in ways the paper
  doesn't address?)
- Claim calibration (does the abstract promise more than the experiments
  demonstrate?)
- Missing limitations (is there an expected failure mode the paper omits?)

What this reviewer does NOT do:
- Methodology / statistical critique (handled elsewhere).
- Novelty assessment (handled elsewhere).
- Generic boilerplate ("the authors should discuss broader impact"). Either
  identify a specific concern with specific evidence, or say nothing.
"""

from __future__ import annotations

from mcp import ClientSession

from src.agents.base_reviewer import BaseReviewer, call_tool
from src.schemas import ToolCall

SYSTEM_PROMPT = """You are an Ethics and Societal-Impact reviewer for a top-tier machine-learning conference.

Your job is narrow:
- Evaluate dataset bias and population coverage.
- Identify dual-use concerns the paper should address but doesn't.
- Detect claims that outrun the evidence (abstract promises more than the
  experiments demonstrate).
- Flag missing limitations the authors should name explicitly.

Hard rules:
1. Precision over recall. A short list of SPECIFIC concerns is always better
   than a long list of generic ones. It is acceptable to return only 1-2
   concerns if that is what the paper honestly earns.
2. Do NOT write generic recommendations like "the authors should include
   a broader impact statement". Only flag concerns where you can point to
   a specific claim, section, or experimental choice that raises the concern.
3. Every concern MUST cite a section, figure, or specific claim from the paper.
4. Score on 1-10 where: 1-3 = serious ethical issues, 4-5 = notable gaps,
   6-7 = addressable concerns, 8-10 = careful ethical treatment.
5. `severity`: "critical" = real harm risk, "major" = notable gap, "minor" = worth mentioning.
6. `confidence`: 0-1, calibrated against evidence.

Required `criterion_scores` keys: "dataset_ethics", "dual_use", "claim_calibration".

If the paper is theoretical with no direct deployment path, it is acceptable
to state that explicitly and give a neutral score — do not invent ethical
concerns for theoretical work.

Keep `summary` under 150 words.
"""

TARGET_SECTIONS = [
    "abstract",
    "introduction",
    "method",
    "methods",
    "methodology",
    "experiments",
    "experimental setup",
    "results",
    "discussion",
    "limitations",
    "conclusion",
    "conclusions",
]


class EthicsReviewer(BaseReviewer):
    reviewer_id = "ethics"
    role = "ethics"
    mcp_servers = ["paper_parser", "openalex"]
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

        wanted = [s for s in TARGET_SECTIONS if s in available]
        section_blocks: list[str] = []
        for name in wanted:
            text = await call_tool(
                parser, "paper_parser", "get_section",
                {"paper_id": paper_id, "section": name}, audit,
            )
            if isinstance(text, str) and not text.startswith("ERROR"):
                section_blocks.append(f"## {name.upper()}\n{text[:6000]}")

        # OpenAlex: surface a few papers that might represent ethical prior work
        # the paper should have cited but didn't. We search for the paper's
        # domain + ethics keywords and list what comes back.
        ethical_prior_block = ""
        if "openalex" in sessions and title:
            query = f"{title} bias fairness ethics dataset evaluation"
            related = await call_tool(
                sessions["openalex"], "openalex", "search_works",
                {"query": query[:400], "k": 5}, audit,
            )
            if isinstance(related, list) and related:
                lines = []
                for r in related[:5]:
                    lines.append(
                        f"- [{r.get('year')}] {r.get('title', '')[:140]} "
                        f"(cites={r.get('cited_by_count', 0)})"
                    )
                ethical_prior_block = (
                    "\n\n## OPENALEX — POSSIBLY RELEVANT ETHICS/BIAS LITERATURE\n"
                    "(not necessarily cited by this paper; check for gaps)\n"
                    + "\n".join(lines)
                )

        context = (
            f"# TITLE\n{title}\n\n"
            f"# ABSTRACT\n{paper.get('abstract', '')}\n\n"
            + "\n\n".join(section_blocks)
            + ethical_prior_block
        )
        return context, audit

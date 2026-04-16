"""Paper-parser MCP server.

Run as a standalone process (stdio transport):
    python -m src.mcp_servers.paper_parser

Exposes four tools to any MCP client:
- parse_pdf(path)            -> full PaperStructure
- get_section(paper_id, name) -> section text
- list_references(paper_id)   -> references
- get_figure_captions(paper_id) -> captions
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from src.config import Config
from src.utils.pdf_extract import parse_pdf as _parse_pdf

mcp = FastMCP("paper-parser")


def _load_cached(paper_id: str) -> dict | None:
    cache = Config.PAPER_CACHE_DIR / f"{paper_id}.json"
    if not cache.exists():
        return None
    return json.loads(cache.read_text(encoding="utf-8"))


@mcp.tool()
def parse_pdf(path: str) -> dict:
    """Parse a PDF and return the structured paper.

    Returns a JSON object with: paper_id, title, abstract, sections, references,
    figure_captions. The result is cached by content hash; subsequent calls on the
    same file are near-instant.
    """
    return _parse_pdf(path)


@mcp.tool()
def get_section(paper_id: str, section: str) -> str:
    """Return the raw text of a named section (e.g. 'introduction', 'method').

    `paper_id` is the value returned in `parse_pdf`'s output. `section` is matched
    case-insensitively against canonical section names.
    """
    data = _load_cached(paper_id)
    if data is None:
        return f"ERROR: paper_id {paper_id} not found in cache. Call parse_pdf first."
    sections = data.get("sections", {})
    key = section.lower().strip()
    if key in sections:
        return sections[key]
    for k, v in sections.items():
        if key in k:
            return v
    return f"ERROR: section '{section}' not found. Available: {sorted(sections.keys())}"


@mcp.tool()
def list_references(paper_id: str) -> list[dict]:
    """Return the parsed reference list for a paper."""
    data = _load_cached(paper_id)
    if data is None:
        return [{"error": f"paper_id {paper_id} not found in cache"}]
    return data.get("references", [])


@mcp.tool()
def get_figure_captions(paper_id: str) -> list[str]:
    """Return all figure and table captions extracted from the paper."""
    data = _load_cached(paper_id)
    if data is None:
        return [f"ERROR: paper_id {paper_id} not found in cache"]
    return data.get("figure_captions", [])


@mcp.tool()
def list_cached_papers() -> list[dict]:
    """Return paper_id and title for every paper currently in the cache."""
    out: list[dict] = []
    for f in Path(Config.PAPER_CACHE_DIR).glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append({"paper_id": d.get("paper_id"), "title": d.get("title", "")[:200]})
        except Exception:
            continue
    return out


if __name__ == "__main__":
    mcp.run()

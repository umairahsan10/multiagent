"""Pure PDF extraction logic. Kept separate from the MCP server so it's testable
without spawning a subprocess.

Section detection in academic PDFs is genuinely hard: papers use different heading
conventions (numbered "3. Method" vs unnumbered "Method"), single vs two-column
layouts, and varied font hierarchies. The heuristic below is intentionally simple
and gets ~80% of conference papers right. Replace with GROBID later if needed.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import fitz  # PyMuPDF

from src.config import Config

CANONICAL_SECTIONS = [
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
    "evaluation",
    "results",
    "analysis",
    "discussion",
    "limitations",
    "conclusion",
    "conclusions",
    "references",
    "acknowledgments",
    "acknowledgements",
]

HEADING_RE = re.compile(
    r"^\s*(?:(\d+(?:\.\d+)*)\s*[\.\)]?\s+)?([A-Z][A-Za-z &/\-]{2,60})\s*$"
)


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _normalize_heading(text: str) -> str | None:
    """Return canonical lowercase heading name if `text` looks like a known section heading."""
    m = HEADING_RE.match(text.strip())
    if not m:
        return None
    label = m.group(2).strip().lower()
    for canon in CANONICAL_SECTIONS:
        if label == canon or label.startswith(canon + " "):
            return canon
    return None


def _extract_lines(doc: fitz.Document) -> list[tuple[str, float]]:
    """Return (line_text, max_font_size) tuples in reading order."""
    out: list[tuple[str, float]] = []
    for page in doc:
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                text = "".join(s.get("text", "") for s in spans).strip()
                if not text:
                    continue
                size = max((s.get("size", 0.0) for s in spans), default=0.0)
                out.append((text, size))
    return out


_ARXIV_MARGIN_RE = re.compile(r"^arxiv:\s*\d+\.\d+", re.I)


def _extract_title(lines: list[tuple[str, float]]) -> str:
    """Heuristic: title is the largest-font text near the top, ignoring arXiv margin headers."""
    if not lines:
        return ""
    head = [(t, s) for t, s in lines[:80] if not _ARXIV_MARGIN_RE.match(t.strip())]
    if not head:
        return ""
    max_size = max(s for _, s in head)
    candidates = [t for t, s in head if s >= max_size - 0.5 and len(t.split()) >= 2]
    return " ".join(candidates[:3]).strip() if candidates else head[0][0]


def _extract_sections(lines: list[tuple[str, float]]) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = "_preamble"
    sections[current] = []
    for text, _ in lines:
        canon = _normalize_heading(text)
        if canon:
            current = canon
            sections.setdefault(current, [])
            continue
        sections[current].append(text)
    return {k: " ".join(v).strip() for k, v in sections.items() if v}


def _extract_abstract(sections: dict[str, str], lines: list[tuple[str, float]]) -> str:
    if "abstract" in sections:
        return sections["abstract"][:5000]
    joined = " ".join(t for t, _ in lines[:200])
    m = re.search(r"abstract\s*[—\-:]?\s*(.{200,2000}?)(?:\n\s*\n|introduction|1\s+introduction)", joined, re.I | re.S)
    return m.group(1).strip() if m else ""


def _extract_references(sections: dict[str, str]) -> list[dict]:
    raw = sections.get("references", "")
    if not raw:
        return []
    items = re.split(r"\n?\[\d+\]\s+|\n(?=\[\d+\])", raw)
    refs: list[dict] = []
    for item in items:
        item = item.strip()
        if len(item) < 20:
            continue
        year_match = re.search(r"\b(19|20)\d{2}\b", item)
        year = int(year_match.group(0)) if year_match else None
        refs.append({"title": item[:300], "authors": [], "year": year, "doi": None})
    return refs[:200]


def _extract_figure_captions(doc: fitz.Document) -> list[str]:
    captions: list[str] = []
    for page in doc:
        text = page.get_text("text")
        for m in re.finditer(r"(Figure|Fig\.|Table)\s+\d+[:\.\-]\s*(.{10,300}?)(?:\n\n|\.\s+[A-Z])", text, re.S):
            captions.append((m.group(1) + " " + m.group(2)).strip())
    return captions[:50]


def parse_pdf(path: str | Path, use_cache: bool = True) -> dict:
    """Parse a PDF and return a PaperStructure dict (JSON-serializable).

    Cached by content hash in PAPER_CACHE_DIR so repeated calls are free.
    """
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"PDF not found: {p}")

    paper_id = file_hash(p)
    cache_path = Config.PAPER_CACHE_DIR / f"{paper_id}.json"
    if use_cache and cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    with fitz.open(str(p)) as doc:
        lines = _extract_lines(doc)
        title = _extract_title(lines)
        sections = _extract_sections(lines)
        abstract = _extract_abstract(sections, lines)
        references = _extract_references(sections)
        figure_captions = _extract_figure_captions(doc)

    sections.pop("_preamble", None)
    sections.pop("references", None)

    result = {
        "paper_id": paper_id,
        "title": title,
        "abstract": abstract,
        "sections": sections,
        "references": references,
        "figure_captions": figure_captions,
    }
    cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result

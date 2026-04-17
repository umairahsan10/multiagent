"""OpenAlex MCP server — scholarly metadata, citation graph, related works.

OpenAlex: https://docs.openalex.org/
- No API key required. Passing `mailto=<email>` in query params joins the
  "polite pool" (faster routing + 100k req/day).
- `abstract_inverted_index` is how abstracts are delivered (Elsevier-style
  licensing workaround). We reconstruct back to plain text.

Tools:
- search_works(query, k)              — free-text search across title+abstract
- search_related(title, abstract, k)  — find related works for a specific paper
- get_references(openalex_id)         — papers this one cites
- get_citations(openalex_id)          — papers that cite this one
- get_work(openalex_id)               — full record incl. abstract
- verify_paper_exists(title, year)    — for the hallucination auditor
"""

from __future__ import annotations

from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from src.config import Config

mcp = FastMCP("openalex")

BASE = Config.OPENALEX_BASE_URL


def _params(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    p: dict[str, Any] = dict(extra or {})
    if Config.OPENALEX_EMAIL:
        p["mailto"] = Config.OPENALEX_EMAIL
    return p


def _reconstruct_abstract(inverted: dict[str, list[int]] | None) -> str:
    if not inverted:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inverted.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(word for _, word in positions)


def _safe_get(d: dict | None, *keys: str):
    cur: object = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def _shape_work(w: dict) -> dict:
    return {
        "id": w.get("id", ""),
        "title": w.get("title") or "",
        "year": w.get("publication_year"),
        "authors": [
            _safe_get(a, "author", "display_name")
            for a in (w.get("authorships") or [])
            if _safe_get(a, "author", "display_name")
        ],
        "venue": _safe_get(w, "primary_location", "source", "display_name"),
        "doi": w.get("doi"),
        "cited_by_count": w.get("cited_by_count", 0),
        "abstract": _reconstruct_abstract(w.get("abstract_inverted_index"))[:2000],
    }


def _get(path: str, params: dict[str, Any] | None = None) -> dict:
    url = f"{BASE}{path}"
    with httpx.Client(timeout=30) as client:
        r = client.get(url, params=_params(params))
        r.raise_for_status()
        return r.json()


@mcp.tool()
def search_works(query: str, k: int = 10) -> list[dict]:
    """Free-text search across OpenAlex. Returns up to k works sorted by relevance."""
    data = _get("/works", {"search": query, "per_page": min(k, 25)})
    return [_shape_work(w) for w in data.get("results", [])[:k]]


@mcp.tool()
def search_related(title: str, abstract: str = "", k: int = 10) -> list[dict]:
    """Find works related to a given title + abstract.

    Uses OpenAlex full-text search on the concatenation. For a published paper
    you already have in OpenAlex, `get_work` followed by inspecting
    `related_works` is more precise — but that requires knowing the ID.
    """
    query = (title + " " + (abstract or "")).strip()[:400]
    return search_works(query, k)


@mcp.tool()
def get_work(openalex_id: str) -> dict:
    """Fetch a single work by OpenAlex ID (`W12345...`) or DOI (`10.xxxx/...`).

    Accepts bare IDs, full OpenAlex URLs, or DOIs.
    """
    ident = openalex_id.strip()
    if ident.startswith("https://openalex.org/"):
        ident = ident.rsplit("/", 1)[-1]
    elif ident.startswith("10."):
        ident = f"doi:{ident}"
    data = _get(f"/works/{ident}")
    return _shape_work(data)


@mcp.tool()
def get_references(openalex_id: str, k: int = 50) -> list[dict]:
    """Return papers cited by this work (its reference list)."""
    work = _get(f"/works/{openalex_id.rsplit('/', 1)[-1]}")
    ref_ids = work.get("referenced_works", [])[:k]
    if not ref_ids:
        return []
    filter_ids = "|".join(r.rsplit("/", 1)[-1] for r in ref_ids)
    data = _get("/works", {"filter": f"openalex:{filter_ids}", "per_page": min(len(ref_ids), 25)})
    return [_shape_work(w) for w in data.get("results", [])]


@mcp.tool()
def get_citations(openalex_id: str, k: int = 25) -> list[dict]:
    """Return papers that cite this work."""
    ident = openalex_id.rsplit("/", 1)[-1]
    data = _get("/works", {"filter": f"cites:{ident}", "per_page": min(k, 25)})
    return [_shape_work(w) for w in data.get("results", [])[:k]]


@mcp.tool()
def verify_paper_exists(title: str, year: int | None = None, authors: list[str] | None = None) -> dict:
    """Check whether a paper with the given title exists in OpenAlex.

    Used by the hallucination auditor to verify citations a reviewer mentioned.
    Returns best match (if any) with a similarity score.
    """
    results = search_works(title, k=5)
    if not results:
        return {"exists": False, "match": None, "confidence": 0.0}

    def norm(s: str) -> str:
        return "".join(c for c in s.lower() if c.isalnum() or c.isspace()).strip()

    target = norm(title)
    best = None
    best_score = 0.0
    for r in results:
        cand = norm(r.get("title", ""))
        if not cand:
            continue
        t_words = set(target.split())
        c_words = set(cand.split())
        if not t_words:
            continue
        overlap = len(t_words & c_words) / len(t_words | c_words)
        if year and r.get("year") and abs(r["year"] - year) <= 1:
            overlap += 0.1
        if overlap > best_score:
            best_score = overlap
            best = r

    exists = best_score >= 0.6
    return {
        "exists": exists,
        "match": best if exists else None,
        "confidence": round(min(best_score, 1.0), 3),
    }


if __name__ == "__main__":
    mcp.run()

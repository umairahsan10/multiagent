"""RAG MCP server.

ChromaDB persistent collection indexed over a user-supplied domain corpus. The
Novelty reviewer queries this to surface papers that Semantic Scholar / OpenAlex
might have missed in their citation graph.

Strategy:
- Persistent Chroma client in CHROMA_DIR.
- Single collection: "papers".
- Embedding: sentence-transformers/all-MiniLM-L6-v2 (384-dim, CPU-friendly).
- Each document = one paper's (title + abstract + concatenated section leads).
  Chunked to ~1000-char pieces with 150-char overlap.

Tools:
- query_corpus(query, k, filters)       — semantic search
- index_paper(paper_id, title, ...)     — add/update a paper's chunks
- corpus_stats()                        — how many docs/chunks indexed
- clear_corpus()                        — wipe the collection (dev only)
"""

from __future__ import annotations

import hashlib
from typing import Any

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from mcp.server.fastmcp import FastMCP

from src.config import Config

mcp = FastMCP("rag")

COLLECTION_NAME = "papers"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150

# Eager init: ChromaDB + sentence-transformers model load is ~5-50s.
# Doing it lazily inside an async tool handler blocks the asyncio event loop
# past the MCP request timeout. Initialize at module import (synchronously)
# before mcp.run() starts the loop.
_client = chromadb.PersistentClient(path=str(Config.CHROMA_DIR))
_embed_fn = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
_collection = _client.get_or_create_collection(
    name=COLLECTION_NAME,
    embedding_function=_embed_fn,
    metadata={"hnsw:space": "cosine"},
)


def _get_collection():
    return _collection


def _chunk(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = text.strip()
    if not text:
        return []
    out: list[str] = []
    i = 0
    while i < len(text):
        out.append(text[i : i + size])
        if i + size >= len(text):
            break
        i += size - overlap
    return out


@mcp.tool()
def query_corpus(query: str, k: int = 5, year_min: int | None = None) -> list[dict]:
    """Semantic search the domain corpus.

    Args:
        query: natural-language query (e.g. "efficient self-attention variants")
        k: max results
        year_min: optional lower bound on publication year (inclusive)

    Returns list of {paper_id, title, year, chunk_text, distance}.
    """
    col = _get_collection()
    where = {"year": {"$gte": year_min}} if year_min is not None else None
    res = col.query(
        query_texts=[query],
        n_results=k,
        where=where,
    )
    if not res.get("ids") or not res["ids"][0]:
        return []

    out: list[dict] = []
    for i, chunk_id in enumerate(res["ids"][0]):
        meta = res["metadatas"][0][i] if res.get("metadatas") else {}
        out.append({
            "chunk_id": chunk_id,
            "paper_id": meta.get("paper_id"),
            "title": meta.get("title"),
            "year": meta.get("year"),
            "chunk_text": (res["documents"][0][i] or "")[:1200],
            "distance": float(res["distances"][0][i]) if res.get("distances") else None,
        })
    return out


@mcp.tool()
def index_paper(
    paper_id: str,
    title: str,
    abstract: str = "",
    body: str = "",
    year: int | None = None,
    source: str = "",
) -> dict:
    """Add (or overwrite) a single paper's chunks in the corpus.

    `body` is typically the concatenation of key sections (intro + method).
    Pass a short `source` tag (e.g. "arxiv", "openreview") if useful for filters.
    """
    col = _get_collection()
    full = f"{title}\n\n{abstract}\n\n{body}".strip()
    chunks = _chunk(full)
    if not chunks:
        return {"indexed": 0, "paper_id": paper_id}

    ids = [f"{paper_id}::{i}" for i in range(len(chunks))]
    existing = col.get(ids=ids)
    if existing.get("ids"):
        col.delete(ids=existing["ids"])

    metadatas: list[dict] = [
        {
            "paper_id": paper_id,
            "title": title[:300],
            "year": year if year is not None else -1,
            "source": source,
            "chunk_idx": i,
        }
        for i in range(len(chunks))
    ]
    col.add(ids=ids, documents=chunks, metadatas=metadatas)
    return {"indexed": len(chunks), "paper_id": paper_id}


@mcp.tool()
def corpus_stats() -> dict:
    """Return size and a sample of indexed paper IDs."""
    col = _get_collection()
    count = col.count()
    sample_ids: list[str] = []
    if count > 0:
        peek = col.peek(limit=min(20, count))
        seen: set[str] = set()
        for meta in peek.get("metadatas") or []:
            pid = meta.get("paper_id")
            if pid and pid not in seen:
                seen.add(pid)
                sample_ids.append(pid)
    return {"chunks": count, "sample_paper_ids": sample_ids[:10]}


@mcp.tool()
def clear_corpus(confirm: str = "") -> dict:
    """Wipe the collection. Pass confirm="yes" to actually run — dev use only."""
    if confirm != "yes":
        return {"cleared": False, "reason": "pass confirm='yes' to actually clear"}
    global _client, _collection
    col = _get_collection()
    ids = col.get().get("ids", [])
    if ids:
        col.delete(ids=ids)
    return {"cleared": True, "removed_chunks": len(ids)}


def _deterministic_id(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


if __name__ == "__main__":
    mcp.run()

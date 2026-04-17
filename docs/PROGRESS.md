# Implementation progress

Tracks what's been built, what each file does, and how to verify each phase.
The full design lives in [readme.md](../readme.md); this file is the build log.

---

## Phase 0 — Project scaffolding + env setup

**Goal:** repository skeleton, dependency manifest, env wiring, and a script that
verifies all three LLM providers respond before any agent code is written.

### Files created

| File | Purpose |
|---|---|
| [requirements.txt](../requirements.txt) | Pinned deps: LangGraph, MCP SDK, PyMuPDF, ChromaDB, sentence-transformers, Streamlit, provider SDKs (Google, Groq, OpenAI-compatible for OpenRouter). |
| [.env.example](../.env.example) | Template for `.env`. Lists every key/value the code reads, with comments on where to obtain each. |
| [.gitignore](../.gitignore) | Ignores `.env`, `.venv/`, caches, ChromaDB store, parsed-paper cache. |
| [src/config.py](../src/config.py) | Single source of truth for env vars, model IDs, paths, debate parameters. Auto-creates data dirs on import. |
| [src/schemas.py](../src/schemas.py) | Pydantic models that every agent shares: `Review`, `Concern`, `A2AMessage`, `Verdict`, `PaperStructure`, `ContestedClaim`. Adding a new reviewer is a prompt change, not a schema change. |
| [src/logging_setup.py](../src/logging_setup.py) | Rich-colored logger factory. |
| [scripts/verify_env.py](../scripts/verify_env.py) | Loads `.env`, checks key presence, then pings each provider with a 1-token call. Exits non-zero if any required provider fails. |

### Decisions locked in this phase

- **Provider mix (heterogeneous panel):**
  - Google **Gemini 2.5 Flash** — for 2 reviewers (Methodology, Ethics).
  - Groq **Llama 3.3 70B Versatile** — for Devil's Advocate + Editor.
  - OpenRouter **GPT-OSS 120B (free tier)** — for Novelty.
  - Originally planned DeepSeek; swapped to OpenRouter free tier (no deposit needed).
  - Originally planned Gemini 2.0 Flash; swapped to 2.5 Flash because 2.0 returned `RESOURCE_EXHAUSTED` immediately on this account.
- **Scholarly metadata source:** OpenAlex, not Semantic Scholar. No API key required (just an email for the polite pool). Decision rationale in [readme.md](../readme.md) and prior chat — OpenAlex is free, structured, and has a citation graph.

### Layout

```
whatsapp2/
├── .env.example
├── .gitignore
├── readme.md                  ← design doc
├── requirements.txt
├── data/
│   ├── papers/                ← downloaded PDFs + parsed JSON cache
│   └── chroma/                ← (Phase 3) ChromaDB persistence
├── docs/
│   └── PROGRESS.md            ← this file
├── scripts/
│   ├── verify_env.py
│   ├── download_test_paper.py
│   └── test_paper_parser.py
└── src/
    ├── config.py
    ├── schemas.py
    ├── logging_setup.py
    ├── agents/                ← (Phase 2+) reviewer + editor agents
    ├── clients/
    │   └── mcp_client.py
    ├── mcp_servers/
    │   └── paper_parser.py
    └── utils/
        └── pdf_extract.py
```

### How to verify Phase 0

```bash
.venv/Scripts/python.exe scripts/verify_env.py
```

Expected: three `[OK ]` lines for Gemini, Groq, OpenRouter. Last line: `All good.`

---

## Phase 1 — Paper-parser MCP server

**Goal:** the first MCP server is live. An external client can spawn it as a
subprocess over stdio, hand it a PDF path, and get back a structured paper —
proving the MCP layer works end-to-end before any LLM is wired in.

### Files created

| File | Purpose |
|---|---|
| [src/utils/pdf_extract.py](../src/utils/pdf_extract.py) | Pure parsing logic. Uses PyMuPDF to extract title, abstract, sections, references, figure captions. Caches by content hash in `data/papers/{hash}.json` so repeated calls are free. Kept separate from the MCP server so it can be unit-tested without subprocess overhead. |
| [src/mcp_servers/paper_parser.py](../src/mcp_servers/paper_parser.py) | FastMCP server exposing 5 tools: `parse_pdf`, `get_section`, `list_references`, `get_figure_captions`, `list_cached_papers`. Run with `python -m src.mcp_servers.paper_parser`. |
| [src/clients/mcp_client.py](../src/clients/mcp_client.py) | Async helper. `mcp_session(name)` context manager spawns the named server as a subprocess and returns a connected MCP `ClientSession`. `unwrap_tool_result` pulls the actual Python value out of FastMCP's `structuredContent` envelope. New servers register in `SERVER_MODULES`. |
| [scripts/download_test_paper.py](../scripts/download_test_paper.py) | One-shot fetcher for *Attention Is All You Need* (arXiv 1706.03762) into `data/papers/`. |
| [scripts/test_paper_parser.py](../scripts/test_paper_parser.py) | End-to-end smoke test. Spawns the MCP server, calls every tool, prints results. |

### Heuristics in the parser

- **Title:** largest-font text near the top of page 1, with arXiv margin headers (`arXiv:NNNN.NNNNN`) filtered out.
- **Sections:** regex match against a canonical list (`introduction`, `method`, `results`, etc.). Numbered prefixes (`3.`, `3.1`) are stripped.
- **Abstract:** prefers the `abstract` section if present; falls back to a regex window between "Abstract" and "Introduction".
- **References:** split on `[N]` markers in the references section; year extracted with `\b(19|20)\d{2}\b`.
- **Figure captions:** regex over each page for `Figure N:` / `Table N:` patterns. Slightly noisy — known limitation, fine for current use.

### How to verify Phase 1

```bash
.venv/Scripts/python.exe scripts/download_test_paper.py
.venv/Scripts/python.exe scripts/test_paper_parser.py
```

Expected output (verified):

```
title    : Attention Is All You Need
sections : abstract, intro, background, model, results, conclusion, ack
refs     : 40        (paper actually has 40)
figures  : 11
```

### Why this matters for the architecture

The MCP layer is now proven. Every subsequent reviewer agent will connect to
this server (and the three more in Phase 3) the same way: subprocess + stdio +
typed tool calls. The agent code never imports `pdf_extract` directly — it goes
through the protocol. That's what makes the MCP/A2A separation real rather than
cosmetic, and it's the key claim the final report rests on.

---

## Phase 2 — Methodology reviewer agent

**Goal:** the first reviewer agent goes end-to-end. PDF in, structured `Review`
JSON out, with an audit trail of every MCP call the agent made along the way.

### Files created

| File | Purpose |
|---|---|
| [src/agents/llm_factory.py](../src/agents/llm_factory.py) | `make_llm(role)` returns a LangChain chat model. `ROLE_TO_PROVIDER` maps each reviewer role to its provider — heterogeneous-panel ablation = one-line change here. Currently: methodology+ethics→Gemini, novelty→OpenRouter, devils_advocate+editor→Groq. |
| [src/agents/base_reviewer.py](../src/agents/base_reviewer.py) | Abstract `BaseReviewer` class. Subclasses declare `reviewer_id`, `role`, `mcp_servers`, `system_prompt`, and implement `gather_context(sessions, paper_path)`. The base handles MCP session lifecycle, structured-output coercion via `with_structured_output(Review)`, and tool-call audit logging. |
| [src/agents/methodology_reviewer.py](../src/agents/methodology_reviewer.py) | First concrete reviewer. Persona: rigorous experimental-design reviewer. Pulls method, experiments, results, ablations sections + figure captions via `paper_parser` MCP. Required `criterion_scores`: rigor, baselines, ablations. |
| [scripts/test_methodology_reviewer.py](../scripts/test_methodology_reviewer.py) | E2E test: spawns paper-parser MCP, runs the reviewer on the test PDF, prints structured review, saves to `data/review_methodology_attention.json`. |

### Why structured output instead of regex parsing

We use LangChain's `llm.with_structured_output(Review)` — the LLM is forced to
emit a JSON object that conforms to the Pydantic schema. The model can't return
free-form prose that we'd then need to post-process. This eliminates a whole
class of brittle parsing bugs and makes the editor's downstream aggregation
trivial (every review is already typed).

### Verified result on *Attention Is All You Need*

```
overall_score  : 7.0
criterion      : {rigor: 7, baselines: 9, ablations: 6}
strengths      : 4   (each citing a specific section/table)
concerns       : 3
  [major / 0.90] No statistical significance tests for SOTA BLEU claims (Section 6.1)
  [minor / 0.80] Optimizer + LR schedule details incomplete for full reproducibility
  [minor / 0.70] Ablation only covers multi-head attention; other novel components untested
tool_calls     : 4   (parse_pdf, get_section ×2, get_figure_captions)
```

Sanity check: these are real, defensible methodology critiques of the original
Transformer paper — not generic boilerplate. The persona prompt is doing its job.

### Note: not yet on LangGraph

Phase 2 calls the reviewer directly (`reviewer.review(path)`). LangGraph wiring
happens in Phase 5 (editor) where we need fan-out + state management for the
debate. Building reviewers as independent classes first lets us test each in
isolation before they become graph nodes.

### Note: stats_verifier not yet wired

The methodology reviewer reads paper text only. The Python sandbox for
re-running numerical claims is Phase 3, after which we'll add `"stats_verifier"`
to its `mcp_servers` list and extend the persona prompt to use it.

### How to verify Phase 2

```bash
.venv/Scripts/python.exe scripts/test_methodology_reviewer.py
```

Expected: prints the structured review, writes JSON to `data/`. First call
takes ~25s (LLM); subsequent calls reuse the parsed-paper cache so MCP overhead
is near zero.

---

## Phase 3 — Three more MCP servers (stats_verifier, openalex, rag)

**Goal:** finish the MCP layer. Every reviewer in Phase 4 has the tools it needs.

### Files created

| File | Purpose |
|---|---|
| [src/mcp_servers/stats_verifier.py](../src/mcp_servers/stats_verifier.py) | Python sandbox via `subprocess` with timeout. Tools: `run_python_sandbox(code, timeout)`, `check_t_statistic(t, df)`. Used by Methodology reviewer to sanity-check numerical claims (e.g. recompute a reported p-value). |
| [src/mcp_servers/openalex.py](../src/mcp_servers/openalex.py) | OpenAlex REST client. Tools: `search_works`, `search_related`, `get_work`, `get_references`, `get_citations`, `verify_paper_exists`. No API key needed; passes `mailto=<email>` for the polite pool. Used by Novelty reviewer + Hallucination auditor. |
| [src/mcp_servers/rag.py](../src/mcp_servers/rag.py) | ChromaDB persistent collection + sentence-transformers `all-MiniLM-L6-v2` (384-dim). Tools: `index_paper`, `query_corpus`, `corpus_stats`, `clear_corpus`. Used by Novelty reviewer to surface papers the citation graph missed. |
| [scripts/test_phase3_servers.py](../scripts/test_phase3_servers.py) | Smoke tests for all three servers — sandbox math + timeout, OpenAlex search + verify (real + fake paper), Chroma index + query round-trip. |

### Two non-obvious gotchas

1. **Eager imports for any heavy library used in tool handlers.** Lazy `import scipy.stats` inside an async tool handler deadlocks the asyncio event loop on Python 3.14 / Windows. Same for ChromaDB + sentence-transformers init. Both servers now import everything at module load (synchronously, before `mcp.run()`). Cost: server startup is ~10s once cached. Benefit: every subsequent call is millisecond-fast and the loop never blocks past the MCP request timeout.
2. **First sentence-transformers run downloads ~80MB.** Cold install of `all-MiniLM-L6-v2` from HuggingFace took 51s on this machine. Cached after that. Worth flagging for first-run setup.

### Verified results

```
stats_verifier:
  numpy mean test       -> 3.0           (0.3s)
  check_t_statistic     -> p=0.0191      (0.0s)
  while-True + 2s tmout -> timed_out=True (2.1s)

openalex:
  search('attention is all you need transformer', k=3) -> 3 works returned
  verify('Attention Is All You Need', 2017)            -> exists=True conf=1.0
  verify('Quantum Kangaroos in RL', 2099)              -> exists=False conf=0.143

rag:
  initial corpus_stats     -> {chunks: 0}
  index_paper(aiayn)       -> {indexed: 1}
  query('multi-head self-attention')
                           -> 1 hit, dist=0.426, title='Attention Is All You Need'
```

### Updated client registry

[src/clients/mcp_client.py](../src/clients/mcp_client.py) now exposes four servers:

```python
SERVER_MODULES = {
    "paper_parser":   "src.mcp_servers.paper_parser",
    "stats_verifier": "src.mcp_servers.stats_verifier",
    "openalex":       "src.mcp_servers.openalex",
    "rag":            "src.mcp_servers.rag",
}
```

### How to verify Phase 3

```bash
.venv/Scripts/python.exe scripts/test_phase3_servers.py
```

First run takes ~60s (model download). Subsequent runs ~10s.

---

## What's next

**Phase 4 — three more reviewers:** Novelty (uses paper_parser + openalex + rag),
Devil's Advocate (uses paper_parser only — argues for rejection), Ethics (uses
paper_parser + openalex). Each gets a sharp persona prompt distinct enough that
the four reviewers will disagree meaningfully — the disagreement is what the
debate loop in Phase 6 metabolizes.

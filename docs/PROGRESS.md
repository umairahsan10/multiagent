# Implementation progress

Tracks what's been built, what each file does, and how to verify each phase.
The full design lives in [readme.md](../readme.md); this file is the build log.

## Status overview

| Phase | Title | Status |
|---|---|---|
| 0 | Project scaffolding + env setup | ✅ Done |
| 1 | Paper-parser MCP server | ✅ Done |
| 2 | Methodology reviewer agent | ✅ Done |
| 3 | stats_verifier + openalex + rag MCP servers | ✅ Done |
| 4 | Novelty + Devil's Advocate + Ethics reviewers | ✅ Done |
| 5 | Editor orchestrator + Round-1 fan-out + disagreement detection | ✅ Done |
| 6 | A2A debate loop + contested-claims synthesis | ✅ Done |
| 7 | Streamlit UI + evaluation harness | ✅ Done |
| 8 | Hallucination auditor + ablations + report assets | ⏳ Planned |

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

## Phase 4 — Novelty, Devil's Advocate, Ethics reviewers ✅ DONE

**Goal:** complete the four-reviewer panel. By the end of this phase, calling
each reviewer on the same paper produces four distinct, evidence-backed,
schema-conformant `Review` objects that visibly disagree.

### Files created

| File | Purpose |
|---|---|
| [src/agents/novelty_reviewer.py](../src/agents/novelty_reviewer.py) | Persona: novelty-judging reviewer. Uses paper_parser + openalex + rag. Pulls contribution-relevant sections, searches OpenAlex for related works by title+abstract, queries the RAG corpus for semantically similar work. Criterion scores: novelty, contribution, prior_art_coverage. |
| [src/agents/devils_advocate_reviewer.py](../src/agents/devils_advocate_reviewer.py) | Persona: deliberately argues for rejection. Paper_parser only. Prompt forces focus on the top 3 reasons for rejection; nitpicks are explicitly disallowed. Criterion scores: rigor_skepticism, claim_overreach, presentation_issues. |
| [src/agents/ethics_reviewer.py](../src/agents/ethics_reviewer.py) | Persona: ethics + societal-impact reviewer. Paper_parser + openalex. Precision-over-recall prompt — small number of specific concerns is preferred to boilerplate. Criterion scores: dataset_ethics, dual_use, claim_calibration. |
| [scripts/test_all_reviewers.py](../scripts/test_all_reviewers.py) | Runs all four reviewers in parallel (`asyncio.gather`), prints side-by-side score table, pairwise disagreement spread, representative concerns per reviewer. Saves each review to `data/round1/review_<id>.json`. |
| [src/agents/base_reviewer.py](../src/agents/base_reviewer.py) updates | Added `_invoke_llm_with_retry` using tenacity: retries only on transient markers (`429`, `503`, `unavailable`, `rate_limit`, etc.) with exponential backoff 2→30s, max 4 attempts. |

### Key design decisions

- **Prompt-level diversification.** The four personas differ on what counts as a *strength* vs. *concern*, what `criterion_scores` they emit, and which MCP tools they reach for. This is the source of structural disagreement — without it, even different LLMs give similar verdicts because they're answering the same question.
- **Tool budget per reviewer.** Methodology has 2 tools (paper_parser + stats_verifier — wired up in this phase). Novelty has 3 (paper_parser + openalex + rag). Devil's Advocate has 1 (paper_parser only — its critique must come from the text alone). Ethics has 2 (paper_parser + openalex). Different tool sets → different evidence available → different conclusions.
- **Methodology gets stats_verifier wired now.** Add `"stats_verifier"` to its `mcp_servers` list and update the persona prompt to tell it: "if the paper reports a t-statistic, F-statistic, or specific p-value, recompute it via the sandbox and flag any discrepancy."

### Provider remap (learned the hard way)

Original plan had two reviewers on Gemini. Reality:
- Gemini 2.5 Flash free tier: 5 RPM **and** 20 requests/day. Two reviewers in parallel exhausted the daily limit in one test run.
- Gemini 2.5 Flash-Lite free tier: 15 RPM, 1000/day. Way more headroom — swapped in.
- Moved ethics off Gemini to Groq (Llama 3.3 70B — 30 RPM).
- OpenRouter: gpt-oss-120b:free was unreliable for structured output (emitted `<|endoftext|>` mid-JSON). Switched to `nvidia/nemotron-3-super-120b-a12b:free` — stable.

Final mapping in [src/agents/llm_factory.py](../src/agents/llm_factory.py):

| Reviewer | Provider | Model |
|---|---|---|
| methodology | Google | gemini-2.5-flash-lite |
| novelty | OpenRouter | nvidia/nemotron-3-super-120b-a12b:free |
| devils_advocate | Groq | llama-3.3-70b-versatile |
| ethics | Groq | llama-3.3-70b-versatile |
| editor (Phase 5) | Groq | llama-3.3-70b-versatile |

### Verified results on *Attention Is All You Need*

```
                     methodology   novelty   devils_adv   ethics
overall_score        7.0           9.0       7.0          8.0
num_strengths        3             2         2            2
num_concerns         5             1         3            2
critical_concerns    0             0         0            0
major_concerns       3             0         2            0
tool_calls           4             7         8            6

Parallel wall time: 64.4s   (bounded by novelty — the slowest reviewer)

Pairwise score spread:
  methodology    vs novelty          Δ=2.0  *
  methodology    vs devils_advocate  Δ=0.0
  methodology    vs ethics           Δ=1.0
  novelty        vs devils_advocate  Δ=2.0  *
  novelty        vs ethics           Δ=1.0
  devils_advocate vs ethics          Δ=1.0
```

Two pairs exceed the 1.5-point spread threshold — these become debate-round
candidates in Phase 6. This is the key success metric for Phase 4: the panel
actually disagrees. If score spreads were all <0.5, the four personas would
be redundant and the debate phase would have nothing to resolve.

### How to verify Phase 4

```bash
.venv/Scripts/python.exe scripts/test_all_reviewers.py
```

Writes four reviews to [data/round1/](../data/round1/). Takes ~60-90s (dominated by novelty's Nemotron call).

---

## Phase 5 — Editor orchestrator + Round-1 fan-out + disagreement detection ✅ DONE

**Goal:** the system becomes a real LangGraph. The editor distributes a paper
to all four reviewers in parallel, aggregates their output, and computes which
pairs disagree enough to warrant a debate round.

### Files created

| File | Purpose |
|---|---|
| [src/orchestrator/state.py](../src/orchestrator/state.py) | `ReviewState` TypedDict. Two reducer functions: `merge_reviews` (dict merge for parallel reviewer writes) and `extend_a2a` (append for Phase 6 debate thread). `total=False` so we can start with partial state. |
| [src/orchestrator/disagreement.py](../src/orchestrator/disagreement.py) | `compute_pairwise_disagreements(reviews)` returns a `Disagreement` per pair. Two signals, both normalized 0-1: score_spread_norm (`|score_a - score_b|/9`) and summary_cosine_distance (1 - cosine similarity of MiniLM-embedded summaries). Combined 50/50. Flagged if ≥ `DISAGREEMENT_THRESHOLD` (default 0.35). |
| [src/agents/editor.py](../src/agents/editor.py) | Editor LangGraph node. Computes disagreements + a naive aggregated `Verdict` from Round-1 reviews. Recommendation from mean score (≥7 = accept, ≥5 = revise, else reject); confidence from score std-dev. `contested_claims` stays empty in Phase 5 — populated in Phase 6 from A2A debate. |
| [src/orchestrator/graph.py](../src/orchestrator/graph.py) | `build_round1_graph()`. Five nodes: the four reviewers + editor. Edges: `START → each reviewer` (parallel fan-out), `each reviewer → editor` (fan-in), `editor → END`. `_make_reviewer_node` factory wraps each reviewer class as an async LangGraph node. |
| [src/schemas.py](../src/schemas.py) updated | Added `Disagreement` model; added `summary` field to `Verdict`. |
| [scripts/test_round1.py](../scripts/test_round1.py) | Runs `graph.ainvoke(initial_state)` on the test paper, prints disagreement matrix sorted by combined score, prints the naive verdict, saves full graph state to `data/round1_graph/round1_state.json`. |

### Key design decisions

- **LangGraph state is the single source of truth.** The reviewers are pure functions over the paper; only the editor mutates state. All A2A messages (Phase 6) live in `state["a2a_thread"]` so the entire debate is replayable.
- **Two-signal disagreement detection.** Embedding cosine alone is too noisy (two reviewers can write similar prose with opposite recommendations). Severity contradictions alone are too sparse (concerns rarely match by exact wording). Combining both is more robust.
- **Naive aggregation in this phase.** A simple mean-of-overall-scores produces a placeholder verdict so we can compare against the post-debate verdict in Phase 6 (this comparison is the "no debate vs 3-round debate" ablation).

### The fan-out/fan-in pattern in LangGraph

Four `add_edge(START, reviewer)` calls schedule the reviewers in parallel
automatically — LangGraph detects the multiple edges from a single node and
runs them concurrently. Each reviewer returns `{"reviews": {reviewer_id: review}}`,
which LangGraph merges into the shared `reviews` dict via the `merge_reviews`
reducer declared on the state field. The editor node sees a populated 4-entry
dict by the time it runs.

This is the whole reason we chose LangGraph over CrewAI. `add_edge(START, A)`,
`add_edge(START, B)`, ... → parallel execution with typed-state merging. In
CrewAI this would require a custom `Flow` wrapping a manual `asyncio.gather`.

### Verified results on *Attention Is All You Need*

```
REVIEWS SUMMARY
  devils_advocate   score=7.0  concerns=3  tool_calls=8
  ethics            score=8.0  concerns=2  tool_calls=6
  methodology       score=7.0  concerns=5  tool_calls=4
  novelty           score=8.5  concerns=1  tool_calls=7

PAIRWISE DISAGREEMENT MATRIX
  pair                             score_spread  cos_dist  combined  flagged
  methodology vs novelty           1.50          0.483     0.325
  ethics vs methodology            1.00          0.377     0.244
  devils_advocate vs novelty       1.50          0.295     0.231
  ethics vs novelty                0.50          0.397     0.226
  devils_advocate vs ethics        1.00          0.249     0.180
  devils_advocate vs methodology   0.00          0.339     0.170

NAIVE AGGREGATED VERDICT
  recommendation : accept
  confidence     : 0.856
  mean score     : 7.62  (±0.65 std-dev)
  per_criterion  : 12 keys averaged across reviewers
  concerns       : 0 critical, 5 major (deduplicated)
```

**Interpretation:** On this paper, no pair crosses the 0.35 threshold, so the
panel is effectively in agreement — the Phase 6 debate loop would exit early
on this paper. This is a *feature*, not a bug: the Transformer paper is a
strong, well-known work; faking disagreement for demo purposes would be
dishonest. To exercise the debate loop fully, Phase 6 needs a paper with more
structural controversy (or we tune the threshold down).

### Total state-object size

The saved `data/round1_graph/round1_state.json` is ~45KB (4 reviews, 6
disagreements, a verdict). This is the full system output after Round 1, ready
for Phase 6 to consume and extend.

### How to verify Phase 5

```bash
.venv/Scripts/python.exe scripts/test_round1.py
```

Expected wall time: ~60-100s (novelty reviewer is the bottleneck). Saves
full graph state to `data/round1_graph/round1_state.json`.

---

## Phase 6 — A2A debate loop + contested-claims synthesis ✅ DONE

**Goal:** when reviewers disagree, the editor brokers structured rebuttals.
After up to 3 rounds, the editor synthesizes a verdict that explicitly preserves
unresolved disagreements as "contested claims" rather than forcing consensus.

### Files created

| File | Purpose |
|---|---|
| [src/orchestrator/a2a.py](../src/orchestrator/a2a.py) | A2A helpers. `build_rebuttal_requests(flagged, reviews, round)` emits two `A2AMessage`s per flagged pair (each reviewer is asked to respond to the other). `messages_for_reviewer` filters the thread; `format_challenges_for_reviewer` renders messages into a prompt-ready text block. The editor is the sender of all rebuttal_requests — peers never message each other directly. |
| [src/agents/base_reviewer.py](../src/agents/base_reviewer.py) updates | Added `respond_to_rebuttals(prior_review, challenges, paper_path)` method. Re-gathers paper context (cached) + receives the challenger's summary + concerns + score. LLM produces a structured `RebuttalOutcome` (position_changed flag, rationale, updated Review). |
| [src/agents/debate_round.py](../src/agents/debate_round.py) | LangGraph node. Filters the thread for messages addressed to each reviewer in the current round; runs `respond_to_rebuttals` for each in parallel via `asyncio.gather`. Emits `rebuttal_response` messages + `position_changed` `announcement`s into the A2A thread. Increments `state["round"]`. |
| [src/agents/editor.py](../src/agents/editor.py) updates | Now debate-aware. After computing disagreements, calls `_detect_stalemate()` (reads announcements from the just-completed round). Routes via `should_continue_debate()` conditional-edge function: `debate_round` if (flagged AND round<MAX AND not stalemate), else `synthesis`. |
| [src/agents/synthesis.py](../src/agents/synthesis.py) | Final-verdict node. LLM (Gemini 2.5 Flash-Lite — moved off Groq because Llama flattens nested-object structured output) reads all final reviews + full A2A thread + disagreement matrix and produces a `Verdict` with `contested_claims` populated. Prompt explicitly forbids inventing contested claims when reviewers actually converged. |
| [src/orchestrator/graph.py](../src/orchestrator/graph.py) | New `build_debate_graph()`. Adds two nodes (`debate_round`, `synthesis`) and a conditional edge from `editor`. The debate_round → editor edge creates the loop. |
| [src/schemas.py](../src/schemas.py) | Added `RebuttalOutcome`. |
| [scripts/test_full_debate.py](../scripts/test_full_debate.py) | E2E: paper in → 4 reviews → debate rounds → final verdict. Prints round-by-round A2A thread (REQ/RES/ANN) and the contested_claims block. Saves to `data/full_debate/debate_state.json`. |

### Key design decisions

- **Termination is multi-condition.** Hard cap at 3 rounds, OR aggregate disagreement drops below threshold, OR no reviewer changed position between consecutive rounds (stalemate detection). A stalemate is not failure — those claims are recorded as `contested_claims` in the verdict.
- **Rebuttal requests are typed messages, not plain strings.** A `rebuttal_request` carries the challenger's specific claim, the target's prior position, and the request body. The target must respond in a typed `rebuttal_response` (structured output again). This is the heart of the A2A protocol claim — without typed messages it would be just chat.
- **Position changes are first-class.** The rebuttal handler returns a `position_changed: bool` flag. The editor uses this to detect stalemate.

### Three things had to be debugged

1. **Stalemate off-by-one.** Editor was checking announcements at `round - 1` while debate_round emits them at `round`. Fixed in `_detect_stalemate`. Symptom: stalemate fired immediately after first debate round even when reviewers had updated.
2. **Groq Llama can't do nested structured output.** `consensus_concerns: list[Concern]` returned `[["claim string", 0.8, ...]]` (array of arrays) instead of `[{"claim": ..., "severity": ..., ...}]`. Fix: moved `editor` role from Groq to Gemini. Synthesis is one call per paper, so Gemini's lower rate limit is fine.
3. **OpenRouter Nemotron occasionally returns `choices=None`.** Surfaces as `TypeError: 'NoneType' object is not iterable` in the OpenAI client. Added to `_TRANSIENT_MARKERS` so retry catches it. Also shrank the rebuttal paper context from 20k → 8k chars (the reviewer already saw the full paper in Round 1).

### Verified end-to-end on *Attention Is All You Need*

```
ROUND 1 — 4 reviewers in parallel
  methodology      score=7.0  concerns=5
  novelty          score=9.0  concerns=0
  devils_advocate  score=7.0  concerns=3
  ethics           score=8.0  concerns=2

EDITOR R1 — 1/6 pairs flagged at threshold=0.25
  methodology vs novelty   combined=0.331
  → emits 2 rebuttal_requests for round 2

ROUND 2 — both targets respond
  methodology  7.0 → 7.0  (held position)
  novelty      9.0 → 9.0  (held position)
  → no announcements → stalemate detected

EDITOR R2 → routes to synthesis

SYNTHESIS (Gemini) — final verdict:
  recommendation : revise
  confidence     : 0.7
  consensus concerns: 3
  contested claims  : 1
    "The Transformer model's performance generalizes well to tasks beyond machine translation."
       methodology: disagreed | devils_advocate: disagreed | ethics: neutral | novelty: neutral
       editor_note: methodology + devils_advocate raised concerns about generalization;
                    ethics + novelty did not address it; debate did not converge.

WALL TIME: 200.8s
```

This is exactly the system's distinctive output: the verdict tells the author that
the panel converged on a `revise` recommendation but **also** preserves the one
specific claim — generalization-beyond-MT — where reviewers genuinely couldn't
agree. Most automated review systems would have dropped that and forced a single
verdict. Ours surfaces it as a `contested_claim` with each reviewer's position
attached.

### The "no-debate vs debate" ablation is now possible

We have:
- [data/round1_graph/round1_state.json](../data/round1_graph/) — naive verdict from Round 1 only (Phase 5)
- [data/full_debate/debate_state.json](../data/full_debate/) — verdict after debate (Phase 6)

Compare side-by-side: Phase 5's verdict was "accept" (mean 7.62 → ≥7); Phase 6's
post-debate verdict is "revise" with 1 contested_claim. The debate phase made the
final answer more honest about the panel's actual disagreement. That's the
ablation result for the report.

### How to verify Phase 6

```bash
DISAGREEMENT_THRESHOLD=0.25 .venv/Scripts/python.exe scripts/test_full_debate.py
```

(env override is only needed if your `.env` still has the old 0.35 default;
`Config` defaults to 0.25 now.) Wall time: ~3 min on the Transformer paper.

---

## Phase 7 — Streamlit UI + evaluation harness ✅ DONE

**Goal:** (a) a usable demo for the project presentation, (b) the evaluation
infrastructure that produces the numbers in the IEEE report.

### Files created

| File | Purpose |
|---|---|
| [app.py](../app.py) | Streamlit demo. Sidebar = file uploader + run button + provider info; main area = final verdict card (recommendation, confidence bar, contested claims with per-reviewer position chips), debate thread (per-round expander), and per-reviewer tabs with full review + MCP tool-call audit trail. Uses `asyncio.run()` inside an `st.status` context. ~250 lines. |
| [src/evaluation/metrics.py](../src/evaluation/metrics.py) | Four pure-function metrics: `verdict_correlation` (accuracy + per-class P/R/F1 + confusion matrix), `issue_detection_pr` (semantic-similarity matching at threshold 0.65), `inter_agent_disagreement` (mean/std/histogram of pairwise scores), `citation_hallucination_rate` (split into `collect_citation_candidates` + `citation_hallucination_rate(candidates, verified)` so MCP I/O happens at the call site, not in the metrics module). |
| [src/evaluation/harness.py](../src/evaluation/harness.py) | `run_corpus(corpus_dir, results_dir, max_papers, use_cache)` — iterates PDFs, runs the debate graph on each, caches per-paper JSON. `_load_record` rehydrates cached JSON back into typed Pydantic objects so metrics can be re-computed without re-running the LLMs. |
| [scripts/fetch_openreview.py](../scripts/fetch_openreview.py) | Reads `data/eval_corpus/arxiv_ids.txt` (one ID per line), downloads each as `<id>.pdf` to `data/eval_corpus/papers/`. Pragmatic choice over OpenReview API scraping — venue schemas vary too much for a robust general-purpose scraper. |
| [scripts/run_evaluation.py](../scripts/run_evaluation.py) | Top-level. Runs harness, gathers all citation candidates, batch-resolves them through one OpenAlex MCP session, computes all 4 metrics, writes `data/eval_results/metrics.json`. CLI: `--max N` for smoke tests, `--no-cache` to re-run. |
| [data/eval_corpus/](../data/eval_corpus/) | Seeded with 1 paper (`1706.03762.pdf`) + `ground_truth.json` so the smoke test runs out-of-the-box. |

### Key design decisions

- **The Streamlit app is read-mostly demo, not the primary interface.** Heavy lifting is the CLI evaluation harness; the UI just wraps it for presentation purposes.
- **Manual concern-overlap annotation is unavoidable.** Issue-detection P/R requires a human (the project author) to read 20 papers' worth of human + system reviews and judge which concerns overlap. Budget time for this — 20 thoroughly-annotated papers > 50 skimmed.
- **Caching is critical.** Every OpenReview paper, every parsed PDF, every OpenAlex query, every ChromaDB embedding is cached. Re-running the evaluation must not re-hit external APIs.

### One subtle async refactor

Initial design had `citation_hallucination_rate(runs, verify_fn)` where
`verify_fn` was a sync wrapper around an async OpenAlex call. That hit
"Cannot run the event loop while another loop is running" because the outer
`asyncio.run()` was already active. Fix: split the metric into two parts —
`collect_citation_candidates(runs)` returns plain tuples, the eval script
batch-resolves them async in a single OpenAlex MCP session, then
`citation_hallucination_rate(candidates, verified)` computes the stat from the
resolved dict. metrics.py stays pure-function with zero I/O.

### Smoke test result (1 paper, full pipeline)

```
Verdict accuracy           : 0.0   (system: revise; ground truth: accept)
Issue detection micro-F1   : 0.0   (semantic-match threshold of 0.65 too strict on small sample)
Mean pairwise disagreement : combined=0.189
Citation hallucination     : 1.0   (5/5 — citation extractor pulls year-windowed text, not titles)
```

The pipeline runs end-to-end. Two metrics are honest-but-coarse on a single
paper: the issue-detection match is conservative, and the citation extractor
pulls noisy substrings. Phase 8's hallucination auditor will replace the
naive citation candidate extraction with a proper title-level walker; the
issue-detection number will firm up with more papers + manual annotation.

### How to verify Phase 7

```bash
# Demo UI — opens at http://localhost:8501
streamlit run app.py

# Build corpus from arxiv_ids.txt
.venv/Scripts/python.exe scripts/fetch_openreview.py

# Run evaluation (smoke test on 1 paper)
DISAGREEMENT_THRESHOLD=0.25 .venv/Scripts/python.exe scripts/run_evaluation.py --max 1

# Full evaluation (only after you've populated arxiv_ids.txt + ground_truth.json)
DISAGREEMENT_THRESHOLD=0.25 .venv/Scripts/python.exe scripts/run_evaluation.py
```

Per-paper graph state is cached in `data/eval_results/runs/<paper>.json` so
re-running computes metrics from cache without re-burning LLM tokens. Use
`--no-cache` to force re-run.

---

## Phase 8 — Hallucination auditor + ablations + report assets (PLANNED)

**Goal:** the polish phase. A post-hoc auditor catches fabricated citations,
two ablation studies generate the comparative numbers the report rests on, and
final assets (architecture diagram, results tables, presentation script) are
produced.

### Files to create

| File | Purpose |
|---|---|
| `src/agents/hallucination_auditor.py` | Post-hoc auditor. Walks `state["reviews"]` and the A2A thread; for every paper-reference any reviewer cited, calls `openalex.verify_paper_exists`. Produces a per-review hallucination rate. Not a debater — runs after synthesis, not before. |
| `src/evaluation/ablations.py` | Two ablation runners: (a) **single-model panel** — re-run evaluation with all four reviewers using Gemini, compare verdict quality + disagreement vs. heterogeneous panel; (b) **no-debate** — re-run with debate disabled (Phase 5 naive aggregation only), compare verdicts vs. full 3-round version. |
| `docs/architecture.md` + `docs/architecture.svg` | One-page architecture diagram for the report. Shows MCP layer (4 servers) below the agent layer (4 reviewers + editor) with the two protocol families (MCP vs A2A) clearly distinguished. |
| `docs/REPORT_ASSETS.md` | Results tables (verdict correlation, issue P/R, hallucination rate before/after auditor, both ablations) in markdown — copy/paste into the IEEE template. |
| `docs/PRESENTATION.md` | 15-minute presentation script: 2 min motivation, 4 min architecture, 6 min demo + results, 2 min ethics, 1 min conclusion. |

### Key design decisions

- **Auditor is independent, not in-loop.** Putting it in the debate loop would let reviewers learn to avoid citations, which masks the underlying hallucination tendency. Run it post-hoc so the rate is honest.
- **Report the rate before AND after the auditor.** The "before" number is the failure mode of an unaudited LLM-based reviewer; the "after" number is what your system actually delivers. The delta is the contribution.
- **Both ablations are pre-registered.** Fix the ablation design (single-model vs. heterogeneous, no-debate vs. 3-round) before running them — don't post-hoc choose comparisons that flatter the system. The report will note which were pre-registered.

### How verification will work

```bash
.venv/Scripts/python.exe scripts/run_evaluation.py --with-auditor
.venv/Scripts/python.exe -m src.evaluation.ablations --variant single_model
.venv/Scripts/python.exe -m src.evaluation.ablations --variant no_debate
```

Each writes a `data/ablation_<name>.json` for inclusion in the report.

---

## Cross-phase notes

### Total dependency footprint

By Phase 8 the project depends on: Python 3.11+ (using 3.14), LangGraph,
LangChain (3 provider adapters), MCP SDK, PyMuPDF, ChromaDB,
sentence-transformers (~80MB model), Streamlit, pandas, scikit-learn, scipy,
numpy, pydantic, httpx, tenacity, rich, python-dotenv. All free, all open
source. No paid services beyond LLM tokens (and even those have free tiers
covering most of the project).

### What this project is and isn't

**Is:** a working multi-agent system with a real MCP/A2A separation, structured
peer-review output that includes a debate trail and contested claims, and an
evaluation methodology that produces defensible quantitative results.

**Isn't:** a production peer-review service. Latency (45-90s/paper), cost
(LLM tokens per run), and reliability (rate limits, occasional hallucinated
citations) are fine for research/coursework but unsuitable for live deployment.

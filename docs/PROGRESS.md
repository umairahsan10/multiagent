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
| 6 | A2A debate loop + contested-claims synthesis | ⏳ Planned |
| 7 | Streamlit UI + evaluation harness | ⏳ Planned |
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

## Phase 6 — A2A debate loop + contested-claims synthesis (PLANNED)

**Goal:** when reviewers disagree, the editor brokers structured rebuttals.
After up to 3 rounds, the editor synthesizes a verdict that explicitly preserves
unresolved disagreements as "contested claims" rather than forcing consensus.

### Files to create

| File | Purpose |
|---|---|
| `src/orchestrator/a2a.py` | A2A message construction + routing. `make_rebuttal_request(challenger, target, claim)` produces a structured `A2AMessage`. The router appends to `state["a2a_thread"]` so the full exchange is auditable. |
| `src/agents/rebuttal_handler.py` | Mixin or method added to `BaseReviewer`: when called with an `A2AMessage` of type `rebuttal_request`, the reviewer re-reads the relevant section of its own prior review and the challenger's evidence, then emits a `rebuttal_response` that either updates its position (with new `confidence`) or defends it (with new evidence). |
| `src/orchestrator/graph.py` | Extended with the debate loop. Conditional edge: while round < `MAX_DEBATE_ROUNDS` and disagreements > threshold and at least one reviewer changed position last round, loop back to debate. Otherwise → synthesis. |
| `src/agents/synthesis.py` | Final-verdict synthesizer (uses Editor LLM = Groq Llama 3.3 70B). Takes the full A2A thread + final reviews and emits a `Verdict` with `consensus_strengths`, `consensus_concerns`, and `contested_claims` (the part most automated reviewers omit). |
| `scripts/test_full_debate.py` | E2E: paper in → 4 reviews → debate rounds → final verdict. Prints round-by-round summary and final verdict JSON. |

### Key design decisions

- **Termination is multi-condition.** Hard cap at 3 rounds, OR aggregate disagreement drops below threshold, OR no reviewer changed position between consecutive rounds (stalemate detection). A stalemate is not failure — those claims are recorded as `contested_claims` in the verdict.
- **Rebuttal requests are typed messages, not plain strings.** A `rebuttal_request` carries the challenger's specific claim, the target's prior position, and the request body. The target must respond in a typed `rebuttal_response` (structured output again). This is the heart of the A2A protocol claim — without typed messages it would be just chat.
- **Position changes are first-class.** The rebuttal handler returns a `position_changed: bool` flag. The editor uses this to detect stalemate.

### How verification will work

```bash
.venv/Scripts/python.exe scripts/test_full_debate.py
```

Expected:
- Round 1: 4 reviews printed.
- Round 2 (if disagreements): rebuttal requests + responses logged. Some reviewers update scores.
- Round 3 (if still disagreeing): final round.
- Synthesis: a `Verdict` with at least one `contested_claim` entry on a real-world paper.

A "healthy" debate shows score movement of ≥0.5 by at least one reviewer in round 2 or 3. If nobody ever updates, the rebuttal prompt isn't pushing hard enough.

---

## Phase 7 — Streamlit UI + evaluation harness (PLANNED)

**Goal:** (a) a usable demo for the project presentation, (b) the evaluation
infrastructure that produces the numbers in the IEEE report.

### Files to create

| File | Purpose |
|---|---|
| `app.py` | Streamlit app at the project root. File uploader for a PDF, "Run review" button, four reviewer panels showing each review with collapsible concern lists, a debate-thread visualization (timeline of A2A messages), and a final verdict card with `contested_claims` highlighted. ~150 lines. |
| `src/evaluation/harness.py` | Batch runner: takes a list of `(pdf_path, ground_truth_decision)` tuples, runs the full pipeline on each, dumps results + metrics to JSON. |
| `src/evaluation/metrics.py` | Four metric functions: `verdict_correlation` (system recommendation vs. real outcome), `issue_detection_pr` (precision/recall of system concerns vs. human-reviewer concerns — manual annotation comparison), `inter_agent_disagreement` (mean pairwise cosine distance, plotted as a histogram), `citation_hallucination_rate` (fraction of cited papers that fail OpenAlex verification). |
| `scripts/fetch_openreview.py` | Pulls 30-50 papers + their human reviews + decisions from OpenReview (ICLR 2022-2024). Caches everything locally so the evaluation is reproducible offline. |
| `scripts/run_evaluation.py` | Top-level: load OpenReview corpus → run harness → write `data/evaluation_results.json` and `data/evaluation_metrics.json`. |

### Key design decisions

- **The Streamlit app is read-mostly demo, not the primary interface.** Heavy lifting is the CLI evaluation harness; the UI just wraps it for presentation purposes.
- **Manual concern-overlap annotation is unavoidable.** Issue-detection P/R requires a human (the project author) to read 20 papers' worth of human + system reviews and judge which concerns overlap. Budget time for this — 20 thoroughly-annotated papers > 50 skimmed.
- **Caching is critical.** Every OpenReview paper, every parsed PDF, every OpenAlex query, every ChromaDB embedding is cached. Re-running the evaluation must not re-hit external APIs.

### How verification will work

```bash
streamlit run app.py                                    # demo UI
.venv/Scripts/python.exe scripts/fetch_openreview.py    # one-time corpus pull
.venv/Scripts/python.exe scripts/run_evaluation.py      # full eval (~hours, runs overnight)
```

Expected outputs:
- `data/evaluation_results.json` — full per-paper output (every review, debate thread, verdict).
- `data/evaluation_metrics.json` — aggregate numbers ready to drop into the IEEE report.

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

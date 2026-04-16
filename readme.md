Alright, here's the full breakdown. I'll go deep enough that you can start building after reading it.

# The thesis of the system

You are building a system where four LLM-powered agents disagree about a research paper on purpose, record their disagreement in a structured way, argue it out through bounded debate, and produce a final verdict that preserves what they agreed on and what they never resolved. Underneath, two different protocols handle two different kinds of communication: **MCP** moves data between agents and their tools, **A2A** moves claims and rebuttals between agents themselves. The interesting pedagogical and research contribution is not "we built a multi-agent system" — it's "we built one where the two protocols are separated cleanly, and we can measure whether the agents actually disagreed."

# Part I: The two-protocol separation

This is the single most important design idea in the project, so understand it before anything else.

In most student multi-agent projects, everything happens through ad-hoc Python function calls. An agent "uses a tool" and an agent "talks to another agent" both look like the same kind of function call in the code. This collapses two fundamentally different things:

**MCP (Model Context Protocol)** is for talking to tools and data. It's client/server: the server exposes a fixed set of capabilities (a PDF parser, a database, a search API) and the client (your agent) calls them. The server has no memory of the conversation, no goals, no opinions. It's a capability provider. Think of it like HTTP for LLM tools.

**A2A (Agent-to-Agent)** is for talking to other agents. The other agent is a peer — it has state, goals, opinions, and can push back. You're not requesting a capability, you're exchanging claims. An A2A message typically includes metadata about the sender, a claim, and sometimes a request for response.

In our system:

- When the Methodology reviewer calls the stats-verifier server to recompute a p-value: **MCP**. That server doesn't care who's asking, doesn't remember the query, and can't argue.
- When the Methodology reviewer tells the Novelty reviewer "your claim that this paper is novel is inconsistent with my finding that its statistical method was published by Smith et al. 2019": **A2A**. The Novelty reviewer will respond, may update its position, and the exchange becomes part of the permanent record of the review.

Keeping these separate is not just tidy — it matches how these protocols were designed to work in the real world, and being able to articulate the distinction in your report is what moves you from a typical B-grade project to a strong A.

# Part II: The complete agent inventory

You have five agents in the full version (four in the MVP). Every agent has a persona, a set of MCP clients it uses, an output schema, and a role in the A2A debate.

**Methodology reviewer** — evaluates experimental design, statistical validity, baseline adequacy, ablation completeness. MCP clients: paper-parser, stats-verifier. A2A role: challenger on technical claims.

**Novelty reviewer** — evaluates contribution relative to prior work. MCP clients: paper-parser, Semantic Scholar, RAG. A2A role: challenger on novelty claims.

**Devil's advocate** — deliberately argues for rejection. Takes every claim charitably from the authors' perspective, then attacks it. MCP clients: paper-parser only. A2A role: permanent opposition; pushes back on optimism from all other reviewers.

**Ethics and impact reviewer** — evaluates dataset bias, populations evaluated on, dual-use concerns, claims outrunning evidence. MCP clients: paper-parser, Semantic Scholar (for checking claimed comparisons to related ethical work). A2A role: raises concerns the technical reviewers often miss.

**Editor-in-chief** — not a reviewer but an orchestrator. Runs the full LangGraph: distributes papers, aggregates Round-1 reviews, identifies disagreements, initiates debate rounds, decides when debate has converged, synthesizes the final verdict. MCP clients: none directly. A2A role: moderator.

**Hallucination auditor** (stretch, Week 7) — audits every citation any reviewer made. MCP clients: Semantic Scholar. A2A role: none — this is a post-hoc auditor, not a debater.

# Part III: The MCP layer — every server in detail

Each server is a separate process that you write using the Python MCP SDK. Each exposes a JSON Schema describing its tools. Agents connect as clients.

**Paper-parser server**. Tools it exposes:

- `parse_pdf(path: str) -> PaperStructure` — returns a structured JSON: title, abstract, sections (with headings and body), references list, figure captions.
- `get_section(paper_id: str, section: str) -> str` — returns the raw text of a named section.
- `list_references(paper_id: str) -> list[Reference]` — returns references with title, authors, year, DOI if available.
- `get_figure_captions(paper_id: str) -> list[str]` — often useful for the methodology reviewer.

Implementation: wrap PyMuPDF (`fitz`). Cache the parsed structure by paper hash so repeated calls don't re-parse. This one server being MCP rather than an in-process import is what lets you swap in a better parser later (e.g., GROBID) without touching any agent code.

**Semantic Scholar server**. Tools:

- `search_related(title: str, abstract: str, k: int) -> list[Paper]` — returns k papers related to the given one.
- `get_citations(paper_id: str) -> list[Paper]` — papers that cite this one.
- `get_references(paper_id: str) -> list[Paper]` — papers this one cites.
- `verify_paper_exists(title: str, authors: list[str], year: int) -> VerificationResult` — critical for the hallucination auditor.
- `get_abstract(paper_id: str) -> str` — pulls the abstract for deeper comparison.

Implementation: thin wrappers around Semantic Scholar's public REST API. Rate-limit your requests (100 per 5 minutes unauthenticated is the limit) by batching in the server layer, not in the agent.

**RAG server**. Tool:

- `query_corpus(query: str, k: int, filters: dict) -> list[Chunk]` — semantic search over a pre-indexed ChromaDB collection.

Implementation: ChromaDB persistent client, `sentence-transformers/all-MiniLM-L6-v2` for embeddings, indexed over a corpus you assemble from a narrow domain (e.g., "papers on transformer efficiency 2019-2024"). Keep the corpus small and focused — 500-1000 papers is plenty. The Novelty reviewer uses this to surface papers Semantic Scholar's citation graph missed.

**Stats verifier server**. Tool:

- `run_python_sandbox(code: str, timeout: int) -> ExecutionResult` — executes short Python snippets in an isolated environment with numpy and scipy available.

Implementation: Python's `subprocess` with resource limits is the minimum viable; for safety, you'd ideally use a proper sandbox (Docker container, seccomp, or a service like E2B). For the project, a subprocess with 5-second timeout and no network is fine. The Methodology reviewer uses this to sanity-check things like "authors claim t(58) = 2.41, p < .01 — does scipy agree?"

Each of these servers is maybe 100–300 lines of Python. Collectively they're a couple of days of work.

# Part IV: The reviewer agents in detail

Each reviewer is a LangGraph node that wraps an LLM call with a persona prompt and MCP client access. Here's the structure in detail for one; the others follow the same pattern.

**Methodology reviewer — anatomy**

System prompt (abbreviated):

> You are a rigorous methodology reviewer for a top-tier ML conference. Your job is to evaluate experimental design, statistical validity, baseline reasonableness, and ablation completeness. You do not evaluate novelty or ethics — only methodological soundness. You have access to tools for reading the paper and for verifying statistical claims in a Python sandbox. When you report a concern, cite the specific section or figure. If you run sandbox code to verify a claim, include the code and result. Your output must conform to the ReviewSchema JSON format.

Available MCP clients: paper-parser, stats-verifier.

Output schema (this is the shared schema every reviewer conforms to):

```
{
  "reviewer_id": "methodology",
  "overall_score": float (1-10),
  "criterion_scores": {"rigor": float, "baselines": float, "ablations": float},
  "strengths": [str],
  "concerns": [
    {
      "claim": str,
      "evidence": str,   // section reference or sandbox output
      "severity": "minor" | "major" | "critical",
      "confidence": float
    }
  ],
  "summary": str (150 words max),
  "tool_calls": [ ... ]  // audit trail of every MCP call made
}
```

Workflow:

1. Editor passes the paper (via parsed structure from the paper-parser server) to this reviewer.
2. Reviewer reads methodology and experiments sections.
3. Reviewer identifies numerical claims worth verifying and invokes stats-verifier for each.
4. Reviewer writes concerns, anchoring each to evidence.
5. Returns the structured review.

The same structure applies to the other three reviewers. What differs is persona prompt, which MCP clients they use, and how they're expected to weight different criteria.

**Novelty reviewer specifics**: heavy use of Semantic Scholar and RAG. The prompt explicitly asks the model to produce three candidate prior works it thinks might exist, then verify each. This forces grounded retrieval rather than vibes-based novelty judgment.

**Devil's advocate specifics**: The prompt is the tricky one. You need to instruct the model to adopt an adversarial stance without descending into nitpicking. Something like: "Assume the authors have presented their work in the most favorable possible light. Your task is to identify the three strongest reasons a careful reader might reject this paper. Be specific and evidence-based; a weak objection is worse than no objection." The severity and confidence fields in its output are what keep it honest — it can't just say "everything is bad."

**Ethics reviewer specifics**: Its concerns section often has fewer items than the others, and that's fine. What you want is a small number of sharp observations, not a long list of generic ethical boilerplate. Prompt accordingly.

# Part V: A2A debate mechanics

Here's how a debate round actually works in code.

After Round 1, the editor has four structured reviews. It runs a **disagreement detection** step: for each pair of reviewers, compute the cosine similarity of their review embeddings, and additionally scan their concern lists for contradictions (same claim, opposite severity, for example). Disagreements above a threshold are flagged.

For each flagged disagreement, the editor constructs an **A2A message**. This message is structured:

```
{
  "round": 2,
  "from": "editor",
  "to": "methodology",
  "type": "rebuttal_request",
  "context": {
    "challenger": "devils_advocate",
    "challenger_claim": "The reported baselines are inadequate because...",
    "your_position": "You rated baselines 8/10.",
  },
  "request": "Respond specifically to the challenger's claim. Either update your position with justification or defend it with evidence."
}
```

The targeted reviewer receives this message, produces a response (which is itself an A2A message back to the editor), and the cycle repeats. The debate state — every A2A message, every response — is stored in the LangGraph's shared state object so every agent can see the full thread.

**Termination conditions**:

- Maximum three rounds. Hard cap.
- If the aggregate disagreement score drops below a threshold (reviewers have converged).
- If no reviewer changes position between two consecutive rounds (stalemate).

A stalemate is not a failure — the editor records unresolved claims as "contested" in the final verdict rather than forcing artificial consensus. That's one of the features of this system that matches real peer review.

One honest note: what I'm calling "A2A" here is conceptually A2A in the sense your course covers. Whether you implement it using Google's formal A2A protocol (which defines agent cards, tasks, artifacts) or using structured JSON messages passed through LangGraph shared state is a scope call. For a medium-commitment project, structured JSON in shared state is fine, and you frame it in the report as an A2A-style communication layer with clear message typing. If you want to go further, implementing Google's A2A protocol properly for a two-agent subset of your system is an impressive stretch goal.

# Part VI: The editor orchestrator

The editor is the most important piece of LangGraph code you'll write. It's a single node with multiple decision branches.

Its state object looks roughly like:

```python
class ReviewState(TypedDict):
    paper: ParsedPaper
    round: int
    reviews: dict[str, Review]        # reviewer_id -> latest review
    a2a_thread: list[A2AMessage]      # full debate history
    disagreements: list[Disagreement] # flagged for current round
    verdict: Optional[Verdict]
```

The editor's logic:

1. On first invocation, distribute the paper to all four reviewers in parallel (LangGraph fan-out). Wait for all four reviews.
2. Compute disagreements. If none above threshold, skip to synthesis.
3. Otherwise, send rebuttal requests to the relevant reviewers. Wait for responses.
4. Update reviews with responses, increment round counter, go back to step 2.
5. When debate terminates, synthesize. The synthesis step is itself an LLM call with a specialized prompt that takes the full A2A thread and produces a structured verdict.

Final verdict schema:

```
{
  "recommendation": "accept" | "reject" | "revise",
  "confidence": float,
  "per_criterion_scores": {...},
  "consensus_strengths": [str],
  "consensus_concerns": [Concern],
  "contested_claims": [
    {
      "claim": str,
      "positions": {"reviewer_id": "agreed" | "disagreed" | "neutral"},
      "editor_note": str
    }
  ],
  "suggested_revisions": [str]
}
```

The `contested_claims` field is a key part of what makes this project distinctive. Most automated review systems produce a single verdict. Yours produces a verdict *plus a transparent record of what the panel couldn't agree on* — which is more useful to an author than a false consensus.

# Part VII: Tech stack with rationale

**LangGraph over CrewAI**: You need explicit state and conditional flow control for the debate loop. CrewAI's hierarchical process is great when one agent delegates subtasks to specialists, but that's not this system — here agents argue as peers. LangGraph's state-machine model matches the actual dynamics.

**MCP Python SDK**: the official SDK from Anthropic. Lets you write each server as a Python file with decorated tool functions, and the SDK handles the protocol. Each reviewer connects as a client using the SDK's client API.

**LLM backends — mix, don't match**: Use Gemini 2.5 Flash for two reviewers, DeepSeek for one, Groq-hosted Llama 3.3 70B for one. All three have usable free tiers and different architectural biases. Heterogeneity of models is itself an experimental variable you report on. Be aware that different providers have different rate limits; your MCP call patterns will also vary in latency.

**ChromaDB persistent client**: Runs locally, stores to disk, no infrastructure. Fine up to tens of thousands of documents.

**sentence-transformers MiniLM-L6-v2**: 384-dim embeddings, runs on CPU in seconds per paper. Good enough for semantic retrieval at this scale.

**Semantic Scholar API**: free tier is generous, no key needed for moderate use. If you find yourself hitting limits, you can request a free API key from the Allen Institute.

**PyMuPDF**: Best Python PDF library for academic papers in my experience. GROBID is better for hard cases but requires running a Java server.

**Streamlit for demo UI**: file uploader, run button, a panel per reviewer showing their review, a debate thread visualization, a final verdict card. Maybe 150 lines of code total.

# Part VIII: Evaluation methodology in detail

Your evaluation is what the instructor will judge hardest, so treat it as a first-class deliverable.

**Ground truth dataset**. Pull 30-50 papers from OpenReview — a mix of accepted, rejected, and borderline from ICLR 2022-2024. For each paper, you have: the PDF, the human reviews, the meta-review, the final decision. This is your benchmark.

**Four metrics**:

*Verdict correlation*. Does your system's accept/reject recommendation match the actual outcome? Report accuracy, and break it down by confidence (does high-confidence verdict predict better?). A reasonable target: 65-70% accuracy on a balanced sample. Chance is 50%. Human inter-reviewer agreement is around 75%.

*Issue detection*. For each paper, take the list of concerns the human reviewers flagged (from their reviews) and check how many your system also flagged. Precision = (your concerns that overlap with human concerns) / (all your concerns). Recall = (human concerns you caught) / (all human concerns). This is done by manual comparison, not automated — so budget time for it. 20 papers thoroughly reviewed is more valuable than 50 skimmed.

*Inter-agent disagreement*. Compute cosine distance between all pairs of Round-1 review embeddings. Plot a distribution. If the mean pairwise distance is very low, your agents aren't actually disagreeing — your personas are too weak. If it's very high, they may be disagreeing about incompatible things rather than engaging. The healthy range is somewhere in between, and the exact shape is something to explore in the report.

*Citation hallucination rate*. For every paper reference any reviewer cites in any review, run verify_paper_exists through Semantic Scholar. Fraction that don't exist = hallucination rate. Report it before and after you add the hallucination auditor, so you can show the auditor actually helps.

**Ablation experiments** (at least two, to fill out the report's experiments section):

1. Single-model vs heterogeneous-model panel. Does using four Gemini instances produce less useful disagreement than a mix of Gemini + DeepSeek + Llama?
2. No debate vs 3-round debate. Does the debate phase actually change verdicts compared to naive aggregation of Round 1?

Either ablation alone is a paper-worthy result.

# Part IX: The 8-week timeline, expanded

**Week 1**: Install LangGraph, work through the official multi-agent-collaboration tutorial. Set up the project repo, pick your LLM providers, get API keys. Build a toy two-node LangGraph that reads a paper PDF and outputs "hello from node 2". Goal: be comfortable with LangGraph state flow.

**Week 2**: Implement the paper-parser MCP server. Implement one reviewer (start with Methodology — it's the least LLM-dependent). Run it end-to-end on three test papers. At the end of this week you have: one working MCP server, one working reviewer agent, and three sample reviews. Start drafting the introduction of the IEEE report.

**Week 3**: Implement the other three MCP servers (Semantic Scholar, RAG, stats-verifier). The Semantic Scholar one will take longest because of rate limit handling. Build a small ChromaDB index for the RAG server from ~200 papers in your chosen domain.

**Week 4**: Implement the other three reviewer agents. Connect each to its MCP clients. Implement the editor's Round-1 flow (distribute and aggregate naively). First complete end-to-end run: paper in, four independent reviews out, naive aggregated verdict. *Prepare a mid-project demo for yourself — record a 5-min walkthrough of the system running.* This is a fallback deliverable if later weeks go badly.

**Week 5**: Implement disagreement detection and the A2A message structure. Add Round 2 and Round 3 to the editor. Debug — the debate loop is where subtle bugs live (infinite loops, reviewers ignoring the rebuttal request, hallucinated responses). Test on 3-5 papers until the debate behaves reasonably.

**Week 6**: Implement the final verdict synthesis with the contested-claims structure. Build the Streamlit UI. Start the evaluation infrastructure: a script that runs your system on a list of papers and dumps results to JSON.

**Week 7**: Run the full evaluation on 30 OpenReview papers. Implement the hallucination auditor. Run ablations. This is the week where results generation happens.

**Week 8**: Write the IEEE report (this will take longer than you think — budget 3 full days). Polish the Streamlit demo. Prepare and rehearse the 15-minute presentation. Submit.

# Part X: Pitfalls and how to handle them

**Reviewers produce near-identical reviews**. You'll see this in Week 2-4. Causes: persona prompts too similar, same underlying LLM, no access to different tools. Fixes: sharper persona prompts with explicit differences in priorities; use different LLM providers; ensure each reviewer actually calls different MCP tools.

**The debate loops infinitely**. Always set a hard cap on rounds. Always log the state at each round during development.

**Hallucinated citations spoil results**. This is a real problem and not a bug in your system — it's a known LLM failure mode. Lean into it: your hallucination auditor is the fix, and reporting the rate is a contribution.

**Rate limits mid-evaluation**. Run evaluation overnight in batches. Cache MCP server responses so re-running a paper doesn't hit APIs again. Use multiple provider keys if you have them.

**PDF parsing fails on some papers**. Always have a fallback — if PyMuPDF returns garbage, skip the paper and note it in the evaluation. Don't let one bad PDF crash the pipeline.

**The report runs long**. 6-page IEEE format is tight. Cut ruthlessly: abstract, introduction, related work, architecture (with one diagram), experiments (with results tables), ethics, conclusion. Move implementation detail to an appendix or GitHub README.

**Presentation runs long**. 15 minutes is also tight. Script it: 2 min motivation, 4 min architecture, 6 min demo + results, 2 min ethics, 1 min conclusion. Rehearse twice with a timer.

# Concrete next steps

If you want to actually start Monday:

1. Create a new Python project with LangGraph, MCP SDK, PyMuPDF, ChromaDB, and requests installed.
2. Sign up for Gemini API (free tier), Groq (free tier), and optionally DeepSeek.
3. Work through LangGraph's "multi-agent collaboration" tutorial — expect 2-3 hours.
4. Pick a narrow paper domain for your RAG corpus (my suggestion: transformer efficiency / parameter-efficient fine-tuning — lots of papers, well-defined boundaries, relevant to the course).
5. Download 5 test papers from OpenReview to use throughout development.

Things I can produce for you next, any of which is roughly one message of detail:

- The full LangGraph state TypedDict, node function signatures, and graph construction code as a starting scaffold.
- Complete system prompts for all four reviewer agents, tuned for meaningful disagreement.
- The JSON Schema for each MCP server's tools, ready to paste into the Python MCP SDK.
- A detailed outline of the IEEE report section by section, so you can write it incrementally as you go.
- A sample evaluation script that takes an OpenReview paper, runs your system, and emits the four metrics.

Which one would actually save you the most time? I'd pick the scaffold plus system prompts — those are where most students waste a week.

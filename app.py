"""Streamlit demo for the multi-agent peer review panel.

    streamlit run app.py

Upload a paper (PDF), the system runs the four reviewers + editor + debate loop
+ synthesis, and renders each reviewer's review, the A2A debate thread, and
the final verdict with contested_claims.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path

import streamlit as st

from src.config import Config
from src.orchestrator.graph import build_debate_graph, initial_state
from src.schemas import A2AMessage, Review, Verdict

st.set_page_config(page_title="MCP+A2A Peer Review", page_icon=":book:", layout="wide")


# ---------- session state ----------

if "result" not in st.session_state:
    st.session_state.result = None
if "paper_name" not in st.session_state:
    st.session_state.paper_name = None
if "run_seconds" not in st.session_state:
    st.session_state.run_seconds = None


# ---------- sidebar ----------

with st.sidebar:
    st.title("MCP + A2A Peer Review")
    st.caption(
        "Four heterogeneous LLM reviewers argue about a paper, and the editor "
        "synthesizes a verdict that preserves contested claims."
    )
    st.divider()
    st.markdown("### Settings")
    st.write(f"**Max debate rounds:** {Config.MAX_DEBATE_ROUNDS}")
    st.write(f"**Disagreement threshold:** {Config.DISAGREEMENT_THRESHOLD}")
    st.divider()
    st.markdown("### Reviewer panel")
    st.write("- **methodology** (Gemini 2.5 Flash-Lite)")
    st.write("- **novelty** (OpenRouter Nemotron 120B)")
    st.write("- **devils_advocate** (Groq Llama 3.3 70B)")
    st.write("- **ethics** (Groq Llama 3.3 70B)")
    st.divider()
    uploaded = st.file_uploader("Upload a paper (PDF)", type=["pdf"])
    run_btn = st.button("Run review", disabled=uploaded is None, use_container_width=True)


# ---------- main ----------

def _save_upload_to_temp(uploaded_file) -> Path:
    suffix = Path(uploaded_file.name).suffix
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=str(Config.PAPER_CACHE_DIR.parent / "papers"))
    tmp.write(uploaded_file.getbuffer())
    tmp.close()
    return Path(tmp.name)


async def _run_graph(pdf_path: Path):
    graph = build_debate_graph()
    return await graph.ainvoke(
        initial_state(str(pdf_path)),
        {"recursion_limit": 50},
    )


def _render_review(review: Review) -> None:
    cols = st.columns([1, 1])
    with cols[0]:
        st.metric("Overall score", f"{review.overall_score:.1f} / 10")
    with cols[1]:
        st.metric("Concerns", len(review.concerns))

    if review.criterion_scores:
        st.markdown("**Per-criterion scores:**")
        cs = review.criterion_scores
        st.json(cs, expanded=False)

    if review.summary:
        st.markdown("**Summary**")
        st.write(review.summary)

    st.markdown(f"**Strengths** ({len(review.strengths)})")
    for s in review.strengths:
        st.markdown(f"- {s}")

    st.markdown(f"**Concerns** ({len(review.concerns)})")
    for c in review.concerns:
        color = {"critical": "red", "major": "orange", "minor": "blue"}.get(c.severity, "gray")
        st.markdown(
            f"- :{color}[**[{c.severity} / conf {c.confidence:.2f}]**] {c.claim}\n\n"
            f"  &nbsp;&nbsp;*evidence:* {c.evidence}"
        )

    if review.tool_calls:
        with st.expander(f"MCP tool-call audit trail ({len(review.tool_calls)} calls)"):
            for tc in review.tool_calls:
                st.code(
                    f"{tc.server}.{tc.tool}({list(tc.args.keys())})\n-> {tc.result_summary[:200]}",
                    language="text",
                )


def _render_debate_thread(thread: list[A2AMessage]) -> None:
    if not thread:
        st.info("No debate round ran — the panel converged after Round 1.")
        return

    rounds = sorted({m.round for m in thread})
    for r in rounds:
        with st.expander(f"Round {r}  ({sum(1 for m in thread if m.round == r)} messages)", expanded=True):
            for m in [m for m in thread if m.round == r]:
                if m.type == "rebuttal_request":
                    ctx = m.context
                    st.markdown(
                        f"**Editor to {m.recipient}** — challenger `{ctx.get('challenger')}` "
                        f"(score {ctx.get('challenger_score')}) disputes your score of "
                        f"{ctx.get('your_prior_score')}"
                    )
                    st.caption(m.request or "")
                elif m.type == "rebuttal_response":
                    ctx = m.context
                    before, after = ctx.get("prior_score"), ctx.get("new_score")
                    if after > before:
                        icon = ":green[position moved UP]"
                    elif after < before:
                        icon = ":red[position moved DOWN]"
                    else:
                        icon = ":gray[position HELD]"
                    st.markdown(f"**{m.sender} responded**  —  score {before} -> {after}  {icon}")
                    if m.body:
                        st.write(m.body)
                elif m.type == "announcement":
                    st.caption(f"**announcement:** {m.body}")


def _render_verdict(verdict: Verdict, disagreements) -> None:
    rec = verdict.recommendation
    rec_color = {"accept": "green", "revise": "orange", "reject": "red"}[rec]
    st.markdown(f"## Final verdict: :{rec_color}[**{rec.upper()}**]")
    st.progress(verdict.confidence, text=f"Confidence {verdict.confidence:.2f}")

    if verdict.summary:
        st.info(verdict.summary)

    cols = st.columns(3)
    with cols[0]:
        st.metric("Consensus strengths", len(verdict.consensus_strengths))
    with cols[1]:
        st.metric("Consensus concerns", len(verdict.consensus_concerns))
    with cols[2]:
        st.metric("Contested claims", len(verdict.contested_claims), delta=None,
                  help="Claims where the panel disagreed and debate did not resolve it")

    if verdict.contested_claims:
        st.markdown("### :red[Contested claims]")
        st.caption("Claims where the panel disagreed and debate did not reach consensus.")
        for cc in verdict.contested_claims:
            with st.container(border=True):
                st.markdown(f"**{cc.claim}**")
                pos_cols = st.columns(len(cc.positions) or 1)
                for i, (reviewer, pos) in enumerate(cc.positions.items()):
                    icon = {"agreed": "✓", "disagreed": "✗", "neutral": "—"}.get(pos, "?")
                    pos_color = {"agreed": "green", "disagreed": "red", "neutral": "gray"}.get(pos, "gray")
                    with pos_cols[i % len(pos_cols)]:
                        st.markdown(f":{pos_color}[**{reviewer}**] {icon} {pos}")
                if cc.editor_note:
                    st.caption(f"Editor: {cc.editor_note}")

    if verdict.consensus_concerns:
        with st.expander(f"Consensus concerns ({len(verdict.consensus_concerns)})"):
            for c in verdict.consensus_concerns:
                st.markdown(
                    f"- **[{c.severity} / conf {c.confidence:.2f}]** {c.claim}\n\n"
                    f"  *evidence:* {c.evidence}"
                )

    if verdict.consensus_strengths:
        with st.expander(f"Consensus strengths ({len(verdict.consensus_strengths)})"):
            for s in verdict.consensus_strengths:
                st.markdown(f"- {s}")

    if verdict.suggested_revisions:
        with st.expander(f"Suggested revisions ({len(verdict.suggested_revisions)})"):
            for r in verdict.suggested_revisions:
                st.markdown(f"- {r}")

    with st.expander("Pairwise disagreement matrix"):
        for d in sorted(disagreements, key=lambda x: -x.combined_score):
            flag = " :red[*]" if d.flagged else ""
            st.markdown(
                f"`{d.reviewer_a:<18} vs {d.reviewer_b:<18}` "
                f"spread={d.score_spread:.2f} cos_dist={d.summary_cosine_distance:.3f} "
                f"combined={d.combined_score:.3f}{flag}"
            )


# ---------- run action ----------

if run_btn and uploaded is not None:
    pdf_path = _save_upload_to_temp(uploaded)
    st.session_state.paper_name = uploaded.name
    with st.status(
        f"Running multi-agent review on {uploaded.name}... this takes a few minutes",
        expanded=True,
    ) as status:
        status.write("Starting LangGraph: 4 reviewers in parallel + editor + debate loop + synthesis")
        t0 = time.time()
        try:
            final = asyncio.run(_run_graph(pdf_path))
            elapsed = time.time() - t0
            st.session_state.result = final
            st.session_state.run_seconds = elapsed
            status.update(label=f"Review complete in {elapsed:.0f}s", state="complete", expanded=False)
        except Exception as e:
            status.update(label=f"Failed: {e}", state="error")
            st.exception(e)
            st.stop()


# ---------- display ----------

if st.session_state.result is None:
    st.title("Multi-agent peer review")
    st.write(
        "Upload a research paper PDF on the left and click **Run review**. The system "
        "will run four independent reviewers in parallel (methodology, novelty, devil's "
        "advocate, ethics), run up to three rounds of structured debate when they "
        "disagree, and produce a verdict that preserves *contested claims* rather than "
        "forcing a false consensus."
    )
    st.caption(
        "MCP = Model Context Protocol (agents → tools). "
        "A2A = Agent-to-Agent (reviewers → each other). "
        "The whole point of the project is keeping these two protocols cleanly separated."
    )
else:
    final = st.session_state.result
    paper_name = st.session_state.paper_name
    elapsed = st.session_state.run_seconds

    reviews: dict[str, Review] = final.get("reviews", {})
    thread: list[A2AMessage] = final.get("a2a_thread", [])
    disagreements = final.get("disagreements", [])
    verdict: Verdict = final.get("verdict")

    st.title(f"Review of: {paper_name}")
    st.caption(
        f"Completed in {elapsed:.0f}s  |  {final.get('round', 1)} round(s)  |  "
        f"{len(thread)} A2A messages"
    )

    if verdict:
        _render_verdict(verdict, disagreements)

    st.divider()
    st.markdown("## A2A debate thread")
    _render_debate_thread(thread)

    st.divider()
    st.markdown("## Individual reviewer final positions")
    reviewer_tabs = st.tabs(list(reviews.keys()))
    for tab, rid in zip(reviewer_tabs, reviews.keys()):
        with tab:
            _render_review(reviews[rid])

"""LangGraph wiring for the review system.

Phase 6 graph (Round-1 fan-out + debate loop + synthesis):

                     START
                       │
        ┌──────────┬───┴────┬──────────────┐
        │          │        │              │
   methodology  novelty  devils_adv    ethics      (run in parallel)
        │          │        │              │
        └──────────┴───┬────┴──────────────┘
                       │
                     editor ◄──────────────┐
                       │                    │
              (conditional edge)            │
                ┌──────┴──────┐             │
                │             │             │
                ▼             ▼             │
          synthesis      debate_round ──────┘
                │             (increments round,
                ▼              updates reviews,
                END            appends to a2a_thread)

Conditional routing is decided by `should_continue_debate` in editor.py:
  - if flagged disagreements exist AND round < MAX_DEBATE_ROUNDS AND not stalemate → debate_round
  - otherwise → synthesis

The debate_round node only re-runs reviewers who have pending rebuttal_requests,
keeping cost bounded.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.agents.debate_round import debate_round_node
from src.agents.devils_advocate_reviewer import DevilsAdvocateReviewer
from src.agents.editor import editor_node, should_continue_debate
from src.agents.ethics_reviewer import EthicsReviewer
from src.agents.methodology_reviewer import MethodologyReviewer
from src.agents.novelty_reviewer import NoveltyReviewer
from src.agents.synthesis import synthesis_node
from src.logging_setup import get_logger
from src.orchestrator.state import ReviewState

log = get_logger(__name__)


def _make_reviewer_node(reviewer_cls):
    async def node(state: ReviewState) -> dict:
        reviewer = reviewer_cls()
        review = await reviewer.review(state["paper_path"])
        log.info(f"[graph] {reviewer.reviewer_id} committed to state")
        return {"reviews": {reviewer.reviewer_id: review}}

    node.__name__ = f"{reviewer_cls.__name__}_node"
    return node


methodology_node = _make_reviewer_node(MethodologyReviewer)
novelty_node = _make_reviewer_node(NoveltyReviewer)
devils_advocate_node = _make_reviewer_node(DevilsAdvocateReviewer)
ethics_node = _make_reviewer_node(EthicsReviewer)


def build_round1_graph():
    """Phase 5 graph: parallel fan-out → editor → END. No debate."""
    g = StateGraph(ReviewState)
    g.add_node("methodology", methodology_node)
    g.add_node("novelty", novelty_node)
    g.add_node("devils_advocate", devils_advocate_node)
    g.add_node("ethics", ethics_node)
    g.add_node("editor", editor_node)

    g.add_edge(START, "methodology")
    g.add_edge(START, "novelty")
    g.add_edge(START, "devils_advocate")
    g.add_edge(START, "ethics")
    g.add_edge("methodology", "editor")
    g.add_edge("novelty", "editor")
    g.add_edge("devils_advocate", "editor")
    g.add_edge("ethics", "editor")
    g.add_edge("editor", END)
    return g.compile()


def build_debate_graph():
    """Phase 6 graph: Round-1 fan-out + debate loop + synthesis."""
    g = StateGraph(ReviewState)

    g.add_node("methodology", methodology_node)
    g.add_node("novelty", novelty_node)
    g.add_node("devils_advocate", devils_advocate_node)
    g.add_node("ethics", ethics_node)
    g.add_node("editor", editor_node)
    g.add_node("debate_round", debate_round_node)
    g.add_node("synthesis", synthesis_node)

    # Round 1: parallel fan-out
    g.add_edge(START, "methodology")
    g.add_edge(START, "novelty")
    g.add_edge(START, "devils_advocate")
    g.add_edge(START, "ethics")

    # All reviewers fan in to editor
    g.add_edge("methodology", "editor")
    g.add_edge("novelty", "editor")
    g.add_edge("devils_advocate", "editor")
    g.add_edge("ethics", "editor")

    # Conditional edge from editor:
    #   debate not needed → synthesis
    #   debate needed     → debate_round (which loops back to editor)
    g.add_conditional_edges(
        "editor",
        should_continue_debate,
        {
            "debate_round": "debate_round",
            "synthesis": "synthesis",
        },
    )

    # debate_round always returns to editor for another round of checking
    g.add_edge("debate_round", "editor")

    g.add_edge("synthesis", END)
    return g.compile()


def initial_state(paper_path: str) -> ReviewState:
    return {
        "paper_path": paper_path,
        "round": 1,
        "reviews": {},
        "a2a_thread": [],
        "disagreements": [],
        "verdict": None,
    }

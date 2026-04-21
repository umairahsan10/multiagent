"""LangGraph wiring for the review system.

Phase 5 graph (Round-1 only, no debate):

                     START
                       │
        ┌──────────┬───┴────┬──────────────┐
        │          │        │              │
   methodology  novelty  devils_adv    ethics      (run in parallel)
        │          │        │              │
        └──────────┴───┬────┴──────────────┘
                       │
                     editor                        (fan-in, compute disagreements)
                       │
                      END

All edges from START to each reviewer are parallel: LangGraph schedules them
concurrently. State merges via the reducer on the `reviews` key (see state.py).
The editor runs after all four reviewers have committed their reviews.

Phase 6 will extend this with debate rounds (conditional edge from editor back
to reviewers if disagreements warrant a rebuttal).
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from src.agents.devils_advocate_reviewer import DevilsAdvocateReviewer
from src.agents.editor import editor_node
from src.agents.ethics_reviewer import EthicsReviewer
from src.agents.methodology_reviewer import MethodologyReviewer
from src.agents.novelty_reviewer import NoveltyReviewer
from src.logging_setup import get_logger
from src.orchestrator.state import ReviewState

log = get_logger(__name__)


def _make_reviewer_node(reviewer_cls):
    """Factory: wrap a BaseReviewer subclass as a LangGraph async node.

    The reviewer instance is created per invocation (stateless — cheap) so
    multiple graph runs don't share LLM rate-limit state unnecessarily.
    """
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
    """Phase 5 graph: parallel Round-1 fan-out → editor aggregation → done."""
    g = StateGraph(ReviewState)

    g.add_node("methodology", methodology_node)
    g.add_node("novelty", novelty_node)
    g.add_node("devils_advocate", devils_advocate_node)
    g.add_node("ethics", ethics_node)
    g.add_node("editor", editor_node)

    # Fan-out: START -> each reviewer (parallel)
    g.add_edge(START, "methodology")
    g.add_edge(START, "novelty")
    g.add_edge(START, "devils_advocate")
    g.add_edge(START, "ethics")

    # Fan-in: each reviewer -> editor
    g.add_edge("methodology", "editor")
    g.add_edge("novelty", "editor")
    g.add_edge("devils_advocate", "editor")
    g.add_edge("ethics", "editor")

    g.add_edge("editor", END)

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

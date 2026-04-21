"""Shared LangGraph state for the review system.

Every node reads this state and returns a partial update. LangGraph merges the
update into state using per-key reducers declared via `Annotated[T, reducer]`.

Parallel reviewers all write to `reviews` concurrently, so that field uses a
dict-merge reducer.
"""

from __future__ import annotations

from typing import Annotated, Optional, TypedDict

from src.schemas import A2AMessage, Disagreement, Review, Verdict


def merge_reviews(a: dict[str, Review], b: dict[str, Review]) -> dict[str, Review]:
    """Reducer for the `reviews` field. Later writes win on key conflict,
    which is what we want — in Round 2+, a reviewer's updated review
    should replace its Round 1 review."""
    return {**a, **b}


def extend_a2a(a: list[A2AMessage], b: list[A2AMessage]) -> list[A2AMessage]:
    """Reducer for the debate thread. Append-only."""
    return [*a, *b]


class ReviewState(TypedDict, total=False):
    paper_path: str
    round: int
    reviews: Annotated[dict[str, Review], merge_reviews]
    a2a_thread: Annotated[list[A2AMessage], extend_a2a]
    disagreements: list[Disagreement]
    verdict: Optional[Verdict]

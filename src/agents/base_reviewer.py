"""Base reviewer.

A reviewer:
1. Opens MCP sessions to its assigned servers (declared by subclass).
2. Builds a `paper_context` string from MCP tool calls (subclass decides which).
3. Asks its LLM (provider chosen by role in `llm_factory`) to produce a structured `Review`.
4. Returns the `Review` plus a tool-call audit trail.

Structured output: rather than asking the LLM for free-form text and parsing it,
we use LangChain's `with_structured_output(Review)` which forces the model to
emit a Pydantic-conformant JSON object. Way more reliable than regex-parsing.
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from mcp import ClientSession
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

_TRANSIENT_MARKERS = ("429", "resource_exhausted", "503", "unavailable", "timeout", "rate_limit", "overloaded")


def _is_transient(exc: BaseException) -> bool:
    msg = str(exc).lower()
    if any(m in msg for m in _TRANSIENT_MARKERS):
        return True
    # Some providers (notably OpenRouter free-tier) intermittently return a
    # response with `choices=None`, which the OpenAI client surfaces as
    # "TypeError: 'NoneType' object is not iterable" inside parse_chat_completion.
    # Treat that as transient.
    if isinstance(exc, TypeError) and "iterable" in msg:
        return True
    return False

from src.agents.llm_factory import Role, make_llm
from src.clients.mcp_client import mcp_session, unwrap_tool_result
from src.logging_setup import get_logger
from src.schemas import A2AMessage, RebuttalOutcome, Review, ReviewerId, ToolCall

log = get_logger(__name__)


class BaseReviewer(ABC):
    """Subclass contract:
        reviewer_id : matches the ReviewerId literal in schemas.py
        role        : matches a key in llm_factory.ROLE_TO_PROVIDER
        mcp_servers : list of server names from clients.mcp_client.SERVER_MODULES
        system_prompt: persona prompt; instruct the model to emit JSON conforming to Review.

    Subclass implements `gather_context(sessions, paper_path)` which returns:
        (context_str, tool_calls)
    """

    reviewer_id: ReviewerId
    role: Role
    mcp_servers: list[str]
    system_prompt: str

    def __init__(self, temperature: float = 0.3) -> None:
        self.llm = make_llm(self.role, temperature=temperature)
        self.structured_llm = self.llm.with_structured_output(Review)
        self.rebuttal_llm = self.llm.with_structured_output(RebuttalOutcome)

    @abstractmethod
    async def gather_context(
        self,
        sessions: dict[str, ClientSession],
        paper_path: str,
    ) -> tuple[str, list[ToolCall]]:
        """Return (formatted paper context for the LLM, audit trail of MCP calls)."""

    async def review(self, paper_path: str) -> Review:
        async with _open_sessions(self.mcp_servers) as sessions:
            context, tool_calls = await self.gather_context(sessions, paper_path)

        log.info(f"[{self.reviewer_id}] gathered context ({len(context):,} chars, {len(tool_calls)} tool calls)")

        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=self._build_user_prompt(context)),
        ]
        result = await self._invoke_llm_with_retry(messages)
        result.reviewer_id = self.reviewer_id
        result.tool_calls = tool_calls
        log.info(f"[{self.reviewer_id}] score={result.overall_score} concerns={len(result.concerns)}")
        return result

    async def respond_to_rebuttals(
        self,
        prior_review: Review,
        challenges: list[A2AMessage],
        paper_path: str,
    ) -> tuple[Review, str]:
        """Respond to all rebuttal_request messages addressed to this reviewer in one round.

        Returns:
            (updated_review, rationale) — updated_review is the reviewer's new position
            after considering the challenges; rationale explains what changed.

        Policy:
        - A reviewer MAY stand firm. Capitulation without evidence is discouraged in the prompt.
        - `position_changed` is True only if the reviewer explicitly says so AND the score changed
          by >= 0.5 or concerns were added/removed/re-graded.
        """
        from src.orchestrator.a2a import format_challenges_for_reviewer  # late import to avoid cycle

        # Re-gather paper context so the reviewer can cite specific sections if defending.
        async with _open_sessions(self.mcp_servers) as sessions:
            paper_context, new_tool_calls = await self.gather_context(sessions, paper_path)

        challenges_block = format_challenges_for_reviewer(challenges)

        prompt_text = f"""You are the '{self.reviewer_id}' reviewer. You submitted a review earlier. Now you have received rebuttal requests from other reviewers who disagree with your position. You must decide, per challenge, whether to update your position or defend it.

YOUR PRIOR REVIEW:
  overall_score: {prior_review.overall_score}
  summary: {prior_review.summary}
  concerns:
{chr(10).join(f'    - [{c.severity}/{c.confidence:.2f}] {c.claim}' for c in prior_review.concerns)}
  strengths:
{chr(10).join(f'    + {s}' for s in prior_review.strengths[:5])}

CHALLENGES AGAINST YOUR POSITION THIS ROUND:
{challenges_block}

=== PAPER CONTEXT (abbreviated — you saw the full paper in Round 1) ===
{paper_context[:8000]}
=== END PAPER CONTEXT ===

INSTRUCTIONS:
1. Read every challenge. Identify which specific claims (if any) you accept,
   partially accept, or reject.
2. Produce an updated Review object reflecting your new position. If you concede
   a concern, remove it or downgrade its severity. If you find you were wrong
   on your score, change it. If the challenge is unpersuasive, keep your position
   and explain why in the rationale.
3. Set `position_changed` = true only if your overall_score moved by at least 0.5
   OR you added/removed concerns OR you changed a concern's severity.
4. The `rationale` field should be a short (2-4 sentence) explanation addressed
   to the editor: what you updated and why, or why you declined to update.
5. Do NOT capitulate just because you were challenged — weak concessions are worse
   than honest disagreement. The system is designed to record contested claims.

Your response MUST conform to the RebuttalOutcome schema.
"""

        messages = [HumanMessage(content=prompt_text)]
        outcome: RebuttalOutcome = await self._invoke_rebuttal_with_retry(messages)

        updated = outcome.updated_review
        updated.reviewer_id = self.reviewer_id
        # Append new tool_calls made during rebuttal to the audit trail
        updated.tool_calls = [*prior_review.tool_calls, *new_tool_calls]
        log.info(
            f"[{self.reviewer_id}] rebuttal: score {prior_review.overall_score:.1f} -> {updated.overall_score:.1f}, "
            f"position_changed={outcome.position_changed}, concerns {len(prior_review.concerns)}->{len(updated.concerns)}"
        )
        return updated, outcome.rationale

    async def _invoke_rebuttal_with_retry(self, messages: list[Any]) -> RebuttalOutcome:
        attempt_idx = 0
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            retry=retry_if_exception(_is_transient),
            reraise=True,
        ):
            attempt_idx += 1
            with attempt:
                if attempt_idx > 1:
                    log.warning(f"[{self.reviewer_id}] rebuttal LLM retry attempt {attempt_idx}")
                return await self.rebuttal_llm.ainvoke(messages)
        raise RuntimeError("unreachable")

    async def _invoke_llm_with_retry(self, messages: list[Any]) -> Review:
        # Retry on transient provider errors: 429 rate-limit, 5xx upstream unavailability.
        # Exponential backoff starting 2s, up to 4 attempts (~24s cumulative worst-case).
        # Non-transient errors (validation, auth) fail immediately.
        attempt_idx = 0
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=2, min=2, max=30),
            retry=retry_if_exception(_is_transient),
            reraise=True,
        ):
            attempt_idx += 1
            with attempt:
                if attempt_idx > 1:
                    log.warning(f"[{self.reviewer_id}] LLM retry attempt {attempt_idx}")
                return await self.structured_llm.ainvoke(messages)
        raise RuntimeError("unreachable")

    def _build_user_prompt(self, context: str) -> str:
        return (
            "You are reviewing the following paper. Produce your review as a JSON object "
            "conforming to the Review schema. Be specific and evidence-based. Anchor every "
            "concern to a section reference. Score on a 1-10 scale where 5 is borderline.\n\n"
            "=== PAPER CONTEXT ===\n"
            f"{context}\n"
            "=== END PAPER CONTEXT ==="
        )


class _SessionPool:
    """Async context manager that opens multiple MCP sessions in parallel and
    closes them all on exit."""

    def __init__(self, server_names: list[str]) -> None:
        self.server_names = server_names
        self._stack: list[Any] = []
        self.sessions: dict[str, ClientSession] = {}

    async def __aenter__(self) -> dict[str, ClientSession]:
        for name in self.server_names:
            cm = mcp_session(name)
            session = await cm.__aenter__()
            self._stack.append((cm, session))
            self.sessions[name] = session
        return self.sessions

    async def __aexit__(self, exc_type, exc, tb) -> None:
        while self._stack:
            cm, _ = self._stack.pop()
            try:
                await cm.__aexit__(exc_type, exc, tb)
            except Exception as e:
                log.warning(f"error closing MCP session: {e}")


def _open_sessions(server_names: list[str]) -> _SessionPool:
    return _SessionPool(server_names)


async def call_tool(
    session: ClientSession,
    server_name: str,
    tool: str,
    args: dict,
    audit: list[ToolCall],
) -> Any:
    """Call an MCP tool and append a ToolCall record to `audit`."""
    raw = await session.call_tool(tool, args)
    value = unwrap_tool_result(raw)
    summary = json.dumps(value)[:200] if not isinstance(value, str) else value[:200]
    audit.append(ToolCall(server=server_name, tool=tool, args=args, result_summary=summary))
    return value

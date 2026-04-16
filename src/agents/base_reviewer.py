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

import json
from abc import ABC, abstractmethod
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from mcp import ClientSession

from src.agents.llm_factory import Role, make_llm
from src.clients.mcp_client import mcp_session, unwrap_tool_result
from src.logging_setup import get_logger
from src.schemas import Review, ReviewerId, ToolCall

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
        result: Review = await self.structured_llm.ainvoke(messages)
        result.reviewer_id = self.reviewer_id
        result.tool_calls = tool_calls
        log.info(f"[{self.reviewer_id}] score={result.overall_score} concerns={len(result.concerns)}")
        return result

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

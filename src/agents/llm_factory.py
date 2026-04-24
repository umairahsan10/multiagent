"""Single place to construct LLM clients.

Reviewers ask for an LLM by *role* (e.g. "methodology"), not by provider, so the
heterogeneous-panel ablation is a one-line change in `ROLE_TO_PROVIDER`.
"""

from __future__ import annotations

from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI

from src.config import Config

Provider = Literal["gemini", "groq", "openrouter"]
Role = Literal["methodology", "novelty", "devils_advocate", "ethics", "editor"]

ROLE_TO_PROVIDER: dict[Role, Provider] = {
    "methodology": "gemini",
    "novelty": "openrouter",
    "devils_advocate": "groq",
    "ethics": "groq",       # moved off Gemini: free tier is 5 req/min, clashes with methodology
    "editor": "gemini",      # synthesis needs reliable nested structured output; Groq Llama flattens lists-of-objects
}


def make_llm(role: Role, temperature: float = 0.3) -> BaseChatModel:
    provider = ROLE_TO_PROVIDER[role]
    if provider == "gemini":
        return ChatGoogleGenerativeAI(
            model=Config.GEMINI_MODEL,
            api_key=Config.GOOGLE_API_KEY,
            temperature=temperature,
        )
    if provider == "groq":
        return ChatGroq(
            model=Config.GROQ_MODEL,
            api_key=Config.GROQ_API_KEY,
            temperature=temperature,
        )
    if provider == "openrouter":
        # gpt-oss-120b uses reasoning tokens, so give structured output extra headroom
        return ChatOpenAI(
            model=Config.OPENROUTER_MODEL,
            api_key=Config.OPENROUTER_API_KEY,
            base_url=Config.OPENROUTER_BASE_URL,
            temperature=temperature,
            max_completion_tokens=16384,
        )
    raise ValueError(f"unknown provider: {provider}")

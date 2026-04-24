from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class Config:
    # LLM providers
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    OPENALEX_EMAIL = os.getenv("OPENALEX_EMAIL", "")
    SEMANTIC_SCHOLAR_API_KEY = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")

    OPENALEX_BASE_URL = "https://api.openalex.org"

    OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

    # Paths
    DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
    CHROMA_DIR = Path(os.getenv("CHROMA_DIR", "./data/chroma"))
    PAPER_CACHE_DIR = Path(os.getenv("PAPER_CACHE_DIR", "./data/papers"))

    # Debate
    MAX_DEBATE_ROUNDS = int(os.getenv("MAX_DEBATE_ROUNDS", "3"))
    DISAGREEMENT_THRESHOLD = float(os.getenv("DISAGREEMENT_THRESHOLD", "0.25"))

    # Sandbox
    SANDBOX_TIMEOUT = int(os.getenv("SANDBOX_TIMEOUT", "15"))

    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    # Model IDs — centralized so the heterogeneous-panel ablation is one-line swap
    GEMINI_MODEL = "gemini-2.5-flash-lite"  # flash (5 RPM, 20/day free) → flash-lite (15 RPM, 1000/day free)
    OPENROUTER_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"  # was gpt-oss-120b; emitted <|endoftext|> mid-structured-output
    GROQ_MODEL = "llama-3.3-70b-versatile"

    @classmethod
    def ensure_dirs(cls) -> None:
        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)
        cls.CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        cls.PAPER_CACHE_DIR.mkdir(parents=True, exist_ok=True)


Config.ensure_dirs()

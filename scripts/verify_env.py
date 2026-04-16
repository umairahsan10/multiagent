"""Run this first after `pip install -r requirements.txt` and copying .env.example -> .env.

It checks which API keys are set and pings each provider with a cheap call so you
know your keys work before you touch the rest of the system.

    python scripts/verify_env.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import Config  # noqa: E402


def check_key(name: str, value: str) -> bool:
    status = "OK " if value else "MISSING"
    print(f"  [{status}] {name}")
    return bool(value)


def main() -> int:
    print("=== API key presence ===")
    google = check_key("GOOGLE_API_KEY", Config.GOOGLE_API_KEY)
    openrouter = check_key("OPENROUTER_API_KEY", Config.OPENROUTER_API_KEY)
    groq = check_key("GROQ_API_KEY", Config.GROQ_API_KEY)
    check_key("SEMANTIC_SCHOLAR_API_KEY (optional)", Config.SEMANTIC_SCHOLAR_API_KEY)

    if not (google and openrouter and groq):
        print("\nAt least one primary LLM key is missing. Fill in .env before proceeding.")
        return 1

    print("\n=== Ping LLM providers ===")
    errors = 0

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI

        llm = ChatGoogleGenerativeAI(model=Config.GEMINI_MODEL, api_key=Config.GOOGLE_API_KEY)
        r = llm.invoke("Say 'pong' and nothing else.")
        print(f"  [OK ] Gemini ({Config.GEMINI_MODEL}): {r.content[:40]!r}")
    except Exception as e:
        print(f"  [ERR] Gemini: {e}")
        errors += 1

    try:
        from langchain_groq import ChatGroq

        llm = ChatGroq(model=Config.GROQ_MODEL, api_key=Config.GROQ_API_KEY)
        r = llm.invoke("Say 'pong' and nothing else.")
        print(f"  [OK ] Groq ({Config.GROQ_MODEL}): {r.content[:40]!r}")
    except Exception as e:
        print(f"  [ERR] Groq: {e}")
        errors += 1

    try:
        from openai import OpenAI

        client = OpenAI(api_key=Config.OPENROUTER_API_KEY, base_url=Config.OPENROUTER_BASE_URL)
        r = client.chat.completions.create(
            model=Config.OPENROUTER_MODEL,
            messages=[{"role": "user", "content": "Say 'pong' and nothing else."}],
            max_tokens=10,
        )
        print(f"  [OK ] OpenRouter ({Config.OPENROUTER_MODEL}): {r.choices[0].message.content[:40]!r}")
    except Exception as e:
        print(f"  [ERR] OpenRouter: {e}")
        errors += 1

    print(f"\n{'All good.' if errors == 0 else f'{errors} provider(s) failed.'}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

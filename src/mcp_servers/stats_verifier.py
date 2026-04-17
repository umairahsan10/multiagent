"""Stats-verifier MCP server.

Exposes a Python sandbox the Methodology reviewer uses to sanity-check numerical
claims in a paper (e.g. "authors say t(58)=2.41, p<.01 — does scipy agree?").

Sandbox strategy: spawn a fresh `python -I -` subprocess with a configurable
timeout, feed it the user code on stdin, and capture stdout/stderr. This is the
minimum-viable sandbox — it relies on subprocess isolation + timeout, not kernel-level
sandboxing. Good enough for a class project; for production use E2B or Docker.

Safety measures:
- `-I` = isolated mode (ignores PYTHONPATH, doesn't add cwd to sys.path)
- Short timeout (default 5s, configurable via SANDBOX_TIMEOUT env var)
- No network access is not enforced — so do NOT point this at untrusted LLM
  output that could include `urllib.request.urlopen(...)`. LLM-generated stats
  code is low-risk here, but consider it.
- Preamble imports numpy + scipy.stats so models can write `scipy.stats.ttest_ind(...)`
  without boilerplate.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

from mcp.server.fastmcp import FastMCP
from scipy import stats as _scipy_stats  # eager import: lazy load deadlocks the asyncio loop on first tool call

from src.config import Config

mcp = FastMCP("stats-verifier")

PREAMBLE = textwrap.dedent("""
    import math
    import numpy as np
""")


@mcp.tool()
def run_python_sandbox(code: str, timeout: int | None = None) -> dict:
    """Execute short Python snippet in an isolated subprocess and return result.

    numpy and scipy.stats are pre-imported. Use `print(...)` to surface values.

    Args:
        code: Python source. Do not include long-running loops or network calls.
        timeout: seconds before the subprocess is killed (default from config).

    Returns:
        { "ok": bool, "stdout": str, "stderr": str, "returncode": int,
          "timed_out": bool }
    """
    t = int(timeout) if timeout else Config.SANDBOX_TIMEOUT
    full = PREAMBLE + "\n" + code
    try:
        completed = subprocess.run(
            [sys.executable, "-I", "-"],
            input=full,
            capture_output=True,
            text=True,
            timeout=t,
        )
        return {
            "ok": completed.returncode == 0,
            "stdout": completed.stdout[:8000],
            "stderr": completed.stderr[:4000],
            "returncode": completed.returncode,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "stdout": (e.stdout or b"").decode("utf-8", errors="replace")[:8000] if isinstance(e.stdout, bytes) else (e.stdout or "")[:8000],
            "stderr": f"TIMEOUT after {t}s",
            "returncode": -1,
            "timed_out": True,
        }


@mcp.tool()
def check_t_statistic(t_value: float, df: int, two_tailed: bool = True) -> dict:
    """Compute the p-value for a reported t statistic.

    Useful when a paper reports t(df)=X without a p-value.
    """
    p = _scipy_stats.t.sf(abs(t_value), df)
    if two_tailed:
        p *= 2
    return {"t": t_value, "df": df, "two_tailed": two_tailed, "p_value": float(p)}


if __name__ == "__main__":
    mcp.run()

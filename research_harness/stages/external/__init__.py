"""External browser-based providers (GPTZero, paperreview.ai, ...).

These adapters drive a real Chrome instance via the Chrome DevTools Protocol
(CDP). They do NOT call an LLM and do NOT depend on the OpenProgram browser
tool's session model — that session only sees tabs it created, but several of
these providers (GPTZero in particular) open result pages in NEW tabs whose
URLs we must locate via the raw CDP tab list.

Requirements at runtime:
  - A Chrome process must already be running with `--remote-debugging-port=9222`.
    `openprogram browser attach` brings up such a sidecar; if you are running
    your own Chrome with that flag, that works too.
  - `playwright` must be installed in the active Python env.
"""

from research_harness.stages.external.gptzero_browser import (
    check_ai_score_gptzero,
)

__all__ = ["check_ai_score_gptzero"]

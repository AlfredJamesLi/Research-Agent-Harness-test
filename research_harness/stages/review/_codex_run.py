"""Wrapper around `subprocess.run` for codex CLI calls with retry on
transient failures.

Codex CLI is observed to occasionally exit with rc=-9 (SIGKILL, usually
OS OOM killer or codex's own watchdog) on long prompts. These failures
are transient — same prompt usually succeeds on the second try. Wrap
the call once here so every codex invocation in the review pipeline
gets the same retry behavior.
"""
from __future__ import annotations

import subprocess
import time


_TRANSIENT_RETURN_CODES = {-9}  # SIGKILL


def run_codex(cmd: list[str], *, timeout_s: int,
              max_attempts: int = 3,
              sleep_between_s: float = 2.0
              ) -> subprocess.CompletedProcess:
    """Run a codex command with retry on transient failures.

    Retries on:
      - return code -9 (SIGKILL)
      - subprocess.TimeoutExpired

    Other non-zero return codes are returned to the caller without
    retry; they are usually deterministic (bad args, auth, etc.).

    Args:
        cmd:            Full argv list for codex CLI.
        timeout_s:      Per-attempt timeout in seconds.
        max_attempts:   Total attempts including the first (default 3).
        sleep_between_s: Backoff between attempts (default 2s).

    Returns:
        CompletedProcess from the successful (or final) attempt.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as e:
            last_exc = e
            if attempt < max_attempts:
                time.sleep(sleep_between_s)
                continue
            raise
        if r.returncode in _TRANSIENT_RETURN_CODES and attempt < max_attempts:
            time.sleep(sleep_between_s)
            continue
        return r
    # Defensive: should be unreachable.
    if last_exc is not None:
        raise last_exc
    return r

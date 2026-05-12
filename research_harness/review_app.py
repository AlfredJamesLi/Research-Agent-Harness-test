"""DEPRECATED — kept as a thin shim for backward compatibility.

The unified entry is `research_harness.review` (CLI: `research-review`).
This module forwards `python -m research_harness.review_app` and the
`generate_review` import to the new entry. Existing skills that call
`python -m research_harness.review_app paper.pdf --venue X [--draft d]`
still work.

New code should import:
    from research_harness.review import review
or run:
    research-review paper.pdf --venue X
"""
from __future__ import annotations

from typing import Optional

from openprogram.agentic_programming.runtime import Runtime

from research_harness.review import (
    review as _review_unified,
    main,
)


def generate_review(paper_path: str, *, venue: str = "NeurIPS",
                    runtime: Optional[Runtime] = None,
                    work_dir: Optional[str] = None,
                    provider: str = "auto",
                    model: Optional[str] = None,
                    draft_path: Optional[str] = None) -> dict:
    """Backward-compatible single-review entry. Forwards to
    `research_harness.review.review(mode='external')`.
    """
    # `runtime` was the legacy single-runtime param; map it to both
    # exec and review runtimes so the new API still works.
    return _review_unified(
        paper=paper_path, venue=venue, mode="external",
        exec_runtime=runtime, review_runtime=runtime,
        work_dir=work_dir, provider=provider,
        review_model=model,
        draft=draft_path,
    )


if __name__ == "__main__":
    import sys
    sys.exit(main())

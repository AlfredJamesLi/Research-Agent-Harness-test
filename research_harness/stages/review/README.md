# review stage

Python module for paper review. Used by the unified `research-review` CLI (see top-level repo README) and by the `research_agent` orchestrator's review stage.

The Claude Code / opencode skills (`peer-review`, `self-review`) live in a separate repo: [Paper-Review-Skills](https://github.com/Fzkuji/Paper-Review-Skills) (or the local `~/Documents/Paper-Review-Skills/` checkout). They are intentionally standalone — no Python dependency — so users without this repo can still run them.

## What this module provides

- **`review_paper`, `review_paper_grounded`** — single-call reviewer functions, used inside the orchestrator and by `research-review` CLI.
- **`review_loop`** — multi-round ARIS-style review-fix loop with N persona reviewers + AC meta-review + optional debate / grounding / GPTZero / auto-fix.
- **`fix_paper`** — applies reviewer feedback to a paper.
- **`extract_judgment`** — compresses an existing review draft into a structured judgment dict, used by humanize mode.
- **`load_paper`** — paper file → markdown (PDF/DOCX/TEX/HTML conversion, cached as sibling .md).
- **`review_corpus/`** — sentence templates + few-shot pool drawn from real human reviewers; powers the humanize and venue-form prose pipelines.

## CLI entry

```bash
research-review paper.pdf --venue NeurIPS                  # single review
research-review paper.pdf --venue NeurIPS --draft draft.md # humanize draft
research-review paper.pdf --venue NeurIPS --mode revise --auto-fix  # ARIS loop
```

See the top-level repo README for full usage.

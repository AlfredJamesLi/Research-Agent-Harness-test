---
name: peer-review
version: 7.0.0
description: |
  Write a venue-form peer review of someone else's paper. Humanization
  is automatic.

  Thin shim over the `research-review` CLI. The CLI runs three steps
  internally for every paper: (1) codex writes a long detailed
  free-form draft, no template constraint; (2) extract_judgment
  compresses the draft into a structured judgment dict (numerics +
  per-field bullets); (3) prose is regenerated under real-human
  sentence templates, with numerics preserved from step 1.

  No draft input, no draft option. Every paper goes through the same
  three-step pipeline.

  For self-critique with no AI-detection concern, use /self-review.
license: MIT
compatibility: claude-code opencode
allowed-tools:
  - Read
  - Write
  - Bash
  - AskUserQuestion
---

# peer-review

Single venue-form review of someone else's paper, with humanization built in.

## When to use

- The user asks you to review someone else's paper for a specific venue.
- The user mentions GPTZero / Originality / Pangram / ACM MM AI-rate cap.
- The user explicitly invokes `/peer-review`.

For a free-form critique of the user's own paper, use `/self-review`. For humanizing an existing draft, use `/humanize-paper-review`.

## Required inputs (use AskUserQuestion if missing)

- **paper** — path to the paper file (PDF / DOCX / MD / TEX / HTML).
- **venue** — target venue (ACM MM, NeurIPS, ICLR, ARR / EMNLP, AAAI, CVPR, ICML, COLM, IJCAI, AISTATS, journals like TPAMI / TMLR / JMLR). Default: ACM MM.
- **output** — output path. Default: alongside the paper, suffix `_review.json`.

## Install from zero (one-time)

If the user has nothing installed yet, run these in order. All three are required.

```bash
# 1. Python CLI (pulls openprogram in as a git dependency automatically)
pip install research-agent-harness

# 2. Codex CLI (the prose generator; gpt-5.5 backend is the verified 0% AI path)
npm install -g @openai/codex
codex auth login   # interactive: ChatGPT account or API key

# 3. Verify
research-review --help
```

Local development (editable, if user has the repo cloned):

```bash
pip install -e ~/Documents/LLM\ Agent\ Harness/OpenProgram
pip install -e ~/Documents/Research-Agent-Harness
```

Symlink this skill into Claude Code / opencode (one time, if not already):

```bash
ln -s <path-to-research-agent-harness>/skills/peer-review ~/.claude/skills/peer-review
# opencode: ~/.config/opencode/skills/peer-review
```

## Run the CLI

```bash
research-review "<paper>" --venue "<venue>" -o "<output>"
```

The CLI runs the full pipeline:
1. Loads the paper (PDF / DOCX / TEX → markdown, cached as sibling .md).
2. Looks up the venue's scoring rubric and required form fields.
3. Generates prose via codex CLI under the real-human sentence-template constraint (RULE 1 paper-grounding, RULE 2 template skeletons).
4. Fills numeric / enum / boolean fields (score, verdict, sub_scores, confidence, best_paper_candidate) via tool-use schema enforcement.
5. Writes a JSON to the output path with all venue-required fields.

## Report to user

- Where the file was saved.
- The venue spec used (echoed in the JSON's `venue` field).
- AI-detection rate not measured. If a hard KPI applies, invoke `/gptzero-check` on the output. Re-run if it exceeds the cap.

## Errors

- `Unknown provider: 'codex'` — use `--review-provider openai-codex` (the canonical name).
- `gpt-5.5-mini not supported` — codex ChatGPT account doesn't allow the mini model on some endpoints; the CLI defaults to `gpt-5.5` so this should not occur unless the default has been overridden.
- `codex did not write` — usually a transient SIGKILL from the codex CLI. The CLI retries internally. If it persists, check Chrome / network and re-run.

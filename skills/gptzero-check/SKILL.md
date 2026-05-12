---
name: gptzero-check
version: 2.0.0
description: |
  Run GPTZero AI detection on one or more review files via the
  app.gptzero.me dashboard. Requires Chrome running with
  --remote-debugging-port=9222 and the user logged into GPTZero.

  Use when user says "check ai rate", "gptzero", "检测AI率", "run gptzero",
  or wants to verify that review prose passes AI detection.
license: MIT
compatibility: claude-code opencode
allowed-tools:
  - Bash
  - AskUserQuestion
---

# gptzero-check

Run GPTZero AI detection on review files. All logic lives in
`research_harness/stages/external/gptzero_check.py` — this skill just
drives that script.

## Install from zero (one-time)

```bash
# 1. Python CLI (auto-installs openprogram)
pip install research-agent-harness

# 2. Symlink the skill
ln -s <path-to-research-agent-harness>/skills/gptzero-check ~/.claude/skills/gptzero-check

# 3. One-time: open Chrome with CDP and log into GPTZero
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=$HOME/.openprogram/chrome-profile &

# Then in that Chrome window: open https://app.gptzero.me, log in. Cookie
# persists in the user-data-dir; subsequent runs just need Chrome up.
```

## When to use

- User says "run gptzero", "check AI rate", "检测AI率", "verify AI detection"
- After `peer-review` or `humanize-paper-review` to verify results

## Required inputs

- **File paths** — one or more review files. Supported formats: `.md`, `.txt`.
  Ask via AskUserQuestion if not provided.

## Format conversion (non-text inputs)

If the input file is not `.md` or `.txt`, convert it first:

**PDF:**
```bash
cd /Users/fzkuji/Documents/Research-Agent-Harness && \
python -m research_harness.stages.review.pdf_to_markdown "<file.pdf>" > "<file.md>"
```

**DOCX:**
```bash
cd /Users/fzkuji/Documents/Research-Agent-Harness && \
python -m research_harness.stages.review.docx_to_markdown "<file.docx>" > "<file.md>"
```

Then pass the converted `.md` file to the scan step below.

## Prerequisites check

Before running, verify Chrome CDP is reachable:

```bash
curl -s http://localhost:9222/json | python3 -c "import json,sys; print('OK:', len(json.load(sys.stdin)), 'tabs')" 2>&1
```

If it fails, start Chrome:

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/Users/fzkuji/.openprogram/chrome-profile &
sleep 4
```

Then verify again. If Chrome is up but GPTZero isn't logged in, tell the
user to open app.gptzero.me in that Chrome window and log in.

## Run the scan

```bash
cd /Users/fzkuji/Documents/Research-Agent-Harness && \
python -m research_harness.stages.external.gptzero_check \
  "<file1>" "<file2>" "<file3>"
```

Output is one JSON object per line. Each line has:
- `ai_pct`, `human_pct`, `mixed_pct` — percentages (0-100)
- `verdict` — full sentence from GPTZero
- `confidence` — "highly" / "moderately" / "somewhat"
- `status` — "ok" / "no_result" / "error"

## Report results

Present as a table:

| File | AI% | Human% | Pass? |
|------|-----|--------|-------|
| ... | ... | ... | ✓ / ✗ |

Pass threshold: AI% ≤ 20% (COLM 2026 / ACM MM standard).

If any file fails (AI% > 20%), suggest running `/humanize-paper-review`.

## Error handling

- `status: error` with "CDP not reachable" → start Chrome (see Prerequisites)
- `status: error` with "Could not open GPTZero tab" → user needs to log in
- `status: no_result` → GPTZero rate-limited; wait 30s and rerun that file

# Research Agent Harness

Autonomous research agent: from topic to submission-ready paper.

Built with [OpenProgram](https://github.com/Fzkuji/OpenProgram) (Agentic Programming paradigm) — Python controls the workflow, LLM reasons at each step via `@agentic_function` docstrings.

## Quick Start

### 1. Install

```bash
# Install dependencies
pip install -r requirements.txt

# Then install the harness itself
pip install research-agent-harness
```

<details>
<summary><b>Local development (editable)</b></summary>

Clone both repos, install OpenProgram first (this repo depends on it):

```bash
# 1. Clone and install OpenProgram
git clone https://github.com/Fzkuji/OpenProgram.git
pip install -e OpenProgram

# 2. Clone and install this harness
git clone https://github.com/Fzkuji/Research-Agent-Harness.git
pip install -r Research-Agent-Harness/requirements.txt
pip install -e Research-Agent-Harness
```

`pip install -e` hard-codes absolute paths into `site-packages/*.pth`. If you rename any parent folder, `import research_harness` (or `openprogram`) will break with `ModuleNotFoundError` until you rerun `pip install -e .` from the new location.

</details>


### 2. Set up LLM providers

```bash
# Executor: Claude Code CLI (recommended — full file system access)
npm install -g @anthropic-ai/claude-code && claude login

# Reviewer: Codex CLI (recommended — cross-model review with GPT)
npm install -g @openai/codex && codex auth login

# Or use API keys directly
export ANTHROPIC_API_KEY=sk-...
export OPENAI_API_KEY=sk-...
```

### 3. Use — two CLI entry points

**`research-harness`** is the top-level autonomous agent. The LLM picks stages, picks functions, runs the full pipeline.

```bash
# Autonomous: pick stages and functions to satisfy the task
research-harness --work-dir /abs/path "Survey recent work on LLM uncertainty"

# Cross-model: Claude writes, Codex/GPT reviews
research-harness --work-dir /abs/path --provider claude-code --review-provider openai-codex \
    "Review the paper at ./my-project/"

# List all 48+ registered functions
research-harness --work-dir /tmp --list
```

**`research-review`** is the focused paper-review CLI. One paper in, one structured review JSON out.

```bash
# Peer mode (default): one venue-form review of someone else's paper
research-review paper.pdf --venue NeurIPS -o review.json

# Humanize an existing draft (preserves score / verdict / observations)
research-review paper.pdf --venue "ACM MM" --draft my_draft.md -o review.json

# Revise mode: multi-round ARIS-style review-fix loop on your own paper
research-review paper.pdf --venue NeurIPS --mode revise --auto-fix --max-rounds 4
```

Defaults: provider `auto`, review provider `openai-codex` (gpt-5.5), output stdout. Run `research-review --help` for the full flag set.

### 4. Use the skills (Claude Code / opencode)

Three skills ship with this repo and depend on the `research-review` CLI installed by step 1.

| Skill | What it does | Backing CLI |
|---|---|---|
| `/peer-review` | Venue-form review of someone else's paper, prose humanized for AI-detection | `research-review paper.pdf --venue X -o out.json` |
| `/humanize-paper-review` | Take an existing review draft, rewrite prose to pass AI-detection, preserve judgment | `research-review paper.pdf --venue X --draft draft.md -o out.json` |
| `/self-review` | Harsh self-critique of your own paper (no detector concern, no humanization) | Pure prompt, no CLI |
| `/gptzero-check` | Run GPTZero on a review file via Chrome CDP | `python -m research_harness.stages.external.gptzero_check ...` |

**Install the skills (one time)**:

```bash
# symlink (Mac / Linux)
ln -s "$(pwd)/skills/peer-review"            ~/.claude/skills/peer-review
ln -s "$(pwd)/skills/humanize-paper-review"  ~/.claude/skills/humanize-paper-review
ln -s "$(pwd)/skills/self-review"            ~/.claude/skills/self-review
ln -s "$(pwd)/skills/gptzero-check"          ~/.claude/skills/gptzero-check
```

For opencode, replace `~/.claude/skills/` with `~/.config/opencode/skills/`.

**Usage in Claude Code / opencode**:

```
> /peer-review paper.pdf venue=NeurIPS
> /humanize-paper-review paper.pdf draft=my_draft.md venue="ACM MM"
> /self-review my_paper.tex
> /gptzero-check review.md
```

The skill triggers Claude (or whichever model is running) to invoke the right CLI command via Bash. All the heavy lifting (codex prose generation under real-human sentence templates, paper-grounding, structured JSON via tool-use schema) happens in the Python CLI — the skill is a thin shim that knows which command to run.

**In Python**:

```python
from research_harness.review import review

# Peer mode
result = review("paper.pdf", venue="NeurIPS")

# Humanize an existing draft
result = review("paper.pdf", venue="ACM MM", draft="my_draft.md")

# Self / revise mode (multi-round ARIS loop)
result = review("paper.pdf", venue="NeurIPS", mode="revise",
                max_rounds=4, auto_fix=True)
```

For the autonomous agent:

```python
from research_harness.main import research_agent
from openprogram.providers import create_runtime

rt = create_runtime(provider="claude-code")
rt.set_workdir("/abs/path/to/work-dir")
result = research_agent(task="Survey LLM uncertainty", runtime=rt)
```

## Architecture

### Two-Level Autonomous Loop

```
research_agent(task, runtime, review_runtime)
│
├── Level 1: _pick_stage(task, progress)
│   LLM sees 10 stages, picks the right one based on task + progress.
│
└── Level 2: _stage_step(stage, sub_task, context)
    LLM sees all functions in that stage, picks one to call.
    Prefers orchestrator functions (review_loop, run_literature, etc.)
    that chain multiple steps internally.
    Loops until stage_done or max steps reached.

→ Back to Level 1 with updated progress, until done or max stages.
```

**Key design:** Python controls the loop structure, LLM makes decisions at each step. Each `@agentic_function` calls `runtime.exec()` exactly once — the LLM reads the docstring and does the work.

### Registry

All 48+ functions are registered in `registry.py` with their stage membership. Functions are lazy-loaded. The dispatcher shows only functions in the current stage.

```python
STAGES = {
    "literature":   "Survey papers, search arXiv/Semantic Scholar, identify research gaps",
    "idea":         "Generate research ideas, check novelty, rank by promise",
    "experiment":   "Design experiments, implement, run, monitor training",
    "writing":      "Write sections, polish, translate, compress/expand, figures, compile LaTeX",
    "review":       "Review paper, fix based on feedback, review-fix loop",
    "rebuttal":     "Parse reviewer comments, build strategy, draft rebuttal",
    "presentation": "Generate slides, poster, speaker notes",
    "theory":       "Derive formulas, write proofs, plan ablations, grant proposals",
    "knowledge":    "Research wiki, meta-optimize harness",
    "project":      "Initialize project, run full pipeline",
}
```

### Cross-Model Review (ARIS Design)

Following [ARIS](https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep), the review system uses **two different models** — the reviewer (GPT via Codex) and the author (Claude) are adversarial by design.

```
review_loop(paper_dir, venue, exec_runtime=Claude, review_runtime=Codex/GPT)
│
├── lookup_venue_criteria(venue)         [GPT — query scoring rubric]
│
└── for round 1..4:                      [each round = new session]
    │
    ├── review_runtime.reset()           ← new session
    │
    ├── Phase A: review_paper()          [GPT reviews the paper]
    │   ├── medium:    curated context (15k tokens)
    │   ├── hard:      + reviewer memory + debate protocol
    │   └── nightmare: + full content (no truncation) + adversarial verification
    │
    ├── Phase B: Parse assessment        [extract score, verdict, weaknesses]
    │
    ├── Phase B.5: Reviewer Memory       [hard/nightmare — accumulate suspicions]
    │   In-memory string, passed to reviewer in next round's prompt.
    │   Reviewer checks if previous concerns were addressed or sidestepped.
    │
    ├── Phase B.6: Debate Protocol       [hard/nightmare — if weaknesses exist]
    │   ├── Author (Claude): rebut up to 3 weaknesses
    │   └── Reviewer (GPT): rule SUSTAINED / OVERRULED / PARTIALLY SUSTAINED
    │
    ├── Phase E: Save to AUTO_REVIEW.md  [cumulative log with full raw responses]
    │
    ├── Stop? score >= 6 or verdict contains "accept"/"ready" → return
    │
    ├── exec_runtime.reset()             ← new session
    └── Phase C: fix_paper()             [Claude fixes the paper]
```

**Difficulty levels (information control):**

| Level | Who controls what reviewer sees | Extra capabilities |
|-------|-------------------------------|-------------------|
| **medium** | **Author** curates 15k tokens for reviewer | Standard review |
| **hard** | **Author** curates 14k tokens, but reviewer has memory | Memory across rounds + debate protocol |
| **nightmare** | **Reviewer reads files independently** (author has zero info control) | Adversarial verification + independent file access |

The key design from ARIS: difficulty controls **information asymmetry**. In medium/hard, Claude decides what GPT sees. In nightmare, GPT reads the repo directly — Claude cannot hide anything.

**Providers:**

| Role | Recommended | Alternative |
|------|------------|------------|
| Executor (author) | `claude-code` (Claude Code CLI) | `anthropic` (API) |
| Reviewer | `codex` (Codex CLI, GPT, session continuity) | `openai` (API, stateless) |

### Runtime Providers

| Provider | CLI Flag | Session | File Access | Auth |
|----------|----------|---------|-------------|------|
| `claude-code` | `--provider claude-code` | Yes (reset per step) | Full file system | `claude login` |
| `codex` | `--provider codex` | Yes (auto thread ID) | Repo access | `codex auth login` |
| `openai` | `--provider openai` | No (stateless API) | None | `OPENAI_API_KEY` |
| `anthropic` | `--provider anthropic` | No (stateless API) | None | `ANTHROPIC_API_KEY` |

### Persistence & Tracing

- All leaf functions include a `# Persistence` prompt: the agent saves complete output to files and returns a summary.
- `AUTO_REVIEW.md` — cumulative review log with full raw reviewer responses, debate transcripts.
- Results are saved to the **target project directory** (the agent infers the path from the task description).

## All Functions (48+)

### Literature & Search
| Function | Description |
|----------|-------------|
| `survey_topic` | Survey literature: find papers, organize by subtopic, note gaps |
| `identify_gaps` | Identify specific, actionable research gaps from a survey |
| `search_arxiv` | Search arXiv API for papers |
| `search_semantic_scholar` | Search Semantic Scholar API |
| `comprehensive_lit_review` | Full literature review with structured output |
| `run_literature` | **Orchestrator**: survey + gaps in one call |

### Idea Generation
| Function | Description |
|----------|-------------|
| `generate_ideas` | Generate research ideas from gaps |
| `check_novelty` | Check idea novelty against literature |
| `rank_ideas` | Rank ideas by feasibility and impact |
| `run_idea` | **Orchestrator**: generate + novelty check + rank |

### Experiment
| Function | Description |
|----------|-------------|
| `design_experiments` | Design experiment plan |
| `experiment_bridge` | Bridge from idea to executable experiment |
| `run_experiment` | Generate and run experiment code |
| `check_training` | Monitor training progress |
| `plan_ablations` | Design ablation studies |
| `run_experiments` | **Orchestrator**: design + run experiments |

### Writing (English)
| Function | Description |
|----------|-------------|
| `write_section` | Write a paper section from outline + notes |
| `polish_rigorous` | Deep polish for academic rigor |
| `polish_natural` | Polish for naturalness, remove AI patterns |
| `translate_zh2en` | Chinese draft -> English LaTeX |
| `translate_en2zh` | English LaTeX -> Chinese text |
| `compress_text` | Reduce word count by 5-15 words |
| `expand_text` | Add 5-15 words with deeper logic |
| `check_logic` | Final check for fatal errors only |
| `analyze_results` | Experimental data -> LaTeX analysis |
| `results_to_claims` | Judge what claims results support |

### Writing (Chinese)
| Function | Description |
|----------|-------------|
| `rewrite_zh` | Rewrite fragmented draft |
| `polish_zh` | Polish Chinese paper text |
| `remove_ai_flavor_zh` | Remove AI patterns from Chinese |

### Figures & Tables
| Function | Description |
|----------|-------------|
| `generate_figure_caption` | Generate English figure caption |
| `generate_table_caption` | Generate English table caption |
| `recommend_visualization` | Recommend chart type for data |
| `design_architecture_figure` | Design framework/architecture diagram |
| `generate_paper_figures` | Generate matplotlib plots |
| `generate_mermaid_diagram` | Generate Mermaid diagram code |
| `compile_paper` | Compile LaTeX -> PDF, fix errors |

### Review & Rebuttal
| Function | Description |
|----------|-------------|
| `review_paper` | Review paper against venue criteria |
| `fix_paper` | Fix paper based on review feedback |
| `lookup_venue_criteria` | Query venue-specific scoring rubric |
| `review_loop` | **Orchestrator**: full review-fix cycle (medium/hard/nightmare) |
| `paper_improvement_loop` | **Orchestrator**: writing quality improvement (2 rounds) |
| `parse_reviews` | Parse reviewer comments into structured issues |
| `build_rebuttal_strategy` | Build response strategy per weakness |
| `draft_rebuttal` | Draft venue-compliant rebuttal |

### Presentation
| Function | Description |
|----------|-------------|
| `generate_slides` | Beamer slides for conference talk |
| `generate_poster` | LaTeX poster for poster session |
| `generate_speaker_notes` | Speaker notes + Q&A prep |

### Theory & Planning
| Function | Description |
|----------|-------------|
| `derive_formula` | Derive formulas from scattered notes |
| `write_proof` | Write rigorous mathematical proof |
| `plan_ablations` | Design ablation studies |
| `refine_research` | Refine vague direction -> focused plan |
| `write_grant_proposal` | Draft grant proposal (NSFC/NSF/ERC/...) |

### Knowledge & Meta
| Function | Description |
|----------|-------------|
| `research_wiki` | Persistent knowledge base |
| `meta_optimize` | Analyze usage, propose harness improvements |

## Project Structure

```
Research-Agent-Harness/
├── SKILL.md                     # Skill definition for IDE discovery
├── research_harness/
│   ├── main.py                  # Two-level loop + CLI entry point
│   ├── registry.py              # Function registry (lazy loading, stage mapping)
│   ├── log.py                   # Append-only operation log
│   ├── pipeline.py              # 8-stage orchestrator
│   ├── utils.py                 # Shared utilities (parse_json, etc.)
│   ├── references/              # Writing principles, citation discipline, venue checklists
│   ├── wiki/                    # Research Wiki (persistent knowledge base)
│   └── stages/
│       ├── init.py              # Project directory setup
│       ├── literature/          # survey_topic, identify_gaps, search_arxiv, ...
│       ├── idea/                # generate_ideas, check_novelty, rank_ideas
│       ├── experiment/          # design_experiments, run_experiment, check_training, ...
│       ├── writing/             # 20 functions: write/polish/translate/analyze/figures
│       ├── review/              # review_paper, fix_paper, review_loop (3 levels), debate
│       ├── rebuttal/            # parse_reviews, build_strategy, draft_rebuttal
│       ├── presentation/        # generate_slides, generate_poster, speaker_notes
│       ├── theory/              # derive_formula, write_proof, plan_ablations, ...
│       ├── submission/          # check_submission
│       └── meta/                # meta_optimize
├── tests/
│   ├── test_main.py             # Two-level loop, CLI, operation log
│   ├── test_registry.py         # Registry, stage mapping, orchestrators
│   ├── test_log.py              # Operation log
│   ├── test_e2e.py              # End-to-end against real projects
│   └── conftest.py              # MockRuntime, fixtures
├── skills/                      # SKILL.md files for IDE discovery
└── templates/                   # Structured input/output templates
```

## Design Principles

1. **Two-level autonomous loop** — Level 1 picks the research stage, Level 2 dispatches to functions within that stage. Python controls the loop, LLM makes decisions.
2. **Prompt = docstring** — no external prompt files; the function's docstring IS the instruction to the LLM.
3. **Cross-model review (ARIS design)** — executor (Claude) and reviewer (GPT/Codex) are different models to avoid self-play blind spots. 3 difficulty levels with reviewer memory and debate protocol.
4. **Agent saves files** — leaf functions prompt the agent to save complete output. No Python `open().write()` in the hot path. The agent decides where to save based on context.
5. **Orchestrators for complete workflows** — `review_loop`, `run_literature`, `run_idea`, `run_experiments` chain multiple steps. The dispatcher prefers these over individual leaf functions.
6. **Everything leaves a trace** — AUTO_REVIEW.md, operation logs, file saves. No work is lost.

## References

- [ARIS](https://github.com/wanshuiyin/Auto-claude-code-research-in-sleep) — autonomous research pipeline with cross-model review (primary reference for review loop design)
- [awesome-ai-research-writing](https://github.com/Leey21/awesome-ai-research-writing) — battle-tested writing prompts from top research labs
- [OpenProgram](https://github.com/Fzkuji/OpenProgram) — the runtime framework (`@agentic_function`, `Runtime.exec()`); *Agentic Programming* is the paradigm it ships

"""Pipe the same text through humanize_text using three different providers
in sequence; record GPTZero ai_pct at each hop.

Hypothesis: token-distribution detectors fingerprint a single LLM's choices.
Cross-model paraphrase round-trips reshuffle those choices — each new model
re-tokenizes and re-samples, so the cumulative sequence drifts away from
any single model's signature.

Pipeline:
  empiricist weakness block
    [hop 1: openai-codex] → humanized_v1 → GPTZero
    [hop 2: claude-code]  → humanized_v2 → GPTZero
    [hop 3: gemini-cli]   → humanized_v3 → GPTZero

Saves all intermediates to /tmp/cross_model_demo/ for inspection.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


HOPS = (
    # (label, provider, model_or_None)
    # gpt-5.5 explicit because the PROVIDERS default ("gpt-5.5-mini") is
    # rejected on ChatGPT-subscription accounts.
    ("hop1_openai_codex", "openai-codex", "gpt-5.5"),
    ("hop2_claude_code",  "claude-code",  None),
    # gemini-cli is skipped: requires a configured GCP project (404 from
    # /v1/projects//locations/...). Re-enable when the user runs `gcloud
    # auth application-default login` and sets GOOGLE_CLOUD_PROJECT.
)

WORK_ROOT = "/tmp/cross_model_demo"
SOURCE = "/Users/fzkuji/Downloads/auto_review/round_1/reviewer_1_empiricist.md"


def _extract_part1(raw: str) -> str:
    m = re.search(r'(?:#\s*)?Part\s*1[^\n]*\n+(.*?)(?=\n+(?:#\s*)?Part\s*2)',
                  raw, flags=re.DOTALL | re.IGNORECASE)
    if not m:
        # Some providers omit the header layout; fall back to whole file.
        return raw.strip()
    return m.group(1).strip().rstrip('-').strip()


def _latest_humanize_artifact(*search_dirs: str) -> str | None:
    cands = []
    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            if f.startswith("humanize") and f.endswith(".md"):
                p = os.path.join(d, f)
                cands.append((os.path.getmtime(p), p))
    if not cands:
        return None
    cands.sort(reverse=True)
    return cands[0][1]


def main() -> None:
    from openprogram.providers import create_runtime
    from research_harness.stages.writing.humanize_text import humanize_text
    from research_harness.stages.external.gptzero_browser import (
        check_ai_score_gptzero,
    )

    with open(SOURCE) as f:
        review = json.load(f)
    text = "\n\n".join(review["weaknesses"])
    print(f"INPUT (LLM empiricist weaknesses): {len(text)} chars, "
          f"{len(text.split())} words\n")

    # Score input baseline so we can compare deltas.
    print("[hop 0] GPTZero on raw input …")
    base = check_ai_score_gptzero(text, poll_timeout=60)
    print(f"  ai_pct={base.get('ai_pct')}  human_pct={base.get('human_pct')}  "
          f"verdict={base.get('verdict')}\n")

    trajectory = [{"hop": 0, "label": "input",
                   "ai_pct": base.get("ai_pct"),
                   "human_pct": base.get("human_pct"),
                   "chars": len(text)}]

    current_text = text
    os.makedirs(WORK_ROOT, exist_ok=True)

    for hop_idx, (label, provider, model) in enumerate(HOPS, start=1):
        hop_dir = os.path.join(WORK_ROOT, label)
        os.makedirs(hop_dir, exist_ok=True)
        # Wipe stale artifacts so _latest_humanize_artifact picks ours.
        for f in os.listdir(hop_dir):
            if f.startswith("humanize") and f.endswith(".md"):
                os.remove(os.path.join(hop_dir, f))
        # Also wipe repo-root humanize_*.md (codex sometimes writes there).
        for f in os.listdir(str(REPO)):
            if f.startswith("humanize") and f.endswith(".md"):
                os.remove(os.path.join(str(REPO), f))

        print(f"[hop {hop_idx}] humanize_text via provider={provider} "
              f"model={model or 'default'} …")
        try:
            rt = create_runtime(provider=provider, model=model)
            rt.set_workdir(hop_dir)
        except Exception as e:
            print(f"  ✗ create_runtime failed: {type(e).__name__}: {e}")
            trajectory.append({"hop": hop_idx, "label": label,
                               "error": f"create_runtime: {e}"})
            continue

        try:
            summary = humanize_text(text=current_text, lang="en",
                                     voice_sample="", runtime=rt)
        except Exception as e:
            print(f"  ✗ humanize_text crashed: {type(e).__name__}: {e}")
            trajectory.append({"hop": hop_idx, "label": label,
                               "error": f"humanize: {e}"})
            continue
        print(f"  summary: {summary[:200]}")

        artifact = _latest_humanize_artifact(hop_dir, str(REPO))
        if artifact:
            with open(artifact) as f:
                raw = f.read()
            humanized = _extract_part1(raw)
        else:
            # Codex (and sometimes other providers) return the humanized
            # text directly instead of saving to disk. Treat the return
            # value as the artifact when it doesn't start with "Saved to".
            print(f"  · no file artifact; using return value as humanized")
            cleaned = (summary or "").strip()
            if cleaned.lower().startswith("saved to"):
                # Provider claimed to save but file is missing — fail.
                trajectory.append({"hop": hop_idx, "label": label,
                                   "error": "claimed_save_but_no_file"})
                continue
            # Strip any "Part 1" / "Part 2" headers if the model included them.
            humanized = _extract_part1(cleaned) if "Part 1" in cleaned \
                        else cleaned

        # Persist a normalized copy in the hop dir.
        norm_path = os.path.join(hop_dir, "humanized_only.txt")
        with open(norm_path, "w") as f:
            f.write(humanized)
        print(f"  → {len(humanized)} chars, {len(humanized.split())} words "
              f"(artifact={artifact})")

        # GPTZero hop check.
        print(f"  GPTZero …")
        result = check_ai_score_gptzero(humanized, poll_timeout=60)
        print(f"  ai_pct={result.get('ai_pct')}  human_pct={result.get('human_pct')}  "
              f"verdict={result.get('verdict')}")
        trajectory.append({
            "hop": hop_idx, "label": label, "provider": provider,
            "model": model, "chars": len(humanized),
            "ai_pct": result.get("ai_pct"),
            "human_pct": result.get("human_pct"),
            "verdict": result.get("verdict"),
            "url": result.get("url"),
        })

        # Feed forward to next hop only if this hop produced text.
        if humanized.strip():
            current_text = humanized
        print()

    # Final report.
    out = os.path.join(WORK_ROOT, "trajectory.json")
    with open(out, "w") as f:
        json.dump(trajectory, f, ensure_ascii=False, indent=2)

    print("\n=== Trajectory ===")
    print(f"{'hop':<5}{'label':<22}{'chars':<8}{'ai_pct':<8}{'human_pct':<10}")
    for t in trajectory:
        if "error" in t:
            print(f"{t['hop']:<5}{t['label']:<22}ERROR — {t['error']}")
        else:
            print(f"{t['hop']:<5}{t['label']:<22}"
                  f"{t.get('chars','?'):<8}{str(t.get('ai_pct','?')):<8}"
                  f"{str(t.get('human_pct','?')):<10}")
    print(f"\nfull trajectory: {out}")


if __name__ == "__main__":
    main()

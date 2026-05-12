"""Use the GPTZero-validated human review corpus as voice_sample for
humanize_text, then re-score the humanized output with GPTZero.

Reads:
  stages/review/review_corpus/extracts/voice_corpus.json
Writes:
  /tmp/humanize_demo_v3/  (whatever codex chooses to save)
  prints a comparison table
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DERIVED_ROOT = ROOT.parent / "extracts"
REPO = Path(__file__).resolve().parents[5]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def build_voice_sample(corpus: list[dict], *,
                       max_total_chars: int = 5500,
                       per_sample_max: int = 1400,
                       prefer_recent: bool = True) -> str:
    """Pick a balanced subset and concat. Diversity matters more than length:
    aim to cover multiple venues and years, not pile on one paper.

    prefer_recent: if True, weight 2023+ over 2020-2022 — the user said
    recent reviews are more representative; older ones are baseline.
    """
    by_bucket: dict[tuple[str, int], list[dict]] = {}
    for s in corpus:
        v = s.get("venue") or "?"
        y = int(s.get("year") or 0)
        by_bucket.setdefault((v, y), []).append(s)

    # Round-robin across buckets — pick the longest text per bucket each round.
    buckets = sorted(by_bucket.keys(),
                     key=lambda k: (-k[1] if prefer_recent else k[1]))
    for k in by_bucket:
        by_bucket[k].sort(key=lambda s: -len(s.get("text") or ""))

    picked: list[dict] = []
    total = 0
    while total < max_total_chars and any(by_bucket[k] for k in buckets):
        for k in buckets:
            if total >= max_total_chars:
                break
            if not by_bucket[k]:
                continue
            cand = by_bucket[k].pop(0)
            text = (cand.get("text") or "").strip()
            if len(text) > per_sample_max:
                text = text[:per_sample_max].rstrip() + "..."
            picked.append({**cand, "_text_used": text})
            total += len(text)

    blocks = []
    for s in picked:
        header = (f"### {s.get('venue')} {s.get('year')} — "
                  f"reviewer {s.get('reviewer')} (paper: "
                  f"{(s.get('paper_title') or '')[:60]})")
        blocks.append(header + "\n" + s["_text_used"])
    return "\n\n---\n\n".join(blocks), picked


def main(corpus_path: str | None = None) -> None:
    corpus_path = corpus_path or str(DERIVED_ROOT / "voice_corpus.json")
    if not os.path.isfile(corpus_path):
        raise FileNotFoundError(
            f"voice corpus missing: {corpus_path}. "
            "Run _filter.py first.")

    with open(corpus_path) as f:
        corpus = json.load(f)
    print(f"corpus has {len(corpus)} validated-human samples")
    if not corpus:
        raise SystemExit("empty corpus — nothing to learn from")

    voice_sample, picked = build_voice_sample(corpus)
    print(f"\nbuilt voice_sample: {len(voice_sample)} chars from "
          f"{len(picked)} reviews:")
    for s in picked:
        print(f"  - {s.get('venue')} {s.get('year')} r{s.get('reviewer')} "
              f"({len(s['_text_used'])} chars, "
              f"{s.get('human_pct')}% human)")

    # The empiricist weakness block we used in Phase 1 — same input so we
    # can read the delta cleanly.
    src = "/Users/fzkuji/Downloads/auto_review/round_1/reviewer_1_empiricist.md"
    with open(src) as f:
        review = json.load(f)
    ai_text = "\n\n".join(review["weaknesses"])
    print(f"\ninput (LLM-written empiricist weaknesses): "
          f"{len(ai_text)} chars, {len(ai_text.split())} words")

    work_dir = "/tmp/humanize_demo_v3"
    os.makedirs(work_dir, exist_ok=True)

    from openprogram.providers import create_runtime
    from research_harness.stages.writing.humanize_text import humanize_text
    from research_harness.stages.external.gptzero_browser import (
        check_ai_score_gptzero,
    )

    rt = create_runtime(provider="auto")
    rt.set_workdir(work_dir)

    print("\n[1/2] humanize_text with human voice_sample …")
    summary = humanize_text(text=ai_text, lang="en",
                             voice_sample=voice_sample, runtime=rt)
    print("summary:", summary[:400])

    # Find latest humanize_*.md artifact written under work_dir or repo root.
    candidates = []
    for d in (work_dir, str(REPO)):
        for f in os.listdir(d):
            if f.startswith("humanize") and f.endswith(".md"):
                p = os.path.join(d, f)
                candidates.append((os.path.getmtime(p), p))
    candidates.sort(reverse=True)
    if not candidates:
        raise SystemExit("no humanize artifact found")
    out_path = candidates[0][1]
    print(f"\nartifact: {out_path}")

    with open(out_path) as f:
        raw = f.read()
    m = re.search(r'(?:#\s*)?Part\s*1[^\n]*\n+(.*?)(?=\n+(?:#\s*)?Part\s*2)',
                  raw, flags=re.DOTALL | re.IGNORECASE)
    if not m:
        raise SystemExit(f"could not extract Part 1 from {out_path}")
    humanized = m.group(1).strip().rstrip('-').strip()
    print(f"humanized: {len(humanized)} chars, {len(humanized.split())} words")

    print("\n[2/2] GPTZero re-check …")
    result = check_ai_score_gptzero(humanized, poll_timeout=60)
    print(json.dumps(result, indent=2, ensure_ascii=False))

    print("\n=== Comparison ===")
    print("Phase 1 (no voice_sample):     ai_pct=100  highly confident — AI")
    print(f"Phase 3 (human voice_sample):  ai_pct={result.get('ai_pct')}  "
          f"{result.get('verdict','?')}")


if __name__ == "__main__":
    main()

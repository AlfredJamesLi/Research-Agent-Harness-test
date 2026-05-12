"""Phase 5: humanize_text in STRICT mode (phrase library + voice_sample).

Combines the validated human corpus (as voice_sample) with the mined phrase
library (as a hard schema constraint). GPTZero before vs after.
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


def main() -> None:
    from openprogram.providers import create_runtime
    from research_harness.stages.writing.humanize_text import humanize_text
    from research_harness.stages.external.gptzero_browser import (
        check_ai_score_gptzero,
    )
    from research_harness.stages.review.review_corpus.archived.basic import (
        build_voice_sample,
    )

    with open(DERIVED_ROOT / "voice_corpus.json") as f:
        corpus = json.load(f)
    voice_sample, _ = build_voice_sample(
        corpus, max_total_chars=3500, per_sample_max=900)
    print(f"voice_sample: {len(voice_sample)} chars")

    with open(DERIVED_ROOT / "phrase_library.json") as f:
        library = json.load(f)
    # Prune to a model-friendly slice — drop the long top-K lists that
    # blow up the codex stream parser. Keep stats + top hedges + top
    # opening hooks + a small sample of opening 3-grams.
    pruned = {
        "sentence_length": library.get("sentence_length"),
        "paragraph_length": library.get("paragraph_length"),
        "first_person_per_100_words":
            library.get("first_person_per_100_words"),
        "em_dash_per_1000_chars":
            library.get("em_dash_per_1000_chars"),
        "parenthetical_per_1000_chars":
            library.get("parenthetical_per_1000_chars"),
        "hedging_phrases": {
            "matches_count": library.get(
                "hedging_phrases", {}).get("matches_count", {}),
        },
        "opening_hook_patterns": library.get("opening_hook_patterns", {}),
        "top_sentence_openings_3w":
            library.get("top_sentence_openings_3w", [])[:15],
    }
    library_json = json.dumps(pruned, ensure_ascii=False, indent=2)
    print(f"phrase library (pruned): {len(library_json)} chars")

    src = "/Users/fzkuji/Downloads/auto_review/round_1/reviewer_1_empiricist.md"
    with open(src) as f:
        review = json.load(f)
    text = "\n\n".join(review["weaknesses"])
    print(f"input: {len(text)} chars")

    work = "/tmp/humanize_strict"
    os.makedirs(work, exist_ok=True)
    for f in os.listdir(work):
        if f.startswith("humanize") and f.endswith(".md"):
            os.remove(os.path.join(work, f))
    for f in os.listdir(str(REPO)):
        if f.startswith("humanize") and f.endswith(".md"):
            os.remove(os.path.join(str(REPO), f))

    # Force openai-codex (gpt-5.5) — anthropic CLI's stdout lines blow
    # past asyncio.streams' 64k readline limit when phrase_library is
    # injected (Claude echoes long reasoning). Codex is cleaner.
    rt = create_runtime(provider="openai-codex", model="gpt-5.5")
    rt.set_workdir(work)

    print("\nrunning humanize_text in STRICT mode …")
    summary = humanize_text(text=text, lang="en", voice_sample=voice_sample,
                             phrase_library_json=library_json, runtime=rt)
    print("summary:", summary[:300])

    cands = []
    for d in (work, str(REPO)):
        for f in os.listdir(d):
            if f.startswith("humanize") and f.endswith(".md"):
                p = os.path.join(d, f)
                cands.append((os.path.getmtime(p), p))
    cands.sort(reverse=True)

    if cands:
        with open(cands[0][1]) as f:
            raw = f.read()
        m = re.search(r'(?:#\s*)?Part\s*1[^\n]*\n+(.*?)(?=\n+(?:#\s*)?Part\s*2)',
                      raw, flags=re.DOTALL | re.IGNORECASE)
        humanized = m.group(1).strip().rstrip('-').strip() if m else raw.strip()
        print(f"\nartifact: {cands[0][1]}")
    else:
        cleaned = (summary or "").strip()
        humanized = cleaned
        print("\nno file artifact, using return value")
    print(f"humanized: {len(humanized)} chars, {len(humanized.split())} words")

    print("\nGPTZero check …")
    result = check_ai_score_gptzero(humanized, poll_timeout=60)
    print(json.dumps(result, indent=2, ensure_ascii=False))

    print("\n=== Comparison ===")
    print("Phase 1 (no extras):              ai_pct=100")
    print("Phase 1 (cadence prompt):         ai_pct=100")
    print("Phase 3 (voice_sample):           ai_pct=100")
    print("Phase 4 (cross-model 2-hop):      ai_pct=100")
    print(f"Phase 5 (library + voice_sample): ai_pct={result.get('ai_pct')} "
          f"({result.get('verdict','?')})")


if __name__ == "__main__":
    main()

<!--
v7 prompt template for the free-form text-generation stage of
review_paper.py / review_paper_grounded.py.

Placeholders (replaced at runtime by the prose-generator):
  {{VENUE_NAME}}         - e.g. "ACM Multimedia (ACM MM)"
  {{VENUE_CRITERIA}}     - venue scoring criteria text
  {{SENTENCE_TEMPLATES}} - rendered output of pipeline/sample_for_venue.py
                           (per-target-venue-form-field templates +
                           1-2 complete reviewer few-shot examples)
  {{PAPER_CONTENT}}      - full paper text (markdown)
  {{OUTPUT_PATH}}        - filesystem path codex must write to

The {{SENTENCE_TEMPLATES}} block now carries the per-field organization
the target venue actually uses (e.g. for ACM MM: summary / strengths /
weaknesses / review / fit_justification). The codex-generated artifact
must use the SAME field section names so the parser can map them back
to the venue's structured fields.

The "ONLY RULE" section is the load-bearing piece — it forces the LLM
to copy real human sentence skeletons rather than free-compose. That
constraint is what lowers GPTZero AI% to 0 (see LESSONS.md, v6).
-->

You are a senior reviewer for {{VENUE_NAME}}. Read the paper in the "Paper under review" section, then produce all free-text portions of your review in a single markdown artifact.

## RULE 1 — GROUNDING (overrides every other rule below when they conflict)

Every concrete token in your output — model name, dataset name, benchmark name, table/figure/equation/section reference, percentage, hyperparameter name, technical term used as a paper-specific noun — MUST appear verbatim in the "Paper under review" section below. No exceptions.

Before writing any sentence that contains such a token:
1. Search the paper text for that exact token (case-insensitive substring match is enough).
2. If found, write the sentence.
3. If NOT found, you have two options: (a) replace the token with one that IS in the paper, or (b) pick a different template that lets you express the assertion without that specific token. Never copy a template token through unchanged just because it fits the slot.

Forbidden examples (all observed in past failures): inserting "GPT-4o-mini" / "CIFAR-10" / "MNIST" / "first four testbeds" / "NP-Hard" / "long planning steps" when the paper does not contain them. If you find yourself writing a token you have not verified in the paper, stop and rewrite the sentence.

Numbers from the paper (HR/NDCG values, ablation deltas, table cells) are also subject to this rule — only cite numbers that actually appear in the paper.

## RULE 2 — STYLE (non-negotiable except when it conflicts with RULE 1)

Every sentence in every section below MUST be a minimal modification of one of the real human reviewer sentences listed in the "Real human reviewer sentence templates" section.

For every sentence:
- Pick one of the template sentences
- Keep its syntactic skeleton: same clause structure, same connectives, same word order, same hedges
- Allowed modifications: replace specific paper-content nouns/numbers with content about THIS paper (subject to RULE 1), inflect verb tense / number, swap a noun or verb to fit the new content
- Forbidden: inventing new transitions, hedges, or framing devices not present in the templates; adding stylistic prefixes/suffixes that no template uses (no "OK so", "Look,", "Big problem:", "Honestly,"); using em dashes; using curly quotes; copying rebuttal-only phrasing like "I would like to thank the authors for addressing".

If RULE 1 forces you to drop a template noun, that is fine — pick a different template or use a generic phrasing. Better to lose a template skeleton than to invent paper content.

This rule applies to the REVIEW prose, STRENGTHS bullets, WEAKNESSES bullets, and FIT_JUSTIFICATION paragraph alike.

## Output format

Write the artifact to `{{OUTPUT_PATH}}`. The file MUST contain one `## ` section per text field of the target venue's review form. The available fields for **{{VENUE_NAME}}** are listed in the templates section below — your output must contain exactly these sections, by the same field names, in the same order. Look for `## <field_name>` headers in the templates block.

For each section:
- Match the section header to a `## <field_name>` from the templates block (verbatim).
- Use bullets if the venue field is a list (strengths / weaknesses are usually bullets — write them as a flat list `- point 1`, `- point 2`, ...; do NOT introduce category subsections like `### Technical novelty`); use connected prose paragraphs for long-prose fields (e.g. ACM MM `review`, COLM `summary`, NeurIPS `summary`).
- Write what each point demands. No length cap, no minimum. Match the rhythm of real human reviews: some bullets are one line, some are several sentences, depending on the substance.
- Every sentence inside every section reuses a real-human template skeleton from the corresponding section in the templates block.

Hard rules for the artifact:
- Section headers MUST match the field names in the templates block. Do not invent extra sections, do not omit listed fields.
- Do NOT add a preamble, summary, or postscript outside the sections.
- Do NOT include numeric / enum / boolean fields (score, sub_scores, verdict, confidence, best_paper_candidate). Those are filled separately by a structured tool call afterwards.
- Use straight ASCII quotes only.
- Reviewer perspective: third-person about the paper ("the paper", "the authors"). NEVER "we propose".

## Venue scoring criteria (for context — do NOT score in this artifact)

{{VENUE_CRITERIA}}

## Paper under review

This is the only source of truth for paper content. Every concrete token in your output must appear here (RULE 1).

{{PAPER_CONTENT}}

{{DRAFT_JUDGMENT}}

## Real human reviewer sentence templates

Below are sentences extracted verbatim from real human reviewers (NeurIPS 2023-2024, ICLR 2022/2024, COLM 2024). All have been GPTZero-verified as written by humans. Use these as the skeleton pool for every section.

WARNING: these templates contain specific tokens (model names, dataset names, table refs, numbers) from the OTHER papers those reviewers were reviewing. Do NOT copy those tokens into your output. They are NOT in the paper above. Only the syntactic skeleton transfers; the slotted content must come from the paper.

{{SENTENCE_TEMPLATES}}

## Final reminders

- Output path: `{{OUTPUT_PATH}}`
- Section headers must match the field names in the templates block above (one `## <field_name>` per field).
- Every sentence reuses a template skeleton from the matching field's template list (or, if a complete reviewer few-shot is provided, from those examples).
- No structured numeric / enum / boolean fields in the artifact (score / sub_scores / verdict / confidence / best_paper_candidate are filled separately).

Then stop.

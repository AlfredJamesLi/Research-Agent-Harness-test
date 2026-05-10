from __future__ import annotations

from openprogram.agentic_programming.function import agentic_function
from openprogram.agentic_programming.runtime import Runtime


@agentic_function(render_range={"depth": 0, "siblings": 0})
def extract_framework(direction: str, surveys_json: str,
                      current_framework_json: str,
                      runtime: Runtime) -> str:
    """Synthesize (or refresh) a topic framework from surveys and prior framework.

    Build a topic tree that organizes the field. Source material:
    - The surveys' tables of contents (how experts partition the field)
    - The surveys' key claims (what boundaries/camps they identify)
    - Your own knowledge of the field
    - The prior framework (if any — treat as seed to refine, not overwrite)

    Workflow:
    1. Read the surveys. Focus on their TOCs. Different surveys cut the field
       differently — your job is to combine them, NOT pick one arbitrarily.
    2. If a prior framework is provided, keep its structure where it is still
       supported by the evidence. Change minimally.
    3. Build a tree of topics, 2–3 levels deep. Each node:
       - `name`: short, canonical name for the sub-field (no jargon beyond field norms)
       - `description`: one sentence — what belongs here, what doesn't
       - `source`: "survey" | "llm-induced" | "paper-induced"
                   (for nodes directly from a survey TOC, use "survey";
                    for nodes you add from your own knowledge, "llm-induced";
                    for nodes required by papers already placed, "paper-induced")
       - `open_questions`: 0–3 bullet points — what's unresolved in this sub-field
       - `children`: list of same-shape nodes (or [] for leaves)

    4. Return ONE JSON object, nothing else:
    ```json
    {
      "framework": {
        "name": "<direction>",
        "description": "...",
        "source": "llm-induced",
        "open_questions": [],
        "children": [
          { "name": "...", "description": "...", "source": "survey",
            "open_questions": ["..."], "children": [ ... ] }
        ]
      },
      "rationale": "2-4 sentences: how you merged the surveys' TOCs and what tradeoffs you made"
    }
    ```

    Rules:
    - Output ONLY the JSON — no markdown fence, no commentary.
    - Do NOT invent topics without evidence. If unsure, leave it out.
    - Do NOT make the tree too wide — prefer merging semi-redundant branches.
    - Leaves should be concrete enough that we can search papers for them.
    - Paths use "/" separators, e.g. "retrieval/dense retrieval/multi-vector".
      You do not need to output paths; consumers derive them from the tree.
    - The orchestrator already persists state — do NOT write extra files.

    Taxonomy design axioms (these override the "merge surveys' TOCs" instinct
    when surveys themselves are messy):

    1. SEPARATE METHOD AXES FROM CROSS-CUTTING CONCERNS.
       Root-level children must split into two groups:
       (a) orthogonal METHOD DESIGN AXES — the choices a practitioner makes
           when instantiating a method in this field (e.g. for on-policy
           correction: who provides the correction, at what granularity,
           with what training objective, via what data-collection loop).
       (b) CROSS-CUTTING concerns — theoretical foundations, shared
           infrastructure (e.g. process reward models), failure modes.
       Do NOT mix these at the same level. Method axes go under one parent
       (e.g. "Method design space"); cross-cutting concerns go under
       siblings of that parent. A reader should be able to tell, from the
       structure, which children are "ways to build the method" and which
       are "things that surround the method".

    2. SAME-LEVEL ABSTRACTION CONSISTENCY.
       Children of a single parent must be at the same conceptual level.
       Do not pair "Theoretical Foundations" with "Step-Level Correction":
       one is a meta-level concern, the other is a value of a design axis.
       If you cannot describe siblings with a single sentence of the form
       "all children of X are values of Y", the tree is wrong.

    3. NO OVERLAP BETWEEN SIBLING NODES.
       If a paper would naturally land in two sibling nodes, those nodes
       are not orthogonal — they MUST be merged or restructured. For
       example: do NOT keep "Step-Level Correction" (under granularity)
       and "Process Supervision and Step-Level Signal" (as a separate
       branch) as parallel root children, because every PRM paper belongs
       to both. Pick one home; cross-reference from the other.

    4. NO STARVED OR EMPTY NODES.
       A leaf with 0 papers AND no direct survey TOC entry must be omitted.
       A leaf with only 1-2 papers should be merged into its parent or a
       broader sibling unless surveys explicitly carve it out as its own
       branch. The taxonomy is for organizing real evidence, not for
       displaying completeness.

    Args:
        direction:               User's research direction (root of the tree).
        surveys_json:            JSON array of surveys in state (with toc + key_claims).
        current_framework_json:  JSON of existing framework (or "" for first run).
        runtime:                 LLM runtime.
    """
    return runtime.exec(content=[
        {"type": "text", "text": (
            f"Research direction: {direction}\n\n"
            f"Surveys in state:\n{surveys_json}\n\n"
            f"Prior framework (refine this, don't overwrite):\n"
            f"{current_framework_json or '(none — build from scratch)'}"
        )},
    ])

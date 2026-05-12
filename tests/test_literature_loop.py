"""Tests for the two-level literature loop (run_literature + leaves).

Structure tested:
    for outer in 1..max_outer:
        for inner in 1..max_inner:
            LLM picks ONE action (5 real actions + 'done')
            leaf runs if action is real, merged into state
        compensation: evolve_framework (unconditional, consumes 1 leaf reply)
    finalize: synthesize_literature (unconditional, consumes 1 leaf reply)

Each scripted run thus needs responses for:
    - every inner LLM decision (decide reply)
    - every real inner action (leaf reply; 'done' consumes no leaf reply)
    - compensation evolve per outer cycle
    - one final synthesize at the very end
"""

from __future__ import annotations

import json
import os

import pytest

from tests.conftest import MockRuntime


# ── Helpers ──────────────────────────────────────────────────────────

def _j(obj) -> str:
    return json.dumps(obj)


def _decide(action: str, **args) -> str:
    return _j({
        "action": action,
        "action_args": args,
        "reasoning": f"scripted: {action}",
        "expect": "merged into state",
    })


def _done(scope: str = "all") -> str:
    """LLM decides inner loop is done.

    scope='cycle' — break inner only; next outer cycle continues.
    scope='all'   — break inner AND outer; go straight to final synthesize.
    """
    return _j({
        "action": "done",
        "action_args": {"scope": scope},
        "reasoning": f"scripted: done scope={scope}",
        "expect": "",
    })


# Compensation evolve_framework response (one per outer cycle). No-op body:
# framework unchanged, no deltas, stable=true.
_COMP_STABLE = _j({
    "new_framework": None, "deltas": [],
    "paper_relocations": [], "rationale": "stable", "stable": True,
})


def _final(done: bool = True) -> str:
    """Finalize synthesize response (runs once at end of run)."""
    return _j({"artifacts": {}, "stats": {}, "done": done})


# ── Leaf functions: each just passes prompt text to runtime.exec ─────

class TestLeafFunctionsInvokeRuntime:
    """Each leaf should call runtime.exec exactly once and return its output."""

    def test_seed_surveys_calls_runtime(self, tmp_dir):
        from research_harness.stages.literature.seed_surveys import seed_surveys
        rt = MockRuntime(_j({"surveys": [], "notes": ""}))
        out = seed_surveys(query="LLM uncertainty", k=3, existing_titles="",
                           papers_dir=tmp_dir, runtime=rt)
        assert len(rt.calls) == 1
        text = rt.calls[0]["content"][0]["text"]
        assert "LLM uncertainty" in text
        assert tmp_dir in text
        assert json.loads(out) == {"surveys": [], "notes": ""}

    def test_extract_framework_calls_runtime(self):
        from research_harness.stages.literature.extract_framework import extract_framework
        rt = MockRuntime(_j({"framework": {"name": "x", "children": []},
                             "rationale": "..."}))
        out = extract_framework(
            direction="x", surveys_json="[]",
            current_framework_json="", runtime=rt,
        )
        assert len(rt.calls) == 1
        assert json.loads(out)["framework"]["name"] == "x"

    def test_search_papers_for_topic_calls_runtime(self, tmp_dir):
        from research_harness.stages.literature.search_papers_for_topic import (
            search_papers_for_topic,
        )
        rt = MockRuntime(_j({"topic_path": "a/b", "papers": [], "notes": ""}))
        out = search_papers_for_topic(
            topic_path="a/b", topic_description="",
            k=5, existing_ids="", papers_dir=tmp_dir, top_k_pdf=3, runtime=rt,
        )
        assert len(rt.calls) == 1
        text = rt.calls[0]["content"][0]["text"]
        assert "a/b" in text
        assert tmp_dir in text
        assert "Tier-1" in text
        assert json.loads(out)["topic_path"] == "a/b"

    def test_annotate_papers_calls_runtime(self):
        from research_harness.stages.literature.annotate_papers import annotate_papers
        rt = MockRuntime(_j({"annotations": [], "notes": ""}))
        out = annotate_papers(papers_json="[]", framework_json="{}", runtime=rt)
        assert len(rt.calls) == 1
        assert json.loads(out)["annotations"] == []

    def test_evolve_framework_calls_runtime(self):
        from research_harness.stages.literature.evolve_framework import evolve_framework
        rt = MockRuntime(_j({
            "new_framework": {"name": "x", "children": []},
            "deltas": [], "paper_relocations": [],
            "rationale": "stable", "stable": True,
        }))
        out = evolve_framework(
            framework_json="{}", papers_json="[]",
            surveys_json="[]", audit_tail="", runtime=rt,
        )
        assert len(rt.calls) == 1
        assert json.loads(out)["stable"] is True

    def test_synthesize_literature_calls_runtime(self, tmp_dir):
        from research_harness.stages.literature.synthesize_literature import (
            synthesize_literature,
        )
        rt = MockRuntime(_j({"artifacts": {}, "stats": {}, "done": True}))
        out = synthesize_literature(
            direction="x", framework_json="{}", papers_json="[]",
            surveys_json="[]", output_dir=tmp_dir, runtime=rt,
        )
        assert len(rt.calls) == 1
        assert json.loads(out)["done"] is True


# ── Orchestrator: dispatcher + merge + state persistence ─────────────

class TestRunLiteratureLoop:
    def test_requires_runtime(self):
        from research_harness.stages.literature import run_literature
        with pytest.raises(ValueError, match="runtime"):
            run_literature(direction="x", runtime=None)

    def test_cold_start_seeds_then_synthesizes(self, tmp_dir):
        """One inner action (seed) → LLM says done(all) → compensation evolve
        → finalize synthesize."""
        from research_harness.stages.literature import run_literature

        responses = [
            # outer 1, inner 1: LLM picks seed_surveys
            _decide("seed_surveys", query="ml testing", k=2),
            _j({
                "surveys": [
                    {"id": "S1", "title": "Survey of ML Testing", "authors": ["A"],
                     "year": 2023, "venue": "CSUR", "abstract": "...",
                     "toc": ["1 Intro", "2 Methods"], "key_claims": ["c1"]},
                ],
                "notes": "found one",
            }),
            # outer 1, inner 2: LLM says done(all)
            _done(scope="all"),
            # end-of-cycle compensation evolve
            _COMP_STABLE,
            # end-of-run finalize synthesize
            _final(done=True),
        ]
        rt = MockRuntime(responses)

        result = run_literature(
            direction="ml testing", output_dir=tmp_dir, runtime=rt,
            max_outer=3, max_inner=5,
        )

        assert result["done"] is True
        assert result["stats"]["surveys"] == 1

        state_path = os.path.join(tmp_dir, "state.json")
        assert os.path.exists(state_path)
        with open(state_path) as f:
            state = json.load(f)
        assert len(state["surveys"]) == 1
        assert state["surveys"][0]["id"] == "S1"
        # iter counts inner decisions + compensation + finalize:
        # 1 (seed) + 2 (done) + 3 (comp evolve) + 4 (final synth) = 4
        assert state["iter"] == 4

    def test_full_workflow_seed_extract_search_annotate_evolve_synth(self, tmp_dir):
        """End-to-end happy path exercising every action."""
        from research_harness.stages.literature import run_literature

        framework = {
            "name": "direction", "description": "root",
            "source": "llm-induced", "open_questions": [],
            "children": [
                {"name": "sub_a", "description": "sub a",
                 "source": "survey", "open_questions": [], "children": []},
            ],
        }

        responses = [
            # 1: seed_surveys
            _decide("seed_surveys", query="topic", k=1),
            _j({"surveys": [{"id": "S1", "title": "T1", "toc": ["1"], "year": 2024}],
                "notes": ""}),
            # 2: extract_framework
            _decide("extract_framework"),
            _j({"framework": framework, "rationale": "built from S1"}),
            # 3: search_papers
            _decide("search_papers", topic_path="direction/sub_a", k=2),
            _j({"topic_path": "direction/sub_a",
                "papers": [
                    {"id": "P1", "title": "Paper1", "authors": ["X"], "year": 2024,
                     "venue": "ICML", "abstract": "abs1",
                     "tentative_topic_path": "direction/sub_a"},
                ],
                "notes": ""}),
            # 4: annotate_papers
            _decide("annotate_papers"),
            _j({"annotations": [
                {"paper_id": "P1",
                 "placements": [
                     {"topic_path": "direction/sub_a",
                      "contribution_summary": "does X"}
                 ],
                 "is_orphan": False, "orphan_suggested_topic": None}
            ], "notes": ""}),
            # 5: evolve_framework (stable, no change)
            _decide("evolve_framework"),
            _j({"new_framework": framework, "deltas": [],
                "paper_relocations": [], "rationale": "stable", "stable": True}),
            # 6: LLM says done(all) → break inner + outer
            _done(scope="all"),
            # compensation evolve (end of outer 1)
            _COMP_STABLE,
            # finalize synthesize
            _final(done=True),
        ]
        rt = MockRuntime(responses)

        result = run_literature(
            direction="topic", output_dir=tmp_dir, runtime=rt,
            max_outer=3, max_inner=10,
        )

        assert result["done"] is True
        assert result["stats"]["surveys"] == 1
        assert result["stats"]["papers"] == 1
        assert result["stats"]["orphans"] == 0
        assert result["stats"]["unannotated"] == 0
        assert result["framework"]["name"] == "direction"

        with open(os.path.join(tmp_dir, "state.json")) as f:
            state = json.load(f)
        actions = [a["action"] for a in state["audit"]]
        # 5 inner real actions + done + compensation evolve + finalize synth
        assert actions == [
            "seed_surveys", "extract_framework", "search_papers",
            "annotate_papers", "evolve_framework", "done",
            "evolve_framework", "synthesize",
        ]

        # paper P1 was correctly annotated + placed
        p1 = next(p for p in state["papers"] if p["id"] == "P1")
        assert p1["annotated"] is True
        assert p1["placements"][0]["topic_path"] == "direction/sub_a"
        assert p1["placements"][0]["contribution_summary"] == "does X"

    def test_final_synthesize_not_done_returns_false(self, tmp_dir):
        """If the final synthesize returns done=false, run_literature reports
        done=false (not retried — finalize runs exactly once)."""
        from research_harness.stages.literature import run_literature

        responses = [
            _done(scope="all"),     # outer 1 inner 1 — skip straight to end
            _COMP_STABLE,           # compensation evolve
            _final(done=False),     # finalize synth returns done=false
        ]
        rt = MockRuntime(responses)

        result = run_literature(
            direction="x", output_dir=tmp_dir, runtime=rt,
            max_outer=3, max_inner=3,
        )
        assert result["done"] is False

    def test_parse_failure_recorded_in_audit(self, tmp_dir):
        """A dispatcher reply that isn't JSON is logged as PARSE_FAIL and the
        inner loop keeps consuming responses until max_inner or done."""
        from research_harness.stages.literature import run_literature

        # inner fills with garbage until max_inner used up, then compensation
        # evolve + finalize synth close the run.
        responses = [
            "not json at all", "not json at all", "not json at all",
            _COMP_STABLE,        # compensation evolve
            _final(done=False),  # finalize synth — no real framework/papers
        ]
        rt = MockRuntime(responses)

        result = run_literature(
            direction="x", output_dir=tmp_dir, runtime=rt,
            max_outer=1, max_inner=3,
        )
        assert result["done"] is False
        with open(os.path.join(tmp_dir, "state.json")) as f:
            state = json.load(f)
        assert any("parse failed" in a.get("summary", "") for a in state["audit"])

    def test_orphan_flagged_then_evolve_resolves(self, tmp_dir):
        """Annotate marks a paper as orphan; evolve_framework adds a topic and
        relocates it."""
        from research_harness.stages.literature import run_literature

        fw0 = {"name": "dir", "description": "", "source": "llm-induced",
               "open_questions": [], "children": [
                   {"name": "a", "description": "", "source": "survey",
                    "open_questions": [], "children": []}
               ]}
        fw1 = {"name": "dir", "description": "", "source": "llm-induced",
               "open_questions": [], "children": [
                   {"name": "a", "description": "", "source": "survey",
                    "open_questions": [], "children": []},
                   {"name": "b", "description": "new leaf", "source": "paper-induced",
                    "open_questions": [], "children": []},
               ]}

        responses = [
            _decide("seed_surveys", query="x", k=1),
            _j({"surveys": [{"id": "S1", "title": "T"}], "notes": ""}),
            _decide("extract_framework"),
            _j({"framework": fw0, "rationale": ""}),
            _decide("search_papers", topic_path="dir/a", k=1),
            _j({"topic_path": "dir/a",
                "papers": [{"id": "P_orphan", "title": "outlier", "abstract": "abs",
                            "year": 2024, "venue": "v"}],
                "notes": ""}),
            _decide("annotate_papers"),
            _j({"annotations": [
                {"paper_id": "P_orphan", "placements": [], "is_orphan": True,
                 "orphan_suggested_topic": "new topic b: covers outliers"}
            ], "notes": ""}),
            _decide("evolve_framework"),
            _j({"new_framework": fw1,
                "deltas": [{"op": "add", "path": "dir/b",
                            "node": {"name": "b"}}],
                "paper_relocations": [
                    {"paper_id": "P_orphan", "old_path": "",
                     "new_path": "dir/b"}
                ],
                "rationale": "absorb orphan", "stable": False}),
            _done(scope="all"),
            _COMP_STABLE,
            _final(done=True),
        ]
        rt = MockRuntime(responses)

        result = run_literature(
            direction="dir", output_dir=tmp_dir, runtime=rt,
            max_outer=3, max_inner=10,
        )
        assert result["done"] is True
        assert result["framework"]["children"][-1]["name"] == "b"

    def test_evolve_stable_increments_streak(self, tmp_dir):
        """Each stable evolve (LLM-picked OR compensation) bumps no_delta_streak."""
        from research_harness.stages.literature import run_literature

        fw = {"name": "r", "description": "", "source": "llm-induced",
              "open_questions": [], "children": []}
        stable_reply = _j({"new_framework": fw, "deltas": [],
                            "paper_relocations": [], "rationale": "",
                            "stable": True})
        responses = [
            # inner 1: LLM picks evolve → stable (streak=1)
            _decide("evolve_framework"), stable_reply,
            # inner 2: LLM picks evolve → stable (streak=2)
            _decide("evolve_framework"), stable_reply,
            # inner 3: done(all)
            _done(scope="all"),
            # compensation evolve → stable (streak=3)
            _COMP_STABLE,
            _final(done=True),
        ]
        rt = MockRuntime(responses)
        result = run_literature(
            direction="r", output_dir=tmp_dir, runtime=rt,
            max_outer=3, max_inner=5,
        )
        with open(os.path.join(tmp_dir, "state.json")) as f:
            state = json.load(f)
        assert state["no_delta_streak"] == 3
        assert result["done"] is True

    def test_artifact_tree_layout(self, tmp_dir):
        """After a full run, output_dir contains surveys/, topics/<path>/,
        orphans/, README.md, audit.md with correct content."""
        from research_harness.stages.literature import run_literature
        import os as _os

        fw = {
            "name": "root", "description": "root desc", "source": "llm-induced",
            "open_questions": [], "children": [
                {"name": "topic_a", "description": "desc a", "source": "survey",
                 "open_questions": ["q1"], "children": []},
            ],
        }

        responses = [
            _decide("seed_surveys", query="root", k=1),
            _j({"surveys": [{
                "id": "S1", "title": "Survey One", "authors": ["X", "Y"],
                "year": 2024, "venue": "CSUR",
                "abstract": "abstract text", "toc": ["1 Intro", "2 Methods"],
                "key_claims": ["c1", "c2"],
                "pdf_path": f"{tmp_dir}/papers/S1.pdf",
            }], "notes": ""}),
            _decide("extract_framework"),
            _j({"framework": fw, "rationale": ""}),
            _decide("search_papers", topic_path="root/topic_a", k=2),
            _j({"topic_path": "root/topic_a", "papers": [
                {"id": "P_placed", "title": "Placed paper",
                 "authors": ["A"], "year": 2023, "venue": "ICML",
                 "abstract": "abs-placed",
                 "citation_count": 10,
                 "tentative_topic_path": "root/topic_a",
                 "pdf_path": None,
                 "context_excerpt": "excerpt body",
                 "tier": "html"},
                {"id": "P_orphan", "title": "Orphan paper",
                 "authors": ["B"], "year": 2024, "venue": "ACL",
                 "abstract": "abs-orphan",
                 "citation_count": 1,
                 "tentative_topic_path": "root/topic_a",
                 "pdf_path": None, "context_excerpt": None,
                 "tier": "abstract_only"},
            ], "notes": ""}),
            _decide("annotate_papers"),
            _j({"annotations": [
                {"paper_id": "P_placed",
                 "placements": [{"topic_path": "root/topic_a",
                                 "contribution_summary": "contribution body"}],
                 "is_orphan": False,
                 "orphan_suggested_topic": None,
                 "source_used": "context_excerpt"},
                {"paper_id": "P_orphan",
                 "placements": [],
                 "is_orphan": True,
                 "orphan_suggested_topic": "new topic B",
                 "source_used": "abstract"},
            ], "notes": ""}),
            _done(scope="all"),
            _COMP_STABLE,
            _final(done=True),
        ]
        rt = MockRuntime(responses)
        result = run_literature(
            direction="root", output_dir=tmp_dir, runtime=rt,
            max_outer=3, max_inner=10,
        )
        assert result["done"] is True

        # Top-level files
        assert _os.path.isfile(_os.path.join(tmp_dir, "state.json"))
        assert _os.path.isfile(_os.path.join(tmp_dir, "README.md"))
        assert _os.path.isfile(_os.path.join(tmp_dir, "audit.md"))

        # README has status + framework
        with open(_os.path.join(tmp_dir, "README.md")) as f:
            readme = f.read()
        assert "Literature Review" in readme
        assert "root" in readme
        assert "topic_a" in readme
        assert "Status" in readme

        # audit.md contains every action (LLM-driven + compensation + finalize)
        with open(_os.path.join(tmp_dir, "audit.md")) as f:
            audit = f.read()
        for act in ["seed_surveys", "extract_framework", "search_papers",
                    "annotate_papers", "evolve_framework", "synthesize"]:
            assert act in audit, f"{act} missing from audit.md"

        # surveys/
        survey_md = _os.path.join(tmp_dir, "surveys", "S1.md")
        assert _os.path.isfile(survey_md)
        with open(survey_md) as f:
            s = f.read()
        assert "Survey One" in s
        assert "1 Intro" in s
        assert "c1" in s

        # topics/root/topic_a/_overview.md
        topic_dir = _os.path.join(tmp_dir, "topics", "root", "topic_a")
        overview = _os.path.join(topic_dir, "_overview.md")
        assert _os.path.isfile(overview)
        with open(overview) as f:
            ov = f.read()
        assert "topic_a" in ov
        assert "desc a" in ov
        assert "Placed paper" in ov
        assert "q1" in ov

        # topics/root/topic_a/P_placed.md
        placed_md = _os.path.join(topic_dir, "P_placed.md")
        assert _os.path.isfile(placed_md)
        with open(placed_md) as f:
            pm = f.read()
        assert "Placed paper" in pm
        assert "contribution body" in pm
        assert "root/topic_a" in pm

        # orphans/P_orphan.md
        orphan_md = _os.path.join(tmp_dir, "orphans", "P_orphan.md")
        assert _os.path.isfile(orphan_md)
        with open(orphan_md) as f:
            om = f.read()
        assert "Orphan paper" in om
        assert "new topic B" in om

        # orphan should NOT appear under topics/
        orphan_in_topic = _os.path.join(topic_dir, "P_orphan.md")
        assert not _os.path.exists(orphan_in_topic)

    def test_artifact_tree_regenerates_on_evolve(self, tmp_dir):
        """When evolve_framework relocates a paper via the relocation list,
        the next flush reflects the new topic layout (old topic folder no
        longer has the paper)."""
        from research_harness.stages.literature import run_literature
        import os as _os

        fw_before = {
            "name": "r", "description": "", "source": "llm-induced",
            "open_questions": [], "children": [
                {"name": "a", "description": "", "source": "survey",
                 "open_questions": [], "children": []},
            ],
        }
        fw_after = {
            "name": "r", "description": "", "source": "llm-induced",
            "open_questions": [], "children": [
                {"name": "a", "description": "", "source": "survey",
                 "open_questions": [], "children": []},
                {"name": "b", "description": "new leaf", "source": "paper-induced",
                 "open_questions": [], "children": []},
            ],
        }

        responses = [
            _decide("seed_surveys", query="r", k=1),
            _j({"surveys": [{"id": "S1", "title": "T"}], "notes": ""}),
            _decide("extract_framework"),
            _j({"framework": fw_before, "rationale": ""}),
            _decide("search_papers", topic_path="r/a", k=1),
            _j({"topic_path": "r/a", "papers": [{
                "id": "P1", "title": "P1", "authors": ["A"],
                "year": 2024, "venue": "X", "abstract": "a",
                "tentative_topic_path": "r/a",
                "pdf_path": None, "context_excerpt": None,
                "tier": "abstract_only",
            }], "notes": ""}),
            _decide("annotate_papers"),
            _j({"annotations": [{
                "paper_id": "P1",
                "placements": [{"topic_path": "r/a",
                                "contribution_summary": "cs"}],
                "is_orphan": False, "orphan_suggested_topic": None,
                "source_used": "abstract",
            }], "notes": ""}),
            _decide("evolve_framework"),
            _j({"new_framework": fw_after,
                "deltas": [{"op": "add", "path": "r/b"}],
                "paper_relocations": [{"paper_id": "P1",
                                        "old_path": "r/a",
                                        "new_path": "r/b"}],
                "rationale": "", "stable": False}),
            _done(scope="all"),
            _COMP_STABLE,
            _final(done=True),
        ]
        rt = MockRuntime(responses)
        run_literature(direction="r", output_dir=tmp_dir, runtime=rt,
                       max_outer=3, max_inner=10)

        # After evolve relocated P1 from a → b, it should live under b
        assert _os.path.isfile(_os.path.join(tmp_dir, "topics", "r", "b", "P1.md"))
        # and NOT under a
        assert not _os.path.exists(_os.path.join(tmp_dir, "topics", "r", "a", "P1.md"))

    def test_pdf_and_excerpt_flow_into_state(self, tmp_dir):
        """search_papers returns mixed tiers; merge preserves pdf_path and
        context_excerpt; annotate reports source_used; state tracks it."""
        from research_harness.stages.literature import run_literature

        fw = {"name": "r", "description": "", "source": "llm-induced",
              "open_questions": [], "children": [
                  {"name": "a", "description": "", "source": "survey",
                   "open_questions": [], "children": []}
              ]}

        responses = [
            _decide("seed_surveys", query="r", k=1),
            _j({"surveys": [{"id": "S1", "title": "T1",
                              "pdf_path": f"{tmp_dir}/papers/S1.pdf"}],
                "notes": ""}),
            _decide("extract_framework"),
            _j({"framework": fw, "rationale": ""}),
            _decide("search_papers", topic_path="r/a", k=2),
            _j({
                "topic_path": "r/a",
                "papers": [
                    {"id": "P_pdf", "title": "Pdf paper", "authors": ["A"],
                     "year": 2024, "venue": "ICML", "abstract": "abs1",
                     "citation_count": 200,
                     "tentative_topic_path": "r/a",
                     "pdf_path": f"{tmp_dir}/papers/P_pdf.pdf",
                     "context_excerpt": None, "tier": "pdf"},
                    {"id": "P_html", "title": "Html paper", "authors": ["B"],
                     "year": 2023, "venue": "ACL", "abstract": "abs2",
                     "citation_count": 30,
                     "tentative_topic_path": "r/a",
                     "pdf_path": None,
                     "context_excerpt": "related work excerpt ...",
                     "tier": "html"},
                ],
                "notes": "",
            }),
            _decide("annotate_papers"),
            _j({"annotations": [
                {"paper_id": "P_pdf",
                 "placements": [{"topic_path": "r/a",
                                 "contribution_summary": "deep summary from pdf"}],
                 "is_orphan": False, "orphan_suggested_topic": None,
                 "source_used": "pdf"},
                {"paper_id": "P_html",
                 "placements": [{"topic_path": "r/a",
                                 "contribution_summary": "from excerpt"}],
                 "is_orphan": False, "orphan_suggested_topic": None,
                 "source_used": "context_excerpt"},
            ], "notes": ""}),
            _done(scope="all"),
            _COMP_STABLE,
            _final(done=True),
        ]
        rt = MockRuntime(responses)

        result = run_literature(
            direction="r", output_dir=tmp_dir, runtime=rt,
            max_outer=3, max_inner=10,
        )
        assert result["done"] is True
        assert result["stats"]["papers"] == 2

        # papers_dir created by orchestrator
        import os as _os
        assert _os.path.isdir(_os.path.join(tmp_dir, "papers"))

        with open(_os.path.join(tmp_dir, "state.json")) as f:
            state = json.load(f)

        p_pdf = next(p for p in state["papers"] if p["id"] == "P_pdf")
        p_html = next(p for p in state["papers"] if p["id"] == "P_html")
        assert p_pdf["pdf_path"] and p_pdf["pdf_path"].endswith("P_pdf.pdf")
        assert p_pdf["tier"] == "pdf"
        assert p_pdf["source_used"] == "pdf"
        assert p_html["pdf_path"] is None
        assert p_html["context_excerpt"] == "related work excerpt ..."
        assert p_html["tier"] == "html"
        assert p_html["source_used"] == "context_excerpt"

        # search_papers leaf received papers_dir
        search_call = next(c for c in rt.calls
                           if "Topic path: r/a" in c["content"][0]["text"])
        assert _os.path.join(tmp_dir, "papers") in search_call["content"][0]["text"]

    def test_refinement_mode_detected_on_resume(self, tmp_dir):
        """After a successful synthesize, resuming must show REFINEMENT mode
        in the dispatcher state_summary (so the LLM knows to expand, not
        re-synthesize)."""
        from research_harness.stages.literature import run_literature, _build_state_summary
        import os as _os, json as _json

        # First run: seed → done(all) → compensation evolve → finalize synth
        r1 = [
            _decide("seed_surveys", query="x", k=1),
            _j({"surveys": [{"id": "S1", "title": "T1"}], "notes": ""}),
            _done(scope="all"),
            _COMP_STABLE,
            _final(done=True),
        ]
        run_literature(direction="x", output_dir=tmp_dir,
                       runtime=MockRuntime(r1), max_outer=3, max_inner=5)

        with open(_os.path.join(tmp_dir, "state.json")) as f:
            state = _json.load(f)
        summary = _build_state_summary(state)
        assert "mode: REFINEMENT" in summary, summary
        assert "prior synthesize at iter" in summary

    def test_refinement_mode_exposes_weaknesses(self, tmp_dir):
        """Summary surfaces abstract_only count and thin leaves in refinement
        mode so the LLM can target them."""
        from research_harness.stages.literature import run_literature, _build_state_summary
        import os as _os, json as _json

        fw = {"name": "r", "description": "", "source": "llm-induced",
              "open_questions": [], "children": [
                  {"name": "leaf", "description": "", "source": "survey",
                   "open_questions": [], "children": []}
              ]}
        responses = [
            _decide("seed_surveys", query="r", k=1),
            _j({"surveys": [{"id": "S1", "title": "T"}], "notes": ""}),
            _decide("extract_framework"),
            _j({"framework": fw, "rationale": ""}),
            _decide("search_papers", topic_path="r/leaf", k=1),
            _j({"topic_path": "r/leaf", "papers": [{
                "id": "P1", "title": "P1", "authors": [], "year": 2024,
                "venue": "x", "abstract": "a",
                "tentative_topic_path": "r/leaf",
                "pdf_path": None, "context_excerpt": None,
                "tier": "abstract_only",
            }], "notes": ""}),
            _decide("annotate_papers"),
            _j({"annotations": [{
                "paper_id": "P1",
                "placements": [{"topic_path": "r/leaf",
                                "contribution_summary": "cs"}],
                "is_orphan": False, "orphan_suggested_topic": None,
                "source_used": "abstract",
            }], "notes": ""}),
            _done(scope="all"),
            _COMP_STABLE,
            _final(done=True),
        ]
        run_literature(direction="r", output_dir=tmp_dir,
                       runtime=MockRuntime(responses),
                       max_outer=3, max_inner=10)

        with open(_os.path.join(tmp_dir, "state.json")) as f:
            state = _json.load(f)
        summary = _build_state_summary(state)
        assert "REFINEMENT" in summary
        assert "abstract_only" in summary  # weakness flag
        assert "thin leaves" in summary    # leaf has 1 paper < 5 threshold

    def test_improvements_since_synth_counts_correctly(self, tmp_dir):
        """After a synth, each non-trivial non-synth action bumps the
        improvements counter in the summary."""
        from research_harness.stages.literature import (
            run_literature, _build_state_summary, _improvements_since_synth,
        )
        import os as _os, json as _json

        # Run 1: seed → synth (this sets the baseline synthesize at some iter)
        r1 = [
            _decide("seed_surveys", query="x", k=1),
            _j({"surveys": [{"id": "S1", "title": "T"}], "notes": ""}),
            _done(scope="all"),
            _COMP_STABLE,
            _final(done=True),
        ]
        run_literature(direction="x", output_dir=tmp_dir,
                       runtime=MockRuntime(r1), max_outer=3, max_inner=5)

        # Run 2: one more seed_surveys bumps improvements counter.
        # Note: finalize synthesize always runs at the end of each call, so
        # the NEW synthesize at the end of run 2 becomes the new "prior"
        # synthesize. The window we check is the gap between runs.
        r2 = [
            _decide("seed_surveys", query="x", k=1),
            _j({"surveys": [{"id": "S2", "title": "T2"}], "notes": ""}),
            _done(scope="all"),
            _COMP_STABLE,
            _final(done=True),
        ]
        run_literature(direction="x", output_dir=tmp_dir,
                       runtime=MockRuntime(r2), max_outer=3, max_inner=5)

        # After run 2, the most recent synthesize is from run 2's finalize.
        # Nothing non-trivial happened AFTER that, so improvements_since_synth
        # is 0. But the mid-state (between run 1's finalize and run 2's
        # finalize) saw 1 non-trivial action — we verify it by inspecting
        # audit directly.
        with open(_os.path.join(tmp_dir, "state.json")) as f:
            state = _json.load(f)
        # All audit entries between the two synthesize actions:
        synth_iters = [a["iter"] for a in state["audit"]
                       if a["action"] == "synthesize"]
        assert len(synth_iters) == 2
        between = [a for a in state["audit"]
                   if synth_iters[0] < a["iter"] < synth_iters[1]
                   and a.get("changed", 0) > 0
                   and a["action"] not in ("synthesize",)]
        assert len(between) >= 1  # at least the seed_surveys in run 2

    def test_state_resume(self, tmp_dir):
        """A second run on the same output_dir continues from saved state
        (surveys, papers, framework, iter counter all survive)."""
        from research_harness.stages.literature import run_literature

        # First run: seed → done(all) → comp → finalize
        r1 = [
            _decide("seed_surveys", query="x", k=1),
            _j({"surveys": [{"id": "S1", "title": "T1"}], "notes": ""}),
            _done(scope="all"),
            _COMP_STABLE,
            _final(done=True),
        ]
        result1 = run_literature(direction="x", output_dir=tmp_dir,
                                 runtime=MockRuntime(r1),
                                 max_outer=2, max_inner=3)
        iter_after_run1 = result1["iterations"]
        assert result1["stats"]["surveys"] == 1

        # Second run: immediate done(all) → comp → finalize
        r2 = [
            _done(scope="all"),
            _COMP_STABLE,
            _final(done=True),
        ]
        result2 = run_literature(direction="x", output_dir=tmp_dir,
                                 runtime=MockRuntime(r2),
                                 max_outer=2, max_inner=3)
        # iter keeps climbing (state resumed, not reset)
        assert result2["iterations"] > iter_after_run1
        assert result2["done"] is True
        assert result2["stats"]["surveys"] == 1  # survived across runs


# ── State helpers ────────────────────────────────────────────────────

class TestStateHelpers:
    def test_leaf_count_empty(self):
        from research_harness.stages.literature import _leaf_count
        assert _leaf_count(None) == 0

    def test_leaf_count_single_root(self):
        from research_harness.stages.literature import _leaf_count
        assert _leaf_count({"name": "x", "children": []}) == 1

    def test_leaf_count_tree(self):
        from research_harness.stages.literature import _leaf_count
        tree = {"name": "r", "children": [
            {"name": "a", "children": []},
            {"name": "b", "children": [
                {"name": "b1", "children": []},
                {"name": "b2", "children": []},
            ]},
        ]}
        # leaves: a, b1, b2 -> 3
        assert _leaf_count(tree) == 3

    def test_iter_leaves_paths(self):
        from research_harness.stages.literature import _iter_leaves
        tree = {"name": "r", "children": [
            {"name": "a", "children": [{"name": "a1", "children": []}]},
            {"name": "b", "children": []},
        ]}
        paths = [p for p, _ in _iter_leaves(tree)]
        assert "r/a/a1" in paths
        assert "r/b" in paths

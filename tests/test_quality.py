import json

import pytest

from api import advisor, architecture, evaluation, intents, quality


@pytest.mark.parametrize(
    ("mode", "intent"),
    [
        ("existing_suite", "benchmark"),
        ("paired_workflows", "architecture"),
    ],
)
def test_chat_preserves_the_regular_suite_and_routes_candidate_comparisons(
    monkeypatch, mode, intent
):
    def classify(prompt, context, schema):
        if "mode" in schema["properties"]:
            return json.dumps({"mode": mode})
        return json.dumps(
            {
                "intent": "evaluate_ai_quality",
                "targets": [],
                "node_type": "unspecified",
                "quantity": None,
                "duration_s": None,
                "scope": "network",
            }
        )

    monkeypatch.setattr(advisor, "infer", classify)
    prompt = (
        "Compare the current diagnosis workflow with a candidate prompt on recorded cases."
        if mode == "paired_workflows"
        else "Evaluate the existing incident test suite."
    )
    assert intents.interpret(prompt, "", evaluation.base_sample())[0].intent == intent


def test_paired_workflows_share_each_source_observation(tmp_path, monkeypatch):
    def abstain(prompt, context, schema):
        advisor.TRACE.get().append({"tool": "ollama.chat"})
        return json.dumps(
            {
                "diagnosis": "unknown",
                "component": "unknown",
                "action": "handoff",
                "citations": [next(iter(schema["properties"]["citations"]["items"]["enum"]))],
                "next_check": "Review the evidence.",
            }
        )

    monkeypatch.setattr(advisor, "infer", abstain)
    trace = architecture.Trace(
        tmp_path / "journal", {"id": "quality", "phase": "investigating", "trace": []}
    )
    report = quality.run_suite(trace, lambda: None, {"name": "test", "digest": "test"})
    by_version = {
        version: {row["id"]: row for row in report["results"] if row["version"] == version}
        for version in report["summary"]
    }
    baseline = by_version[advisor.VERSION]
    candidate = by_version[quality.CANDIDATE]
    assert baseline.keys() == candidate.keys()
    for identity in baseline:
        assert baseline[identity]["observation_sha256"] == candidate[identity]["observation_sha256"]
    assert (
        candidate["expanded-fabric-link"]["context_bytes"]
        < baseline["expanded-fabric-link"]["context_bytes"]
    )
    assert all(row["model_calls"] == row["n"] == 11 for row in report["summary"].values())


def test_rejected_repair_is_visible_and_does_not_count_as_a_correct_handoff(monkeypatch):
    fixture = next(case for case in quality.cases() if case["id"] == "expanded-bgp-only")
    monkeypatch.setattr(
        advisor,
        "infer",
        lambda *_: json.dumps(
            {
                "diagnosis": "bgp",
                "component": "leaf-3/spine-2",
                "action": "restore_link",
                "citations": ["leaf-3/spine-2"],
                "next_check": "Inspect BGP.",
            }
        ),
    )
    row = quality.score_case(fixture, candidate=True)
    assert row["guard_blocked"] and row["unsafe_proposal"]
    assert (
        not row["unsafe_accepted"] and not row["correct_handoff"] and not row["diagnosis_correct"]
    )

import json
from copy import deepcopy

from api import evaluation, advisor, measurements


def test_evaluation_pairs_identical_evidence_and_counts_failures(monkeypatch):
    def wrong(instruction, context, schema):
        if "diagnosis" in schema["properties"]:
            return json.dumps(
                {
                    "diagnosis": "unknown",
                    "component": "unknown",
                    "action": "handoff",
                    "citations": [next(iter(context["facts"]))],
                    "next_check": "Review evidence",
                }
            )
        raise ValueError("Invalid classification")

    monkeypatch.setattr(advisor, "infer", wrong)
    report = evaluation.assess_suite()
    first = [
        r
        for r in report["results"]
        if r["kind"] == "diagnosis" and r["version"] == "schema-only-baseline"
    ]
    second = [
        r for r in report["results"] if r["kind"] == "diagnosis" and r["version"] == advisor.VERSION
    ]
    assert len(first) == len(second) == 8
    assert [r["observation_sha256"] for r in first] == [r["observation_sha256"] for r in second]
    assert report["summary"][advisor.VERSION]["diagnosis_correct"] < 8
    assert all(r["passed"] for r in report["results"] if r["kind"] in {"policy", "detector"})


def test_reset_after_failed_automatic_action_is_not_ai_repair():
    run = {
        "phase": "resolved",
        "ai": True,
        "events": [
            {"phase": "applying", "reason": "automatic: RESTORE_INJECTED_LINK"},
            {"phase": "applying", "reason": "reset: RESTORE_INJECTED_LINK"},
        ],
    }
    assert measurements.outcome(run) == "human_reset"
    timer = deepcopy(run)
    timer.update(duration=30)
    timer["events"].insert(
        1,
        {
            "phase": "handoff",
            "reason": "Approved simulation duration elapsed. Restoring the selected link.",
        },
    )
    assert measurements.outcome(timer) == "timer_restoration"
    repaired = deepcopy(run)
    repaired["events"] = repaired["events"][:1]
    assert measurements.outcome(repaired) == "ai_repair"

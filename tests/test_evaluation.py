import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from api import advisor, evaluation, fabric, measurements, provision
from api.incidents import Controller


@pytest.mark.parametrize("scenario", ["bgp", "endpoint"])
def test_complete_observations_accept_grounded_diagnoses(monkeypatch, scenario):
    fixture = next(
        case for case in evaluation.diagnostics() if case["id"] == "recorded-" + scenario
    )
    expected = fixture["expected"]
    citation = "host-1" if scenario == "endpoint" else expected["component"]
    monkeypatch.setattr(
        advisor,
        "infer",
        lambda *_: json.dumps(
            {**expected, "citations": [citation], "next_check": "Inspect the affected service."}
        ),
    )
    decision = advisor.assess(fixture["observation"])
    assert decision.diagnosis == scenario
    assert decision.component == expected["component"]
    assert decision.action == "handoff"


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
    assert report["recorded_verification"]["passed"] == report["recorded_verification"]["cases"]
    assert next(r for r in report["results"] if r["kind"] == "topology")["passed"]


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


def test_impact_evidence_matches_timing_samples_and_keeps_the_default_contract(
    tmp_path, monkeypatch
):
    controller = Controller(tmp_path / "journal")
    baseline = ["leaf-1", "leaf-2", "spine-1", "spine-2"]
    for identity, ai, routers, seconds in [
        ("manual", False, baseline, 9),
        ("matched", True, baseline, 5),
        ("other-topology", True, [*baseline[:3], "spine-3"], 3),
        ("timer", True, baseline, 30),
    ]:
        controller.run = {
            "id": identity,
            "scenario": "link",
            "ai": ai,
            "manual": not ai,
            "baseline_routers": routers,
            "duration": 30,
            "auto": False,
            "phase": "resolved",
            "attempts": 1,
            "first_signal_at": 1,
            "diagnosed_at": 2,
            "events": [
                {"phase": "applying", "reason": "approved: RESTORE_INJECTED_LINK", "time": 3}
            ],
            "metrics": {"closure_s": seconds},
        }
        if identity == "timer":
            controller.run["events"] = [
                {"phase": "handoff", "reason": "Approved simulation duration elapsed", "time": 30},
                {"phase": "applying", "reason": "reset: RESTORE_INJECTED_LINK", "time": 31},
            ]
        controller.save()

    for identity, ended in [("older-plan", 22), ("latest-plan", 26)]:
        plan = {
            "id": identity,
            "leaves": [3],
            "started": 10,
            "events": [{"phase": "applying", "time": 15}, {"phase": "verified", "time": ended}],
        }
        provision.save(controller.db, plan, "rolled_back")

    monkeypatch.setattr(fabric, "DB", controller.db)
    monkeypatch.setattr(fabric, "REPLAY", False)
    client = TestClient(fabric.app)
    standard = client.get("/api/impact").json()
    extended = client.get("/api/impact?evidence=true").json()
    assert "outcome_evidence" not in standard
    assert extended["saved"] == standard["saved"] == 4
    assert standard["rows"] == [
        {key: value for key, value in row.items() if key != "evidence"} for row in extended["rows"]
    ]

    matched = next(
        row for row in extended["rows"] if row["metric"] == "Verified closure" and row["manual"]
    )
    assert matched["evidence"]["topologies"] == [{"routers": baseline}]
    assert [(run["url"], run["seconds"]) for run in matched["evidence"]["runs"]] == [
        ("/api/incidents/manual", 9),
        ("/api/incidents/matched", 5),
    ]
    assert extended["outcome_evidence"]["timer_restoration"][0]["url"] == "/api/incidents/timer"
    assert extended["outcomes"]["ai_repair"] == 2
    rollout = next(row for row in extended["rows"] if row["metric"] == "Provisioning")
    assert rollout["assisted"] == 14
    assert [run["url"] for run in rollout["evidence"]["runs"]] == [
        "/api/provision/older-plan",
        "/api/provision/latest-plan",
    ]

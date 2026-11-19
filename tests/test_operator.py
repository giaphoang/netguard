import json
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import architecture, evaluation, incident_history, intents, investigations, measurements, operator, provision
from api.incidents import Controller
from test_incidents import sample


@pytest.mark.parametrize("valid", [True, False])
@pytest.mark.parametrize(
    ("path", "headers"),
    [
        ("/api/operator", {"Accept": "application/x-ndjson"}),
        ("/api/operator/stream", {}),
    ],
)
def test_chat_stream_reports_workflow_and_preserves_json_api(
    tmp_path, monkeypatch, valid, path, headers
):
    c = Controller(tmp_path / "journal")
    app = FastAPI()
    app.include_router(operator.router)
    app.dependency_overrides[operator.authorized] = lambda: c
    monkeypatch.setattr(operator, "observe", sample)

    def interpret(*_):
        if not valid:
            raise ValueError("Please specify the requested target.")
        spec = intents.Intent(
            intent="answer",
            targets=["leaf-1"],
            node_type="unspecified",
            quantity=None,
            duration_s=None,
            scope="network",
        )
        return spec, ["leaf-1"], {}

    monkeypatch.setattr(intents, "interpret", interpret)
    monkeypatch.setattr(operator.questions, "answer", lambda *_: {"text": "Observed Leaf 1."})
    client = TestClient(app)
    request = {"prompt": "Check Leaf 1", "selection": ""}
    response = client.post(path, json=request, headers=headers)
    assert response.headers["content-type"] == "application/x-ndjson"
    *steps, result = [json.loads(line) for line in response.iter_lines()]
    assert [event["step"]["label"] for event in steps[:2]] == [
        "Collect live observations",
        "Interpret the request",
    ]
    old = client.post("/api/operator", json=request)
    if valid:
        assert "leaf-1" in steps[2]["step"]["detail"]
        assert result == {"answer": old.json()} == {"answer": {"text": "Observed Leaf 1."}}
    else:
        assert len(steps) == 2
        assert old.status_code == 409
        assert result == {"error": old.json()["detail"]}


def test_chat_can_read_a_discarded_plan_by_identity(tmp_path):
    c = Controller(tmp_path / "journal")
    previous = provision.propose(c, 1, time.time())
    provision.save(c.db, previous, "verified")
    preview = provision.propose(c, 1, time.time(), "host", ["leaf-2"])
    operator.cancel(preview["id"], c)
    assert operator.state(c)["id"] == previous["id"]
    discarded = operator.plan_state(preview["id"], c)
    assert [e["phase"] for e in discarded["events"]] == ["preview", "cancelled"]


def test_chat_starts_and_reopens_the_experiment_without_leaving_the_transcript(tmp_path, monkeypatch):
    c = Controller(tmp_path / "journal")
    app = FastAPI()
    app.include_router(operator.router)
    app.dependency_overrides[operator.authorized] = lambda: c
    app.dependency_overrides[operator.local] = lambda: c
    monkeypatch.setattr(operator, "observe", sample)
    intent = intents.Intent(intent="architecture", targets=[], node_type="unspecified",
                            quantity=None, duration_s=None, scope="network")
    monkeypatch.setattr(intents, "interpret", lambda *_: (intent, [], {}))
    prepared = []

    def prepare(controller, run):
        prepared.append(run["prompt"])
        trace = architecture.Trace(controller.db, run)
        with trace.tool("Read observations") as event:
            event["result"] = {"packets": 0}
        trace.mark("evaluated", "Evidence collected.")

    monkeypatch.setattr(architecture, "prepare", prepare)
    client = TestClient(app)
    prompt = "Does a paired workload improve our incident workflow?"
    reply = client.post("/api/operator", headers={"Accept": operator.PROGRESS_MEDIA_TYPE},
                        json={"prompt": prompt})
    answer = json.loads(reply.text.splitlines()[-1])["answer"]
    assert answer["record"]["source"] == "experiment"
    reference = answer["record"]["id"]
    assert prepared == [prompt]
    view = client.get(f"/api/operator/records/experiment/{reference}").json()
    assert view["trace"][0]["result"] == {"packets": 0}
    assert view["title"] == "Evaluation complete" and view["actions"] == []
    blocked = client.post(
        f"/api/operator/records/experiment/{reference}/actions/approve",
        json={"revision": "0" * 64},
    )
    assert blocked.status_code == 409
    assert architecture.objective_schema("Benchmark GRE against IPv4-in-IPv4.")[
        "properties"
    ]["objective"]["enum"] == ["gre", "unsupported"]


def test_incident_chat_reads_saved_runs_and_investigations(tmp_path, monkeypatch):
    c = Controller(tmp_path / "journal")
    incident = "a10b2000" + "1" * 24
    for index, identity in enumerate([incident, *[f"newer-{n}" for n in range(13)]]):
        c.run = {
            "id": identity,
            "scenario": "link" if index == 0 else "endpoint",
            "leaf": 4,
            "spine": 3,
            "phase": "resolved",
            "events": [{"time": 100 + index, "phase": "resolved", "reason": "Verified health checks"}],
        }
        c.save()
    investigation = investigations.record(
        c.db, "monitor", "Repeated BGP transitions",
        {"findings": [["bgp_flapping", "leaf-4/spine-3", 3]]},
    )
    saved = c.db.read_bytes()
    app = FastAPI()
    app.include_router(operator.router)
    app.dependency_overrides[operator.authorized] = lambda: c
    client = TestClient(app)

    def unexpected(*_):
        pytest.fail("Scoped journal lookups must not collect live telemetry.")

    monkeypatch.setattr(operator, "observe", unexpected)
    monkeypatch.setattr(incident_history, "interpret", lambda _: incident_history.Search(
        operation="search", keywords=["resolved", "link"],
    ))
    response = client.post("/api/operator", headers={"Accept": "application/x-ndjson"}, json={
        "prompt": "Find resolved link incidents on Leaf Four",
    })
    step, result = [json.loads(line) for line in response.iter_lines()]
    assert step["step"]["label"] == "Read the incident journal"
    search = result["answer"]["incident_history"]
    assert search["total"] == 1
    assert search["records"][0]["id"] == incident
    assert search["records"][0]["devices"] == ["leaf-4", "spine-3"]
    assert not search["inspection"]
    no_match = client.post("/api/operator", json={
        "prompt": "Find resolved link failures on Leaf 40", "incident_history": True,
    }).json()
    assert no_match["incident_history"]["records"] == []

    monkeypatch.setattr(incident_history, "interpret", unexpected)
    opened = client.post("/api/operator", json={
        "prompt": f"Inspect incident {incident[:8]}", "incident_history": True,
    }).json()
    assert opened["incident_history"]["inspection"]
    assert opened["incident_history"]["records"][0]["id"] == incident
    case = client.post("/api/operator", json={
        "prompt": "Inspect the investigation",
        "incident_record": {"source": "investigation", "id": investigation["id"]},
    }).json()
    assert case["incident_history"]["records"][0]["title"] == "BGP flapping"
    assert "action" not in case
    assert c.db.read_bytes() == saved


def test_incident_history_routing_preserves_operational_requests():
    for prompt, *_ in evaluation.intent_cases():
        assert not incident_history.wants_journal(prompt)
    assert not incident_history.wants_journal("Review this incident and generate a verified postmortem.")
    assert incident_history.wants_journal("What happened in the most recent link failure?")
    assert not incident_history.wants_journal(
        "Monitor my network and open an incident when a peer flaps. Show every tool execution."
    )


def test_provision_scope_and_superseded_previews(tmp_path):
    c = Controller(tmp_path / "journal")
    with pytest.raises(ValueError):
        provision.propose(c, 3, time.time())
    a = provision.propose(c, 2, time.time())
    b = provision.propose(c, 1, time.time())
    assert a["leaves"] == [3, 4] and b["leaves"] == [3]
    assert "+ neighbor 10.211.24.2 remote-as 65104" in a["diff"]
    with pytest.raises(ValueError, match="stale"):
        provision.execute(c, a["id"])
    compose, _ = provision.render(a["leaves"], Path("/tmp/lab"))
    assert all(n["internal"] for n in compose["networks"].values())
    assert all(compose["services"][n]["mem_limit"] == "192m" for n in ["leaf-3", "leaf-4"])
    assert measurements.impact(c.db)["saved"] is None


def test_matched_time_savings_do_not_mix_fault_targets(tmp_path):
    c = Controller(tmp_path / "journal")
    for identity, ai, spine, seconds in [("m", False, 1, 8), ("a", True, 2, 4), ("b", True, 1, 5)]:
        c.run = {
            "id": identity,
            "scenario": "link",
            "leaf": 1,
            "spine": spine,
            "duration": 30,
            "ai": ai,
            "manual": not ai,
            "baseline_routers": ["leaf-1", "leaf-2", "spine-1", "spine-2"],
            "phase": "resolved",
            "attempts": 1,
            "auto": False,
            "events": [
                {"phase": "applying", "time": 1, "reason": "approved: RESTORE_INJECTED_LINK"}
            ],
            "metrics": {"closure_s": seconds},
        }
        c.save()
        if identity == "a":
            assert measurements.impact(c.db)["saved"] is None
    assert measurements.impact(c.db)["saved"] == 3

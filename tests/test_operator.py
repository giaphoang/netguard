import json
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import intents, operator, provision, measurements
from api.incidents import Controller
from test_incidents import sample


@pytest.mark.parametrize("valid", [True, False])
def test_chat_stream_reports_workflow_and_preserves_json_api(tmp_path, monkeypatch, valid):
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
    response = client.post("/api/operator/stream", json=request)
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

import time
from pathlib import Path

import pytest

from api import operator, provision, measurements
from api.incidents import Controller


@pytest.mark.parametrize(
    ("target", "spine"),
    [("Spine 1", 1), ("Spine 2", 2), ("spine‑one", 1), ("spine   ２", 2)],
)
def test_spine_removal_returns_targeted_evidence(tmp_path, monkeypatch, target, spine):
    surviving_peer = "Established" if spine == 2 else "Idle"
    monkeypatch.setattr(
        operator,
        "observe",
        lambda: {
            "routers": [
                {
                    "name": "leaf-1",
                    "peers": {"10.211.11.1": "Established", "10.211.21.1": "Idle"},
                    "routes": "",
                }
            ]
        },
    )
    result = operator.operator(
        operator.Request(prompt=f"What could break if I remove {target}?"),
        Controller(tmp_path / "journal"),
    )
    assert result["text"].startswith(f"Blocked removal of Spine {spine}.")
    assert result["evidence"]["facts"]["leaf-1"]["surviving_peer"] == surviving_peer
    assert "action" not in result


@pytest.mark.parametrize("target", ["Spine 3", "Spine 1 and Spine 2", "Spine 1 or 2"])
def test_spine_removal_requests_one_supported_target(target):
    request = operator.Request(prompt=f"What could break if I remove {target}?")
    assert operator.interpret(request) == ("risk_clarify", {})


def test_model_risk_intent_uses_spine_named_by_operator(monkeypatch):
    monkeypatch.setattr(
        operator.advisor,
        "infer",
        lambda *_: '{"tool":"risk","leaf":0,"spine":2,"count":0,"seconds":0}',
    )
    assert operator.interpret(operator.Request(prompt="Suppose Spine One is retired")) == (
        "risk",
        {"spine": 1},
    )
    assert operator.interpret(operator.Request(prompt="Suppose the router disappears")) == (
        "risk_clarify",
        {},
    )


def test_spine_outage_what_if_routes_to_risk():
    request = operator.Request(prompt="Suppose Spine One disappears. What is the impact?")
    assert operator.interpret(request) == ("risk", {"spine": 1})


def test_exact_targets_and_duration():
    tool, args = operator.interpret(
        operator.Request(prompt="Simulate a Leaf 1 → Spine 2 link failure for 30 seconds.")
    )
    assert (tool, args) == ("simulate", {"leaf": 1, "spine": 2, "seconds": 30})
    tool, args = operator.interpret(
        operator.Request(prompt="Add two leaf routers connected to both spines with BGP.")
    )
    assert (tool, args) == ("provision", {"count": 2})


def test_only_explicit_changes_can_be_previewed(monkeypatch):
    monkeypatch.setattr(
        operator.advisor,
        "infer",
        lambda *_: '{"tool":"simulate","leaf":1,"spine":2,"count":0,"seconds":60}',
    )
    assert (
        operator.interpret(
            operator.Request(prompt="Describe the difference between a link and a peer")
        )[0]
        == "clarify"
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
            "events": [],
            "metrics": {"closure_s": seconds},
        }
        c.save()
        if identity == "a":
            assert measurements.impact(c.db)["saved"] is None
    assert measurements.impact(c.db)["saved"] == 3

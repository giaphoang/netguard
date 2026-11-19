import time
from copy import deepcopy

from api import advisor, fabric
from api.incidents import Controller
from api.monitoring import Observer, Rules, detect
from test_incidents import sample


def test_proactive_investigation_deduplicates_and_verifies(tmp_path, monkeypatch):
    calls = []

    def assess(obs):
        calls.append(obs)
        return advisor.Decision(
            diagnosis="link",
            component="leaf-1/spine-1",
            action="restore_link",
            citations=["leaf-1/spine-1"],
            next_check="Inspect interface",
        )

    monkeypatch.setattr(advisor, "assess", assess)
    c = Controller(tmp_path / "db")
    observer = Observer(c)
    start = time.time()

    def feed(offset, failed):
        s = sample(failed)
        s["observed"] = start + offset
        observer.step(s)

    feed(0, True)
    assert not calls
    feed(1, True)
    assert len(calls) == 1 and c.signal["status"] == "escalated"
    assert c.run is None  # Unknown/unapproved faults never reach the actuator.
    identity = c.signal["id"]
    for n in range(2, 5):
        feed(n, True)
    assert len(calls) == 1
    feed(5, False)
    assert c.signal
    feed(6, False)
    assert c.signal is None
    from api.investigations import cases

    assert cases(c.db)[0]["id"] == identity
    assert cases(c.db)[0]["outcome"] == "observed_recovery"
    restarted = Observer(c)
    assert restarted.last_inference and restarted.episode is None
    restarted.step({**sample(True), "observed": start + 7})
    restarted.step({**sample(True), "observed": start + 8})
    assert len(calls) == 1


def test_rules_track_each_peer_and_both_sides():
    obs = advisor.evidence(sample())
    down = deepcopy(obs)
    down["facts"]["leaf-1/spine-1"]["spine_peer"] = "Idle"
    assert detect([obs, down], Rules()) == []
    assert detect([down, down], Rules())[0][0] == "link_or_peer_failure"
    history = []
    for state in ["Established", "Idle", "Established", "Idle"]:
        entry = deepcopy(obs)
        entry["facts"]["leaf-2/spine-2"]["leaf_peer"] = state
        history.append(entry)
    assert ("bgp_flapping", "leaf-2/spine-2", 3) in detect(history, Rules())


def test_monitor_requires_measured_latency_and_observed_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(advisor, "assess", lambda _: advisor.Decision(
        diagnosis="unknown", component="unknown", action="handoff", citations=["host-1"],
        next_check="Check observed RTT.",
    ))
    controller = Controller(tmp_path / "observer")
    observer = Observer(controller)
    stamp = time.time()

    def feed(offset, rtt):
        observed = sample()
        observed["observed"] = stamp + offset
        observed["flows"][0]["icmp"] = {"sent": 1, "received": 1, "rtt_ms": rtt}
        observer.step(observed)

    assert fabric.icmp_observation(
        "64 bytes from host: seq=0 ttl=63 time=8.25 ms\n1 packets transmitted, 1 packets received"
    ) == {"sent": 1, "received": 1, "rtt_ms": 8.25}
    feed(0, 8)
    assert controller.signal is None
    feed(1, 9)
    assert controller.signal["facts"]["findings"] == [("high_latency", "host-1", 9)]
    feed(2, 10)
    assert controller.signal and observer.recovered == 0
    feed(3, 1)
    feed(4, 1)
    assert controller.signal is None


def test_missing_model_escalates_once(tmp_path, monkeypatch):
    def unavailable(_):
        raise OSError("offline")

    monkeypatch.setattr(advisor, "assess", unavailable)
    c = Controller(tmp_path / "db")
    observer = Observer(c)
    observer.step(sample(True))
    observer.step(sample(True))
    assert c.signal["status"] == "escalated"
    assert c.signal["events"][-1]["stage"] == "Blocked"
    assert c.run is None


def test_model_cannot_contradict_down_interface(monkeypatch):
    import pytest

    def dishonest(instruction, context, schema):
        assert schema["properties"]["diagnosis"]["enum"] == ["link", "unknown"]
        return '{"diagnosis":"bgp","component":"leaf-1/spine-1","action":"restore_link","citations":["leaf-1/spine-1"],"next_check":"restore"}'

    monkeypatch.setattr(advisor, "infer", dishonest)
    with pytest.raises(ValueError, match="contradicts"):
        advisor.assess(advisor.evidence(sample(True)))


def test_drift_recovery_requires_consecutive_clean_scans(tmp_path, monkeypatch):
    from api import investigations as inv

    c = Controller(tmp_path / "db")
    observer = Observer(c)
    observer.episode = inv.record(
        c.db, "monitor", "drift", {"findings": [["configuration_drift", "fabric", "test"]]}
    )
    observer.episode.update(status="escalated", affected=["fabric"])
    clean = {"facts": {"leaf-1": {"missing": [], "unexpected": []}}}
    dirty = {"facts": {"leaf-1": {"missing": ["neighbor missing"], "unexpected": []}}}
    checks = iter([clean, dirty, clean, clean])
    monkeypatch.setattr(inv, "drift", lambda *_: next(checks))
    for expected in [1, 0, 1]:
        observer.next_drift = 0
        observer.follow(sample())
        assert observer.episode and observer.recovered == expected
    observer.next_drift = 0
    observer.follow(sample())
    assert observer.episode is None

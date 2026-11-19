from api import investigations as inv


def test_alternating_peer_events_open_investigation():
    def snapshot(state):
        return {"facts": {"leaf-1/spine-1": {"leaf_peer": state}}}

    events = [snapshot(s) for s in ["Established", "Idle", "Established", "Idle"]]
    assert inv.anomaly(events[:2]) is None
    assert "transitions" in inv.anomaly(events)
    assert inv.anomaly([events[0]] * 4) is None


def test_spine_removal_reports_independent_survivor_evidence(tmp_path):
    sample = {
        "routers": [
            {
                "name": "leaf-1",
                "peers": {"10.211.11.1": "Established"},
                "routes": "prefix via spine-1",
            },
            {"name": "leaf-2", "peers": {}, "routes": ""},
        ]
    }
    row = inv.risk(tmp_path / "db", sample, 2)
    assert row["facts"]["policy_check"]["observed"] == "block"
    assert row["facts"]["leaf-1"]["remaining_spines"] == 1
    assert row["facts"]["leaf-2"]["surviving_peer"] is None
    assert inv.cases(tmp_path / "db")[0]["id"] == row["id"]


def test_drift_identifies_unexpected_shutdown():
    expected = "router bgp 65101\n neighbor 10.211.11.1 remote-as 65000"
    actual = expected + "\n neighbor 10.211.11.1 shutdown"
    assert inv.bgp_config(actual) - inv.bgp_config(expected) == {"neighbor 10.211.11.1 shutdown"}

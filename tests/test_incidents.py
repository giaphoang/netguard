from copy import deepcopy
import json
from pathlib import Path

import pytest

from api import incidents
from api.incidents import Controller, diagnose, healthy
from api.presets import Target

CAP = Path(__file__).resolve().parents[1] / "scenarios"


def sample(failed=False):
    frame = json.loads((CAP / "failover.json").read_text())
    routers = (
        frame["snapshot"]["routers"]
        if failed
        else json.loads((CAP / "healthy.json").read_text())["routers"]
    )
    return {
        "routers": routers,
        "flows": [
            {
                "probes": {"ICMP": "success", "HTTP": "success"},
                "candidates": [2] if failed else [1, 2],
            }
            for _ in range(2)
        ],
        "observed": incidents.time.time(),
    }


@pytest.mark.parametrize("auto", [False, True])
def test_link_workflow(tmp_path, monkeypatch, auto):
    down = False

    def change(s, t, inject):
        nonlocal down
        down = inject

    monkeypatch.setattr(
        incidents.presets, "identify", lambda _: Target(container="test", interface="eth1")
    )
    monkeypatch.setattr(incidents.presets, "fault_present", lambda *_: down)
    monkeypatch.setattr(incidents.presets, "change", change)
    monkeypatch.setattr(incidents, "observe", lambda: sample(down))
    c = Controller(tmp_path / "test.db")
    c.auto = auto
    c.inject("link")
    with pytest.raises(ValueError):
        c.inject("link")
    c.step()
    c.step()
    if not auto:
        assert c.run["phase"] == "proposed" and down
        c.restore("approved", c.run["id"])
    assert not down and c.run["phase"] == "verifying"
    c.step()
    c.step()
    assert c.run["phase"] == "resolved" and c.run["attempts"] == 1
    assert Controller(c.db).detail(c.run["id"]) == c.run


def test_ambiguous_peers_block_diagnosis():
    s = sample(True)
    assert diagnose("link", s)
    bad = deepcopy(s)
    bad["routers"][1]["peers"] = {}
    assert not diagnose("link", bad)
    assert not healthy(bad)


def test_emergency_stop_survives_restart(tmp_path):
    db = tmp_path / "run.sqlite"
    Controller(db).emergency_stop(True)
    c = Controller(db)
    assert c.stopped and not c.auto
    with pytest.raises(ValueError):
        c.inject("link")
    c.emergency_stop(False)
    assert not Controller(db).stopped


def test_recorded_walkthrough_and_comparison():
    from api.fabric import recording
    from api.measurements import comparison

    run = json.loads((CAP / "p1-link.json").read_text())
    frames = recording(True)
    assert frames[0]["label"] == "Injecting" and frames[-1]["label"] == "Resolved"
    assert any(f["label"] == "Proposed" for f in frames)
    assert all(f["probes"] and f["snapshot"]["routers"] for f in frames)
    reset = deepcopy(run)
    next(e for e in reset["events"] if e["phase"] == "applying")["reason"] = (
        "reset: RESTORE_INJECTED_LINK"
    )
    rows = comparison([run, reset])
    assert rows[0]["n"] == 1 and rows[0]["automatic"]


@pytest.mark.parametrize(
    "override", [{"Internal": False}, {"Options": {"parent": "eth0"}}, {"Driver": "bridge"}]
)
def test_network_boundary_rejects_misconfigured_target(monkeypatch, override):
    from api import presets

    name = "netguard_access2"
    info = {
        "Config": {
            "Labels": {
                "com.docker.compose.project": "netguard",
                "com.docker.compose.service": "host-2",
                "com.docker.compose.project.config_files": str(presets.ROOT / "fabric.yml"),
            }
        },
        "NetworkSettings": {"Networks": {name: {"NetworkID": "net-id"}}},
        "HostConfig": {"Privileged": False},
        "State": {"Running": True},
    }
    net = {
        "Name": name,
        "Driver": "macvlan",
        "Internal": True,
        "Options": {},
        "Labels": {"com.docker.compose.project": "netguard"},
        **override,
    }

    def read(*args):
        if args[0] == "context":
            return "unix:///var/run/docker.sock"
        if args[0] == "inspect":
            return json.dumps([info])
        assert args == ("network", "inspect", "net-id")
        return json.dumps([net])

    monkeypatch.setattr(presets, "docker", read)
    with pytest.raises(ValueError, match="isolated"):
        presets.identify("endpoint")


def test_replaced_target_requires_healthy_observation_and_never_actuates(tmp_path, monkeypatch):
    controller = Controller(tmp_path / "journal")
    target = Target(container="original", interface="eth1")
    monkeypatch.setattr(incidents.presets, "identify", lambda s: target)
    monkeypatch.setattr(incidents.presets, "fault_present", lambda *args: False)
    monkeypatch.setattr(incidents.presets, "change", lambda *args: None)
    monkeypatch.setattr(incidents, "observe", lambda: sample(False))
    controller.inject("link")
    restarted = Controller(controller.db)
    assert restarted.run["phase"] == "handoff" and not restarted.auto
    monkeypatch.setattr(
        incidents.presets, "identify", lambda s: Target(container="replacement", interface="eth1")
    )
    monkeypatch.setattr(
        incidents.presets, "change", lambda *args: pytest.fail("Actuated replacement")
    )
    monkeypatch.setattr(incidents, "observe", lambda: sample(True))
    with pytest.raises(ValueError, match="unhealthy"):
        restarted.restore("reset")
    monkeypatch.setattr(incidents, "observe", lambda: sample(False))
    restarted.restore("reset")
    restarted.step()
    restarted.step()
    assert restarted.run["phase"] == "resolved" and restarted.run["attempts"] == 0

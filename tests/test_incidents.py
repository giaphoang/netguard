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

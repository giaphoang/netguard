import time
from pathlib import Path

import pytest

from api import provision, measurements
from api.incidents import Controller


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

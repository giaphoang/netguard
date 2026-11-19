from copy import deepcopy
from time import time

import pytest
from fastapi import BackgroundTasks, HTTPException

from api import architecture, architecture_plans
from api.incidents import Controller


def preview(controller):
    run = {
        "id": "test",
        "phase": "preview",
        "version": architecture_plans.VERSION,
        "kind": "throughput",
        "scope": {},
        "endpoints": [],
        "plan": {"prediction": {"gbps": 2}},
        "baseline": {"gbps": 2},
        "method": {},
        "trace": [],
        "expires": time() + 30,
    }
    run["digest"] = architecture.digest(run)
    architecture.save(controller.db, run)
    return run


def test_architecture_only_reserves_the_frozen_approved_preview(tmp_path):
    controller = Controller(tmp_path / "journal")
    run = preview(controller)
    approval = architecture.Approval(digest=run["digest"])
    tampered = deepcopy(run)
    tampered["plan"]["prediction"]["gbps"] = 3
    architecture.save(controller.db, tampered)
    task = BackgroundTasks()
    with pytest.raises(HTTPException, match="stale"):
        architecture.approve(run["id"], approval, task, controller)
    assert not task.tasks
    assert not architecture.reservation(controller.db)

    architecture.save(controller.db, run)
    architecture.approve(run["id"], approval, task, controller)
    saved = architecture.load(controller.db, run["id"])
    assert saved["approval"]["digest"] == run["digest"]
    assert saved["phase"] == "applying"
    assert "actual" not in saved
    with pytest.raises(ValueError, match="active Architecture Lab"):
        controller.inject("link")


def test_restart_distinguishes_pending_measurements_from_attempted_changes(tmp_path):
    db = tmp_path / "journal"
    for identity, phase, mutation in [
        ("baseline", "investigating", False),
        ("deployment", "applying", True),
    ]:
        architecture.save(db, {"id": identity, "phase": phase, "change_started": mutation})
    Controller(db)
    assert architecture.load(db, "baseline")["phase"] == "interrupted"
    assert architecture.load(db, "deployment")["phase"] == "needs_review"
    assert architecture.reservation(db) == "deployment"


def test_stale_hash_policy_blocks_every_write():
    class Lab:
        def run(self, node, command, flag, key):
            assert (command, flag, key) == ("sysctl", "-n", architecture_plans.HASH_POLICY)
            return type("Reading", (), {"stdout": "1" if node == "leaf-2" else "0"})()

    plan = {
        "change": {
            "before": {"leaf-1": 0, "leaf-2": 0},
            "after": {"leaf-1": 1, "leaf-2": 1},
            "nodes": ["leaf-1", "leaf-2"],
        },
    }
    with pytest.raises(ValueError, match="changed after the baseline"):
        architecture_plans.set_policies(Lab(), plan, before_write=lambda _: pytest.fail("write"))

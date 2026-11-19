from api import capacity, expansion
from api.incidents import Controller
from test_incidents import sample


def test_capacity_doubles_hosts_without_adding_routers(tmp_path, monkeypatch):
    controller = Controller(tmp_path / "journal")
    monkeypatch.setattr(
        capacity,
        "resources",
        lambda *_, **__: {
            "cpus": 4,
            "memory_available_mib": 1024,
            "usage": [],
        },
    )
    details, plan, _ = capacity.plan(controller, "Support twice as many hosts.", sample(), 1)
    assert details["requirements"]["hosts"] == 2
    assert details["requirements"]["additional_memory_mib"] == 64
    assert plan["before"]["spines"] == plan["topology"]["spines"]
    assert plan["before"]["leaves"] == plan["topology"]["leaves"]
    assert len(plan["topology"]["hosts"]) == 4
    assert plan["added"] == ["host-3", "host-4"]
    assert plan["phase"] == "preview"
    assert expansion.current(controller.db) is None


def test_capacity_enforces_host_policy_and_guest_budget(tmp_path, monkeypatch):
    controller = Controller(tmp_path / "journal")
    monkeypatch.setattr(
        capacity,
        "resources",
        lambda *_, **__: {
            "cpus": 4,
            "memory_available_mib": 10,
            "usage": [],
        },
    )
    details, plan, result = capacity.plan(controller, "Scale to seven total hosts.", sample(), 1)
    assert details["target_hosts"] == 7
    assert plan is None and "lab limit" in result
    details, plan, result = capacity.plan(controller, "Support four hosts.", sample(), 1)
    assert details["target_hosts"] == 4
    assert plan is None and "available memory" in result

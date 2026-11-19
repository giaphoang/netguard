import json

import pytest

from api import advisor, capacity, evaluation, expansion, intents
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


def test_host_growth_keeps_specific_add_node_tool_out_of_the_model_choices(monkeypatch):
    def classify(prompt, context, schema):
        assert schema["properties"]["intent"]["enum"] == ["plan_host_capacity", "answer_question"]
        return json.dumps(
            {
                "intent": "plan_host_capacity",
                "targets": [],
                "node_type": "host",
                "quantity": None,
                "duration_s": None,
                "scope": "network",
            }
        )

    monkeypatch.setattr(advisor, "infer", classify)
    spec, targets, _ = intents.interpret(
        "Help our fabric support twice the host population within its resource budget.",
        "",
        evaluation.expanded(),
    )
    assert spec.intent == "capacity" and targets == []


def test_additional_hosts_count_toward_the_current_inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(
        capacity, "resources", lambda *_, **__: {"cpus": 4, "memory_available_mib": 1024}
    )
    prompt = "Expand the fabric to support 10 additional hosts."
    assert capacity.has_host_growth_goal(prompt)
    controller = Controller(tmp_path / "journal")
    details, plan, reason = capacity.plan(controller, prompt, sample(), 1)
    assert details["target_hosts"] == 12
    assert plan is None and "approved lab limit is 4" in reason


def test_router_preview_checks_mem_and_healthy_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(
        capacity, "resources", lambda *_, **__: {"cpus": 4, "memory_available_mib": 128}
    )
    controller = Controller(tmp_path / "journal")
    with pytest.raises(ValueError, match="available memory"):
        expansion.propose(controller, "spine", 1, [], 1, observation=sample())
    with pytest.raises(ValueError, match="Restore healthy routing"):
        expansion.propose(controller, "leaf", 1, [], 1, observation=sample(True))

import time

import pytest
from fastapi import BackgroundTasks, HTTPException

from api import architecture, chat_views, operator, protocol_study
from api.incidents import Controller
from api.routing_lab import summarize_ping


def test_protocol_designs_use_the_same_links_and_access_prefixes():
    assert protocol_study.requested_protocols("Compare eBGP, OSPFv2 and IS IS.") == [
        "BGP",
        "OSPF",
        "IS-IS",
    ]
    assert protocol_study.interfaces(False, 1) == {
        "transit-s1": "10.217.11.1",
        "transit-s2": "10.217.21.1",
    }
    bgp = protocol_study.render("BGP", False, 1)
    ospf = protocol_study.render("OSPF", False, 1)
    isis = protocol_study.render("IS-IS", False, 1)
    assert "network 10.218.1.0/28" in bgp
    assert "interface access1" in ospf and "ip ospf passive" in ospf
    assert "interface access1" in isis and "isis passive" in isis
    assert "timers bgp 1 4" in bgp
    assert "ip ospf dead-interval 4" in ospf
    assert "isis hello-multiplier 4" in isis


def test_routing_study_uses_the_shared_approval_action(tmp_path, monkeypatch):
    controller = Controller(tmp_path / "journal")
    request = operator.Request(
        prompt="Design an isolated convergence experiment for IS-IS and OSPF."
    )
    tasks = BackgroundTasks()

    def prepare(controller, run):
        protocols = protocol_study.requested_protocols(run["prompt"])
        run.update(
            plan=protocol_study.draft(protocols, "test-project"), scope={}, expires=time.time() + 60
        )
        run["digest"] = architecture.digest(run)
        architecture.Trace(controller.db, run).mark("preview", "Review the isolated study.")

    monkeypatch.setattr(architecture, "prepare_routing", prepare)
    command = type("Intent", (), {"intent": "routing_study"})()
    reply = operator.answer_request(request, controller, time.time(), {}, command, [], {})
    identity = reply["record"]["id"]
    record = architecture.load(controller.db, identity)
    (action,) = chat_views.experiment(record)["actions"]
    assert action["label"] == "Approve and run comparison"
    assert list(record["plan"]["arms"]) == ["OSPF", "IS-IS"]
    assert "approval" not in record and "actual" not in record

    with pytest.raises(HTTPException, match="stale"):
        operator.record_action(
            "experiment",
            identity,
            "approve",
            operator.RecordApproval(revision="0" * 64),
            tasks,
            controller,
        )
    assert tasks.tasks == []
    operator.record_action(
        "experiment",
        identity,
        "approve",
        operator.RecordApproval(revision=action["revision"]),
        tasks,
        controller,
    )
    assert [job.func for job in tasks.tasks] == [architecture.apply_routing]
    assert architecture.load(controller.db, identity)["phase"] == "applying"


def test_probe_results_keep_unreachable_sequences_and_require_recovery():
    method = {**protocol_study.WORKLOAD, "probe_count": 7}
    output = "\n".join(
        [
            "[1.1] 64 bytes from 10.218.2.2: icmp_seq=1 ttl=61 time=0.1 ms",
            "[1.3] From 10.218.1.1 icmp_seq=2 Destination Net Unreachable",
            "[1.5] 64 bytes from 10.218.2.2: icmp_seq=4 ttl=61 time=0.1 ms",
            "[1.6] 64 bytes from 10.218.2.2: icmp_seq=5 ttl=61 time=0.1 ms",
            "[1.7] 64 bytes from 10.218.2.2: icmp_seq=6 ttl=61 time=0.1 ms",
            "[1.8] 64 bytes from 10.218.2.2: icmp_seq=7 ttl=61 time=0.1 ms",
            "7 packets transmitted, 5 received, +1 errors, 28.57% packet loss",
        ]
    )
    trial = summarize_ping(output, 1.2, method)
    assert trial["missing_sequences"] == [2, 3]
    assert trial["longest_loss_ms"] == 200
    with pytest.raises(ValueError, match="sustained delivery"):
        summarize_ping(output.replace("icmp_seq=7", "icmp_seq=6"), 1.2, method)

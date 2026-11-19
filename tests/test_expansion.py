from copy import deepcopy
from ipaddress import ip_address, ip_network
import time

import pytest

from api import expansion, provision
from api.incidents import Controller


def test_cumulative_plans_and_rollback_keep_the_prior_topology(tmp_path):
    c = Controller(tmp_path / "db")
    leaf = provision.propose(c, 1, time.time())
    provision.save(c.db, leaf, "verified")
    spine = provision.propose(c, 1, time.time(), "spine")
    assert expansion.current(c.db)["id"] == leaf["id"]  # Preview is not live.
    assert spine["topology"]["leaves"] == [1, 2, 3]
    provision.save(c.db, spine, "verified")
    host = provision.propose(c, 1, time.time(), "host", ["leaf-3"])
    assert host["added"] == ["host-3"]
    provision.save(c.db, host, "rolled_back")
    assert expansion.current(c.db)["id"] == spine["id"]
    provision.save(c.db, spine, "rolled_back")
    assert expansion.current(c.db)["id"] == leaf["id"]


def test_addressing_neighbors_and_isolation(tmp_path):
    topo = {
        "spines": [1, 2, 3],
        "leaves": [1, 2, 3, 4],
        "hosts": [
            {"id": 1, "leaf": 1},
            {"id": 2, "leaf": 2},
            {"id": 3, "leaf": 2},
            {"id": 4, "leaf": 4},
        ],
    }
    overlay, configs = expansion.render(topo, tmp_path)
    assert len(configs) == 7
    for name, service in overlay["services"].items():
        assert service["mem_limit"] in {"192m", "32m"}
        for network, attach in service["networks"].items():
            net = overlay["networks"][network]
            assert net["internal"] and not net.get("driver_opts")
            assert ip_address(attach["ipv4_address"]) in ip_network(
                net["ipam"]["config"][0]["subnet"]
            )
        if name.startswith("leaf"):
            assert configs[name].count(" remote-as 65000") == 3
    assert expansion.host_ip(topo["hosts"][2], topo) == "10.212.2.3"
    assert "neighbor 10.211.34.2 remote-as 65104" in configs["spine-3"]
    addresses = [n["ipam"]["config"][0]["subnet"] for n in overlay["networks"].values()]
    assert len(set(addresses)) == len(addresses)


def test_capacity_unknown_attachment_and_stale_approval(tmp_path):
    c = Controller(tmp_path / "db")
    with pytest.raises(ValueError, match="existing leaf"):
        provision.propose(c, 1, time.time(), "host", ["leaf-99"])
    with pytest.raises(ValueError, match="capacity"):
        provision.propose(c, 2, time.time(), "spine")
    old = provision.propose(c, 1, time.time())
    provision.propose(c, 1, time.time(), "spine")
    with pytest.raises(ValueError, match="stale"):
        provision.execute(c, old["id"])
    invalid = deepcopy(expansion.BASE)
    invalid["spines"] = [1, 3]
    with pytest.raises(ValueError, match="preservation"):
        expansion.validate(invalid)


def test_single_failed_new_leaf_link_is_diagnosable():
    from test_incidents import sample
    from api import incidents, advisor

    s = sample()
    s["routers"].append(
        {
            "name": "leaf-3",
            "peers": {"10.211.13.1": "Idle", "10.211.23.1": "Established"},
            "interfaces": "eth1 DOWN 10.211.13.2/28\neth2 UP 10.211.23.2/28",
            "routes": "",
        }
    )
    for r in s["routers"]:
        if r["name"].startswith("spine"):
            number = int(r["name"][-1])
            r["peers"][f"10.211.{number}3.2"] = "Idle" if number == 1 else "Established"
    assert incidents.diagnose("link", s, 3, 1)
    facts = advisor.evidence(s)["facts"]
    assert facts["leaf-3/spine-1"]["interface"] == "DOWN"
    assert not incidents.healthy(s)


def test_unchanged_containers_keep_authorized_ancestor_origins(tmp_path):
    c = Controller(tmp_path / "db")
    leaf = provision.propose(c, 1, time.time())
    provision.save(c.db, leaf, "verified")
    host = provision.propose(c, 1, time.time(), "host", ["leaf-2"])
    origins = expansion.approved_origins(c.db, host)
    assert str(provision.BASE) in origins
    assert (
        str(provision.BASE) + "," + str(provision.material(c.db, leaf)[0] / "compose.json")
        in origins
    )
    assert len(origins) == 3


def test_rollout_initializes_a_missing_loopback_in_the_owned_leaf(monkeypatch):
    spec = {**expansion.BASE, "leaves": [1, 2, 3]}
    origins = {"approved"}
    calls = []

    def owner(topology, allowed):
        assert topology == spec and allowed == origins
        return {"leaf-3": {"Id": "owned-leaf-3"}}

    def docker(*args):
        calls.append(args)
        return "lo UNKNOWN 127.0.0.1/8"

    monkeypatch.setattr(expansion, "ownership", owner)
    monkeypatch.setattr(provision.presets, "docker", docker)
    assert expansion.initialize_loopbacks(spec, origins) == [
        {"node": "leaf-3", "address": "10.214.0.3/32"}
    ]
    assert calls[-1] == (
        "exec",
        "owned-leaf-3",
        "ip",
        "-4",
        "address",
        "replace",
        "10.214.0.3/32",
        "dev",
        "lo",
    )

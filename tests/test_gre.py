from types import SimpleNamespace

import pytest

from api import expansion, gre


def test_ipv4_in_ipv4_uses_ipip_type_and_20_byte_overhead():
    assert gre.requested("Benchmark IPv4-in-IPv4 in the lab.") == ["ipip"]
    assert gre.TUNNELS["ipip"].overhead == 20 and gre.TUNNELS["ipip"].protocol == 4
    change = {
        "mode": "ipip",
        "interface": "ngiabcdef0",
        "ifindex": 1026,
        "mtu": 1480,
        "ends": {
            "leaf-1": {"local": "10.212.1.1", "remote": "10.212.2.1", "overlay": "10.215.0.1"}
        },
    }
    command = gre.commands(change, "leaf-1")[0]
    assert command[command.index("type") + 1 :] == (
        "ipip",
        "local",
        "10.212.1.1",
        "remote",
        "10.212.2.1",
    )


def test_gre_resolves_existing_leaves_in_requested_order():
    spec = {**expansion.BASE, "leaves": [1, 2, 3, 4]}
    sample = {
        "routers": [
            {
                "name": f"leaf-{n}",
                "interfaces": f"eth0 UP 10.212.{n}.1/28" if n != 4 else "lo UNKNOWN",
            }
            for n in spec["leaves"]
        ],
    }
    selected = gre.endpoints("Design a GRE tunnel from Leaf Three to Leaf 2.", "", spec, sample)
    assert [endpoint.socket for endpoint in selected] == [
        ("leaf-3", "10.212.3.1"),
        ("leaf-2", "10.212.2.1"),
    ]
    with pytest.raises(ValueError, match="routed access interfaces"):
        gre.endpoints("Design GRE to Leaf 4.", "", spec, sample)


def test_gre_rollback_leaves_an_unowned_interface_in_place():
    namespace = {"container": "same-container"}
    lab = SimpleNamespace(identities={"leaf-1": namespace})
    lab.json = lambda *args: [{"ifname": "ngg123", "ifindex": 23}]
    lab.run = lambda *args: pytest.fail("attempted to delete an unowned tunnel")
    plan = {
        "change": {
            "interface": "ngg123",
            "ifindex": 123,
            "ends": {"leaf-1": {"local": "10.212.1.1", "remote": "10.212.2.1"}},
        },
    }
    with pytest.raises(ValueError, match="ownership changed"):
        gre.restore(lab, plan, ["leaf-1"], {"leaf-1": namespace})

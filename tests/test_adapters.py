import json

import pytest

from api import adapters, investigations, network_tools, topology
from test_incidents import sample


def test_same_inspection_reads_frr_and_openwrt(monkeypatch):
    snapshot = sample()
    node = adapters.OPENWRT_NODE
    snapshot["adapter_devices"] = [
        {
            "name": node,
            "identity": "b" * 64,
            "vendor": "OpenWrt",
            "aliases": ["OpenWrt"],
            "interfaces": "1: lo: <UP> mtu 65536\n    inet 127.0.0.1/8 scope host lo",
            "routes": "local 127.0.0.1 dev lo proto kernel",
            "error": None,
        }
    ]
    monkeypatch.setattr(
        adapters.OPENWRT,
        "uci",
        lambda _: {
            "network.loopback.device": "lo",
            "network.loopback.ipaddr": "127.0.0.1",
            "network.loopback.netmask": "255.0.0.0",
        },
    )
    monkeypatch.setattr(
        investigations,
        "configuration_facts",
        lambda names: {name: {"missing": [], "unexpected": []} for name in names},
    )
    ctx = network_tools.Context(snapshot, topology.inventory(snapshot))
    targets = [topology.resolve("OpenWrt", ctx.graph), "spine-1"]
    result = network_tools.invoke("inspect_routing", {"targets": targets}, ctx)
    assert result["platforms"] == {node: "OpenWrt", "spine-1": "FRRouting"}
    assert result["devices"][node]["bgp"]["status"] == "unavailable"
    assert result["devices"][node]["kernel"]["status"] == "observed"
    comparison = network_tools.invoke("inspect_configuration", {"targets": targets}, ctx)
    assert all(not v["missing"] and not v["unexpected"] for v in comparison["devices"].values())
    assert comparison["devices"][node]["scope"] == "UCI loopback addresses"


def test_openwrt_refuses_a_container_connected_to_a_network(monkeypatch):
    image = "sha256:" + "a" * 64
    monkeypatch.setenv("NETGUARD_OPENWRT_IMAGE_ID", image)
    info = {
        "Id": "b" * 64,
        "Image": image,
        "Config": {"Labels": {"netguard.engineering.adapter": "openwrt"}},
        "Mounts": [],
        "State": {"Running": True},
        "HostConfig": {"Privileged": False, "NetworkMode": "bridge"},
    }
    monkeypatch.setattr(
        adapters.presets,
        "docker",
        lambda *args: "unix:///tmp/docker.sock" if args[0] == "context" else json.dumps([info]),
    )
    with pytest.raises(ValueError, match="isolation"):
        adapters.OPENWRT.owned()

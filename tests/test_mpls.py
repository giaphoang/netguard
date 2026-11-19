from types import SimpleNamespace

import pytest

from api import mpls


def test_iproute2_mpls_readings_bind_the_label_port_and_source():
    planned = {
        "label": 16,
        "gateway": "10.211.11.1",
        "source_ip": "10.215.0.1",
        "egress": "eth0",
        "next_hop": "10.211.12.2",
        "spine_egress": "eth1",
    }
    route = (
        "10.215.0.2  encap mpls  16 via 10.211.11.1 dev eth0 proto static src 10.215.0.1 mtu 1496"
    )
    assert mpls.owns_ingress(route, planned)
    assert not mpls.owns_ingress(route.replace(" 16 ", " 160 "), planned)
    assert mpls.owns_label("16 via inet 10.211.12.2 dev eth1 ", planned)
    assert not mpls.owns_label("16 via inet 10.211.12.2 dev eth10", planned)


def test_preexisting_label_table_blocks_an_experiment():
    lab = SimpleNamespace(run=lambda *_: SimpleNamespace(stdout="1024"))
    with pytest.raises(ValueError, match="unused label table"):
        mpls.check_empty(lab, {"spine": "spine-2", "paths": [{"spine_egress": "eth1"}]})

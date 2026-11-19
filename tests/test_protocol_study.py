from api import protocol_study


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

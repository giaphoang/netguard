from api import expansion, risk_preview
from test_incidents import sample


def test_risk_projects_live_ecmp_and_removes_only_the_requested_node():
    observation = sample()
    facts = risk_preview.preview(observation, ["spine-1"], expansion.BASE)
    assert facts["before"]["spines"] == [1, 2]
    assert facts["after"]["spines"] == [2]
    assert facts["after"]["hosts"] == facts["before"]["hosts"]
    assert facts["policy_check"]["observed"] == "block"
    assert {row["link"] for row in facts["affected_bgp_sessions"]} == {
        "leaf-1/spine-1",
        "leaf-2/spine-1",
    }
    assert all(row["remaining"] == [2] for row in facts["forwarding_projection"])
    second = risk_preview.preview(observation, ["spine-2"], expansion.BASE)
    assert all(row["remaining"] == [1] for row in second["forwarding_projection"])


def test_installed_paths_read_multiline_kernel_route_and_match_expected_gateway():
    assert risk_preview.installed_paths(
        "10.212.3.0/28 proto bgp metric 20\n"
        "\tnexthop via 10.211.11.1 dev eth0 weight 1\n"
        "\tnexthop via 10.211.21.1 dev eth1 weight 1\n"
        "10.212.1.0/28 proto kernel\n",
        1,
        3,
    ) == [1, 2]

"""Validate observed sessions, kernel ECMP and bidirectional data plane."""

from api.fabric import collect, trace


def main() -> None:
    sample = collect()
    for r in sample.routers:
        print(r.name, r.peers)
        assert r.peers and list(r.peers.values()) == ["Established"] * 2
        if r.name.startswith("leaf"):
            assert r.routes.count("nexthop via") == 2
    for direction in (1, 2):
        result = trace(direction)
        print(result)
        assert all(v == "success" for v in result["probes"].values())


if __name__ == "__main__":
    main()

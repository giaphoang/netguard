from api.traffic import rates


def test_rates_skip_resets_and_stale_intervals():
    old = {"1-1": (2, "mac", 10, 12, 9, 100, 200)}

    def read(*values):
        return rates(old, {"1-1": values})

    assert read(2, "mac", 12, 13, 11, 130, 230)["1-1"] == {
        "rx": 1,
        "tx": 2,
        "bytes_per_second": 30,
        "seconds": 2,
    }
    assert not read(3, "mac", 12, 13, 11, 130, 230)
    assert not read(2, "mac", 12, 1, 11, 130, 230)
    assert not read(2, "mac", 42, 13, 11, 130, 230)


def test_forwarding_lookup_can_select_new_spine(monkeypatch):
    import json
    from api import fabric

    router = fabric.Router(
        name="leaf-1", peers={}, routes="", interfaces="eth0 UP 10.212.1.1/28", error=None
    )
    monkeypatch.setattr(fabric, "command", lambda *_: json.dumps([{"gateway": "10.211.31.1"}]))
    assert fabric.probe_spine(router, "10.212.1.2", "10.212.3.2") == 3


def test_probe_paths_use_observed_host_attachments_and_only_animate_success(monkeypatch):
    from api import fabric

    snapshot = fabric.Snapshot.model_validate_json(fabric.FIXTURE.read_text())
    snapshot.routers.append(
        fabric.Router(name="spine-3", peers={}, routes="", interfaces="", error=None)
    )
    snapshot.hosts = [
        {"name": "host-1", "leaf": 1, "address": "10.212.1.2"},
        {
            "name": "host-3",
            "leaf": 2,
            "address": "10.212.2.3",
            "probe_from": "host-1",
            "probes": {"ICMP": "success", "HTTP": "success"},
        },
    ]

    def lookup(leaf, source, destination):
        assert (leaf.name, source, destination) == ("leaf-1", "10.212.1.2", "10.212.2.3")
        return 3

    monkeypatch.setattr(fabric, "probe_spine", lookup)
    [result] = fabric.probe_paths(snapshot, [])
    assert result["path"] == ["host-1", "leaf-1", "spine-3", "leaf-2", "host-3"]
    snapshot.hosts[-1]["probes"]["ICMP"] = "failed"
    [result] = fabric.probe_paths(snapshot, [])
    assert result["path"] is None
    snapshot.hosts.pop()
    assert fabric.probe_paths(snapshot, []) == []


def test_added_leaf_without_host_can_show_its_loopback_probe(monkeypatch):
    from api import fabric

    snapshot = fabric.Snapshot.model_validate_json(fabric.FIXTURE.read_text())
    snapshot.hosts = [
        {"name": "host-1", "leaf": 1, "address": "10.212.1.2"},
        {"name": "host-2", "leaf": 2, "address": "10.212.2.2"},
    ]
    snapshot.routers.append(
        fabric.Router(
            name="leaf-3", peers={}, routes="", interfaces="lo UNKNOWN 10.214.0.3/32", error=None
        )
    )

    def check(source, address, *, http):
        assert (source, address, http) == ("host-1", "10.214.0.3", False)
        return {"ICMP": "success"}

    monkeypatch.setattr(fabric, "probe_results", check)
    monkeypatch.setattr(fabric, "probe_spine", lambda *_: 2)
    [result] = fabric.probe_paths(snapshot, [])
    assert result["path"] == ["host-1", "leaf-1", "spine-2", "leaf-3"]


def test_live_map_probes_both_directions_for_each_discovered_host(monkeypatch):
    from api import fabric

    snapshot = fabric.Snapshot.model_validate_json(fabric.FIXTURE.read_text())
    snapshot.hosts = [
        {"name": "host-1", "leaf": 1, "address": "10.212.1.2"},
        {"name": "host-2", "leaf": 2, "address": "10.212.2.2"},
        {"name": "host-3", "leaf": 2, "address": "10.212.2.3"},
    ]
    monkeypatch.setattr(fabric, "probe_results", lambda *_args, **_kwargs: {"ICMP": "success"})
    monkeypatch.setattr(fabric, "probe_spine", lambda *_: 1)
    paths = {
        (r["source"], r["target"]): r["path"]
        for r in fabric.probe_paths(snapshot, [], all_directions=True)
    }
    assert len(paths) == 6
    assert paths["host-3", "host-1"] == ["host-3", "leaf-2", "spine-1", "leaf-1", "host-1"]
    assert paths["host-1", "host-3"] == ["host-1", "leaf-1", "spine-1", "leaf-2", "host-3"]
    assert paths["host-3", "host-2"] == ["host-3", "host-2"]

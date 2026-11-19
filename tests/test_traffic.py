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
    assert fabric.probe_spine(router, 1) == 3

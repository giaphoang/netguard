import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from api import fabric  # noqa: E402


def test_recorded_probe_directions(monkeypatch):
    monkeypatch.setattr(fabric, "REPLAY", True)
    with TestClient(fabric.app) as client:
        assert client.get("/api/state").json()["mode"] == "RECORDED REPLAY"
        for source in (1, 2):
            data = client.get(f"/api/trace?source={source}").json()
            assert data["source"] == source
            assert data["probes"] == {"HTTP": "success", "ICMP": "success"}
        assert client.get("/api/trace?source=3").status_code == 422


def test_persist_only_session_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(fabric, "DB", tmp_path / "events.db")
    s = fabric.Snapshot.model_validate_json(fabric.FIXTURE.read_text())
    fabric.record(s)
    fabric.record(s)
    assert len(fabric.timeline()) == 4
    s.routers[0].peers = None
    s.routers[0].error = "Telemetry unavailable"
    fabric.record(s)
    assert len(fabric.timeline()) == 5


def test_probe_spine_uses_forwarded_flow_tuple(monkeypatch):
    leaf = fabric.Router(
        name="leaf-2", interfaces="eth1@if8 UP 10.212.2.1/28", routes="", peers={}, error=None
    )

    def lookup(*args):
        assert args == (
            "leaf-2",
            "ip",
            "-j",
            "route",
            "get",
            "10.212.1.2",
            "from",
            "10.212.2.2",
            "iif",
            "eth1",
            "ipproto",
            "icmp",
        )
        return '[{"gateway":"10.211.22.1"}]'

    monkeypatch.setattr(fabric, "command", lookup)
    assert fabric.probe_spine(leaf, "10.212.2.2", "10.212.1.2") == 2
    monkeypatch.setattr(fabric, "command", lambda *_: '[{"dev":"lo"}]')
    assert fabric.probe_spine(leaf, "10.212.2.2", "10.212.1.2") is None


def test_host_inventory_uses_busybox_compatible_ip_command(monkeypatch):
    from api import fabric, expansion

    calls = []

    def run(node, *args):
        calls.append((node, args))
        assert args == ("ip", "addr", "show")
        return "2: eth0 UP inet 10.212.1.2/28"

    monkeypatch.setattr(fabric, "command", run)
    result = fabric.host_state(({"id": 1, "leaf": 1}, expansion.BASE))
    assert result["error"] is None and result["name"] == "host-1"
    assert calls

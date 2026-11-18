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

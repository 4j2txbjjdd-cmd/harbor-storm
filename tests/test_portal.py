"""The portal: ReliefRun's web lane, mounted over the frozen app.

Held to the same contracts as everything else: the seeded lane is
deterministic and offline, an observation applies at most once however many
times it is delivered, authority stays with the verifier, and the frozen
dashboard and API are served unchanged underneath.
"""
import pytest
from starlette.testclient import TestClient

from app.portal import portal, _memory_runs


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("STATE_BACKEND", "memory")
    monkeypatch.setenv("WEATHER_PROVIDER", "mock")
    _memory_runs.clear()
    with TestClient(portal) as c:
        yield c


def start(client):
    r = client.post("/relief/runs", json={})
    assert r.status_code == 200
    return r.json()


def test_seeded_run_commits_the_nine_oclock_mission(client):
    body = start(client)
    assert body["committed_plan_id"] == "relief-r0-p2"
    assert body["plan"]["metrics"]["departure_hour"] == 9.0


def test_barrier_lake_alert_revokes_and_recommits(client):
    run_id = start(client)["run_id"]
    r = client.post(f"/relief/runs/{run_id}/observe",
                    json={"profile": "disrupted"})
    body = r.json()
    assert body["revision_advanced"] is True
    kinds = [e["kind"] for e in body["new_events"]]
    assert "COMMIT_REVOKED" in kinds
    assert body["committed_plan_id"] == "relief-r1-p3"

    # Same forecast observed again -- a scheduler redelivery -- moves nothing.
    r2 = client.post(f"/relief/runs/{run_id}/observe",
                     json={"profile": "disrupted"})
    body2 = r2.json()
    assert body2["revision_advanced"] is False
    assert [e["kind"] for e in body2["new_events"]] == ["DUPLICATE_EVENT_IGNORED"]
    assert body2["committed_plan_id"] == "relief-r1-p3"


def test_authority_stays_with_the_verifier(client):
    run_id = start(client)["run_id"]
    client.post(f"/relief/runs/{run_id}/observe", json={"profile": "disrupted"})
    trace = client.get(f"/relief/runs/{run_id}/trace").json()["trace"]
    for e in trace:
        if e["kind"] in ("PLAN_VERIFIED", "PLAN_COMMITTED", "PLAN_REJECTED",
                         "COMMIT_REVOKED"):
            assert e["actor"] == "verifier"


def test_unknown_run_is_404(client):
    assert client.get("/relief/runs/nope").status_code == 404
    assert client.post("/relief/runs/nope/observe", json={}).status_code == 404


def test_create_means_create(client):
    """The creation endpoint must never open an existing operational run:
    re-planning a running mission belongs to the fenced observe lifecycle."""
    assert client.post("/relief/runs",
                       json={"run_id": "mission-a"}).status_code == 200
    second = client.post("/relief/runs", json={"run_id": "mission-a"})
    assert second.status_code == 409
    assert "observe it instead" in second.json()["detail"]


def test_default_run_ids_do_not_collide(client):
    a = client.post("/relief/runs", json={}).json()["run_id"]
    b = client.post("/relief/runs", json={}).json()["run_id"]
    assert a != b


def test_relief_page_and_config_serve(client):
    page = client.get("/relief")
    assert page.status_code == 200
    assert "ReliefRun" in page.text
    cfg = client.get("/relief/config").json()
    assert cfg["weather_provider"] == "mock"
    assert cfg["deterministic_replay"] is True


def test_frozen_app_is_served_unchanged_underneath(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    dash = client.get("/")
    assert dash.status_code == 200
    assert "HarborWindow" in dash.text


def test_live_lane_refuses_the_seeded_control(monkeypatch):
    monkeypatch.setenv("STATE_BACKEND", "memory")
    monkeypatch.setenv("WEATHER_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_WEATHER_API_KEY", "irrelevant")
    # Construction of the live provider must not happen for the refusal --
    # make it explode if anything tries.
    from app.providers import weather as w
    monkeypatch.setattr(w.GoogleWeatherProvider, "__init__",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError(
                            "live provider constructed for a seeded control")))
    _memory_runs.clear()
    with TestClient(portal) as c:
        r = c.post("/relief/runs/anything/observe", json={"profile": "disrupted"})
        assert r.status_code == 400
        assert "seeded-lane control" in r.json()["detail"]

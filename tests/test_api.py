"""HTTP surface. The API must not become a second way to change state."""
import base64
import json

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app import runner


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    for var in ("STATE_BACKEND", "WEATHER_PROVIDER", "GOOGLE_WEATHER_API_KEY",
                "GOOGLE_CLOUD_PROJECT"):
        monkeypatch.delenv(var, raising=False)
    runner.reset()
    yield
    runner.reset()


@pytest.fixture
def client():
    return TestClient(app)


def test_health_and_config(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    cfg = client.get("/config").json()
    assert cfg["state_backend"] == "memory"
    assert cfg["deterministic_replay"] is True


def test_scenarios_endpoint_names_the_flagship_and_still_offers_both(client):
    body = client.get("/scenarios").json()
    assert set(body["scenarios"]) == {"stormslot", "harborwindow"}
    assert body["selected"] == "harborwindow"


@pytest.mark.parametrize("scenario,expected_hour", [("stormslot", 14.0),
                                                    ("harborwindow", 14.0)])
def test_run_commits_a_replanned_slot(client, scenario, expected_hour):
    r = client.post("/runs", json={"scenario": scenario})
    assert r.status_code == 200
    body = r.json()
    assert body["committed_plan_id"] is not None
    assert body["committed_plan"]["metrics"]["departure_hour"] == expected_hour
    assert body["rejected_plans"], "the booked slot should appear as a rejection"
    kinds = [e["kind"] for e in body["trace"]]
    assert kinds.index("PLAN_REJECTED") < kinds.index("PLAN_COMMITTED")


def test_unknown_scenario_is_refused(client):
    r = client.post("/runs", json={"scenario": "moonbase"})
    assert r.status_code == 400
    assert "moonbase" in r.json()["detail"]


def test_unknown_run_is_404_not_an_empty_run(client):
    assert client.get("/runs/does-not-exist").status_code == 404
    assert client.get("/runs/does-not-exist/trace").status_code == 404
    assert client.post("/runs/does-not-exist/disrupt", json={}).status_code == 404


def test_disruption_revokes_and_replans(client):
    run = client.post("/runs", json={"scenario": "stormslot"}).json()
    first = run["committed_plan_id"]
    after = client.post(f"/runs/{run['run_id']}/disrupt",
                        json={"profile": "disrupted"}).json()
    kinds = [e["kind"] for e in after["trace"]]
    assert "COMMIT_REVOKED" in kinds
    assert after["committed_plan_id"] != first
    assert after["committed_plan"]["metrics"]["departure_hour"] == 17.0


def test_pubsub_push_drives_a_disruption(client):
    run = client.post("/runs", json={"scenario": "harborwindow"}).json()
    payload = json.dumps({"run_id": run["run_id"], "profile": "disrupted"}).encode()
    envelope = {"message": {"data": base64.b64encode(payload).decode(),
                            "messageId": "m1", "attributes": {}},
                "subscription": "sub"}
    r = client.post("/pubsub/push", json=envelope)
    assert r.status_code == 200
    assert r.json()["run_id"] == run["run_id"]
    trace = client.get(f"/runs/{run['run_id']}/trace").json()["trace"]
    assert "COMMIT_REVOKED" in [e["kind"] for e in trace]


def test_pubsub_push_accepts_attributes_only(client):
    run = client.post("/runs", json={"scenario": "stormslot"}).json()
    envelope = {"message": {"messageId": "m2",
                            "attributes": {"run_id": run["run_id"],
                                           "profile": "disrupted"}},
                "subscription": "sub"}
    assert client.post("/pubsub/push", json=envelope).status_code == 200


@pytest.mark.parametrize("envelope,why", [
    ({}, "no message object"),
    ({"message": {}}, "no run_id anywhere"),
    ({"message": {"data": "!!!not-base64!!!"}}, "undecodable data"),
    ({"message": {"data": base64.b64encode(b"not json").decode()}}, "not JSON"),
    ({"message": {"data": base64.b64encode(b'["a"]').decode()}}, "JSON but not an object"),
])
def test_malformed_push_is_rejected_loudly(client, envelope, why):
    r = client.post("/pubsub/push", json=envelope)
    assert r.status_code == 400, why


def test_api_cannot_commit_a_plan_directly(client):
    """There is no endpoint that sets committed_plan_id. Verification is the
    only path, and this test exists so nobody adds one casually."""
    paths = {r.path for r in app.routes if hasattr(r, "path")}
    assert not any("commit" in p for p in paths), paths


def test_dashboard_renders(client):
    r = client.get("/")
    assert r.status_code == 200 and "StormSlot" in r.text

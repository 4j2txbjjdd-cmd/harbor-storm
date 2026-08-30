"""The gateway-log scrubber, and the leak it exists to prevent.

Every key in this file is synthetic. The real one is revoked and must never
appear in a test, a fixture, or a failure message.

The bug being guarded against was not "someone forgot to redact". The probe DID
redact the URL it built. The live key was committed anyway, because the Cloud
Logging record carries its own copy of the request under
`httpRequest.requestUrl`. So these tests care about nesting, and about whether
the writer refuses when the redactor misses something.
"""
import json

import pytest

from app.geap.log_scrubber import (REDACTED, SecretResidueError, find_residue,
                                   sanitize, sanitize_or_raise, sanitize_url,
                                   write_sanitized)

# Synthetic, correctly shaped, and not a credential: AIza + 35 chars.
FAKE_KEY = "AIza" + "S" * 35
FAKE_TOKEN = "ya29." + "T" * 40


def _gateway_record(url):
    """The shape that actually leaked: the secret three levels down."""
    return [{
        "timestamp": "2026-08-28T08:32:24.974952Z",
        "httpRequest": {"requestMethod": "GET", "status": 200, "requestUrl": url},
        "jsonPayload": {
            "enforcedGatewaySecurityPolicy": {"hostname": "weather.googleapis.com"},
            "agentGatewayInfo": {"agentRegistryResource": "projects/1/locations/us-central1/endpoints/e1"},
            "mtls": {"clientCertPresent": "true"},
        },
    }]


# --- A. the field that actually leaked --------------------------------

def test_http_request_url_key_is_scrubbed():
    url = f"https://weather.googleapis.com/v1/forecast?lat=37.9&key={FAKE_KEY}"
    out = sanitize(_gateway_record(url))
    got = out[0]["httpRequest"]["requestUrl"]
    assert FAKE_KEY not in got
    assert "key=" in got and REDACTED in got


# --- B. nesting ------------------------------------------------------

def test_urls_are_scrubbed_at_any_depth():
    obj = {"a": [{"b": {"c": [f"https://x.example/p?key={FAKE_KEY}"]}}]}
    out = sanitize(obj)
    assert FAKE_KEY not in json.dumps(out)


def test_credential_fields_are_scrubbed_by_name():
    out = sanitize({"headers": {"Authorization": f"Bearer {FAKE_TOKEN}"}})
    assert out["headers"]["Authorization"] == REDACTED


# --- C. benign content survives --------------------------------------

def test_benign_query_parameters_survive():
    url = f"https://weather.googleapis.com/v1/f?location.latitude=37.942&hours=1&key={FAKE_KEY}"
    got = sanitize_url(url)
    assert "location.latitude=37.942" in got
    assert "hours=1" in got
    assert got.startswith("https://weather.googleapis.com/v1/f?")
    assert FAKE_KEY not in got


def test_url_without_secrets_is_untouched():
    url = "https://bigquery.googleapis.com/bigquery/v2/projects/p/datasets"
    assert sanitize_url(url) == url


# --- D. non-secret structures are preserved ---------------------------

def test_records_without_secrets_are_structurally_identical():
    rec = _gateway_record("https://bigquery.googleapis.com/v2/datasets")
    assert sanitize(rec) == rec


def test_substantive_gateway_evidence_survives_sanitisation():
    """Redaction must not cost the evidence its meaning."""
    rec = _gateway_record(f"https://weather.googleapis.com/v1/f?key={FAKE_KEY}")
    out = sanitize(rec)[0]
    assert out["httpRequest"]["status"] == 200
    assert out["jsonPayload"]["mtls"]["clientCertPresent"] == "true"
    assert out["jsonPayload"]["agentGatewayInfo"]["agentRegistryResource"].endswith("/e1")
    assert out["jsonPayload"]["enforcedGatewaySecurityPolicy"]["hostname"] == "weather.googleapis.com"


# --- E. fail closed ---------------------------------------------------

def test_key_shaped_residue_is_detected():
    assert find_residue({"note": FAKE_KEY})


def test_sanitize_or_raise_refuses_when_redaction_missed_something(monkeypatch):
    """The writer must not trust the redactor.

    A scrubber that silently missed a field would otherwise produce a file that
    reads as clean because nobody looked.
    """
    monkeypatch.setattr("app.geap.log_scrubber.sanitize", lambda obj: obj)
    with pytest.raises(SecretResidueError, match="credential-shaped"):
        sanitize_or_raise({"leaked": FAKE_KEY})


# --- F. nothing unsanitised is ever written ---------------------------

def test_write_sanitized_writes_only_clean_output(tmp_path):
    out = tmp_path / "evidence.json"
    url = f"https://weather.googleapis.com/v1/f?hours=1&key={FAKE_KEY}"
    write_sanitized(_gateway_record(url), str(out))
    body = out.read_text()
    assert FAKE_KEY not in body
    assert "hours=1" in body


def test_no_file_is_written_when_residue_survives(tmp_path, monkeypatch):
    """The refusal must happen before the file exists, not after."""
    out = tmp_path / "evidence.json"
    monkeypatch.setattr("app.geap.log_scrubber.sanitize", lambda obj: obj)
    with pytest.raises(SecretResidueError):
        write_sanitized({"leaked": FAKE_KEY}, str(out))
    assert not out.exists(), "a rejected capture must leave no artifact behind"

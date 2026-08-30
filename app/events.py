"""Pub/Sub ingestion seam for weather and operational disruptions.

A disruption is not a request to do something; it is new evidence. It arrives,
the committed plan is re-verified against it, and the commitment survives or is
revoked. Nothing an inbound message says can commit a plan -- the message only
supplies facts, and the verifier still decides.
"""
from __future__ import annotations
import base64
import binascii
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class MalformedEvent(ValueError):
    """An inbound message could not be read as a disruption event."""


@dataclass(frozen=True)
class DisruptionEvent:
    run_id: str
    kind: str = "WEATHER_UPDATE"
    # Which seeded forecast to apply when running without a live provider.
    profile: str = "disrupted"
    attributes: Dict[str, Any] = field(default_factory=dict)
    message_id: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any], message_id: Optional[str] = None,
                  attributes: Optional[Dict[str, Any]] = None) -> "DisruptionEvent":
        run_id = data.get("run_id") or (attributes or {}).get("run_id")
        if not run_id:
            raise MalformedEvent(
                "disruption event has no run_id, in the body or the attributes; "
                "refusing to guess which run it applies to"
            )
        return cls(
            run_id=str(run_id),
            kind=str(data.get("kind") or (attributes or {}).get("kind") or "WEATHER_UPDATE"),
            profile=str(data.get("profile") or (attributes or {}).get("profile") or "disrupted"),
            attributes=dict(attributes or {}),
            message_id=message_id,
        )


def parse_push(envelope: Dict[str, Any]) -> DisruptionEvent:
    """Decode a Pub/Sub push envelope.

    Shape:
        {"message": {"data": "<base64 json>", "messageId": "...",
                     "attributes": {...}}, "subscription": "..."}

    An attributes-only message (no data payload) is valid, so long as it
    carries run_id.
    """
    message = envelope.get("message")
    if not isinstance(message, dict):
        raise MalformedEvent("push envelope has no 'message' object")

    attributes = message.get("attributes") or {}
    raw = message.get("data")
    body: Dict[str, Any] = {}
    if raw:
        try:
            decoded = base64.b64decode(raw, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
            raise MalformedEvent(f"message.data is not base64 utf-8: {exc}") from exc
        try:
            body = json.loads(decoded)
        except json.JSONDecodeError as exc:
            raise MalformedEvent(f"message.data is not JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise MalformedEvent("message.data JSON must be an object")

    return DisruptionEvent.from_dict(body, message_id=message.get("messageId"),
                                     attributes=attributes)


def encode_push(event: DisruptionEvent) -> Dict[str, Any]:
    """Build a push envelope. Used by tests and by the demo trigger."""
    payload = json.dumps({"run_id": event.run_id, "kind": event.kind,
                          "profile": event.profile}).encode("utf-8")
    message: Dict[str, Any] = {
        "data": base64.b64encode(payload).decode("ascii"),
        "attributes": event.attributes,
    }
    # No synthesised id. A constant like "local" would make every locally built
    # envelope look like a redelivery of the same message, so the second demo
    # disruption would be silently ignored. Absent an id there is no delivery
    # identity, and the event applies every time -- which is correct for a
    # local trigger, and keeps the seeded replay deterministic in a way a
    # generated uuid would not.
    if event.message_id is not None:
        message["messageId"] = event.message_id
    return {"message": message, "subscription": "local"}


class PubSubPublisher:
    """Thin publisher for emitting disruptions onto a topic.

    Optional: the API's push endpoint works without it, and the seeded demo
    triggers disruptions directly. This exists so a real weather watcher can
    fan disruptions in from outside the service.
    """

    def __init__(self, topic: Optional[str] = None, project: Optional[str] = None):
        self.topic = topic or os.environ.get("PUBSUB_TOPIC")
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not self.topic:
            raise RuntimeError("PUBSUB_TOPIC is not set; nothing to publish to")
        if not self.project:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT is not set")
        from google.cloud import pubsub_v1
        self._client = pubsub_v1.PublisherClient()
        self._path = self._client.topic_path(self.project, self.topic)

    def publish(self, event: DisruptionEvent) -> str:
        payload = json.dumps({"run_id": event.run_id, "kind": event.kind,
                              "profile": event.profile}).encode("utf-8")
        future = self._client.publish(self._path, payload,
                                      **{k: str(v) for k, v in event.attributes.items()})
        return future.result(timeout=30)

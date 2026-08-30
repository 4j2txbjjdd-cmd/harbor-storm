"""The Harbor actor as it runs inside a managed Agent Runtime.

This is deployment infrastructure, not a second Harbor. It hosts three things
in one process and keeps them apart, because the whole claim rests on them
being apart:

  the bounded actor    an existing `LlmAgent` built by `build_actor_agent`,
                       holding an existing `ActorToolkit` and nothing else
  the verifier         `app.core.verify.verify_and_commit`, ordinary
                       deterministic code with no tool wrapper, unreachable
                       from the model
  authoritative state  `FirestoreStateStore`, written only through the
                       toolkit and the verifier

The model never gets a handle on the second or third. It gets five tools --
claim_work, read_facts, report_constraint, propose_plan, read_trace -- exactly
as it does locally, and `run_actor` re-audits that list against the outgoing
request on every model call.

Four identities meet here and are deliberately never collapsed into "the
agent":

  Google runtime identity   the Agent Identity principal Google provisioned
                            for this Reasoning Engine; chosen by Google, and
                            what IAM governs
  Harbor actor role         `window-agent`; chosen by Harbor's scope table
  model identity            the Gemini model that answered, as the server
                            reported it
  verifier identity         `verifier`, the actor name on every verdict

`egress_probe` exists to exercise the governed path from this identity. It is
not a tool: it is never offered to the model, and nothing the model does can
reach it.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SPIFFE_DIR = "/var/run/secrets/workload-spiffe-credentials"
TRUST_DOMAIN = "agents.global.org-648972411952.system.id.goog"
PROJECT_NUMBER = "801248256447"

SCENARIO = "harborwindow"
ACTOR = "window-agent"
WORK_ID = "window"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _run_actor_on_own_loop(agent: Any, briefing: str, session_id: str) -> Any:
    """Run the async actor from a host that already owns an event loop.

    Agent Runtime invokes handlers on a running loop, so `run_actor`'s
    `asyncio.run()` raises "cannot be called from a running event loop". A
    dedicated thread with its own loop fixes that here, in the host adapter.

    The alternative was editing `app/agents/execution.py` -- a frozen file --
    to solve a hosting problem that belongs to the host. The frozen seam still
    audits the outgoing tool declarations on every model call, which is the
    behaviour that matters, and it does so unchanged.
    """
    import asyncio
    import concurrent.futures

    from app.agents.execution import run_actor_async

    def _target():
        return asyncio.run(run_actor_async(agent, briefing, session_id=session_id))

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_target).result()


class HarborWindowRuntime:
    """Agent Runtime entrypoint for one bounded HarborWindow actor."""

    def __init__(self, project: str, location: str = "us-central1",
                 model: str = "gemini-3.5-flash",
                 model_location: str = "global",
                 firestore_database: Optional[str] = None):
        self.project = project
        self.location = location
        self.model = model
        # The runtime resource is regional; the model is not necessarily served
        # from the same region. gemini-3.5-flash is not a publisher model in
        # us-central1 and is served from the global endpoint, so the two are
        # configured separately rather than assumed equal.
        self.model_location = model_location
        self.firestore_database = firestore_database

    def _database(self) -> Optional[str]:
        """Which Firestore database holds authoritative truth.

        Resolved at call time, not at construction: this project's database is
        named `harbor` rather than `(default)`, and a runtime pickled before
        that was known would otherwise write to a database that does not exist
        and fail in a way that looks like a permissions problem.
        """
        return self.firestore_database or os.environ.get("FIRESTORE_DATABASE")

    def register_operations(self) -> Dict[str, List[str]]:
        """The methods Agent Runtime exposes, all synchronous.

        This is the *runtime's* surface, reachable by an authenticated caller
        of the Reasoning Engine. It is not the model's surface: the model sees
        only the five ActorToolkit tools, and nothing here is offered to it.
        """
        return {"": ["run_shift", "egress_probe", "whoami",
                     "transport_environment", "derived_identity_hint"]}

    # Agent Engine calls this once on the remote instance.
    def set_up(self) -> None:
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", self.project)
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", self.location)
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")

    # --- identity ---------------------------------------------------

    def whoami(self) -> Dict[str, Any]:
        """Infrastructure facts about this process. NOT the caller identity.

        The metadata server reports the *tenant* service account that hosts
        the managed runtime. It is not the Agent Identity, it is not what IAM
        evaluates, and naming it as the caller would misreport who Google
        authorized. It is reported under `tenant_infrastructure_sa` so it
        cannot be read as identity evidence.

        The authoritative Agent Identity comes from a control-plane GET on the
        Reasoning Engine (`spec.effectiveIdentity`) -- read from Google, never
        assembled here.
        """
        out: Dict[str, Any] = {
            "observed_at": _now(),
            "note": ("tenant_infrastructure_sa is the managed host identity, "
                     "not the Agent Identity; read spec.effectiveIdentity from "
                     "the control plane for that"),
        }
        base = "http://metadata.google.internal/computeMetadata/v1"
        for label, path in (("tenant_infrastructure_sa",
                             "instance/service-accounts/default/email"),
                            ("project", "project/project-id"),
                            ("numeric_project", "project/numeric-project-id")):
            try:
                req = urllib.request.Request(
                    f"{base}/{path}", headers={"Metadata-Flavor": "Google"})
                with urllib.request.urlopen(req, timeout=5) as r:
                    out[label] = r.read().decode()
            except Exception as exc:                    # noqa: BLE001
                out[label] = f"<unavailable: {type(exc).__name__}>"
        out["env_reasoning_engine"] = os.environ.get(
            "GOOGLE_CLOUD_AGENT_ENGINE_ID", os.environ.get("K_SERVICE", "<unset>"))
        return out

    # --- governed egress --------------------------------------------

    def egress_probe(self, targets: List[Dict[str, str]]) -> Dict[str, Any]:
        """Attempt an outbound call to each target from this runtime identity.

        Not a tool. Never offered to the model, and nothing the model can do
        reaches it -- the point of the probe is what the *infrastructure*
        permits this identity to do, and a result the model could influence
        would prove obedience rather than governance.

        Every attempt is reported: status, a short body excerpt, and any
        headers the gateway or IAP added. A refusal is a result, not an error.
        """
        import google.auth
        import google.auth.transport.requests
        import requests

        results: List[Dict[str, Any]] = []
        creds = None
        cred_kind = None
        try:
            creds, _ = google.auth.default()
            cred_kind = type(creds).__name__
        except Exception as exc:                        # noqa: BLE001
            results.append({"note": f"credential fetch failed: {type(exc).__name__}: {exc}"})

        # AuthorizedSession alone does NOT do mTLS: its __init__ sets
        # _is_mtls = False and mounts a plain HTTPAdapter. Only
        # configure_mtls_channel() mounts _MutualTlsAdapter and presents the
        # certificate, and it returns silently when check_use_client_cert()
        # is false -- so the result is asserted rather than assumed. Without
        # this the handshake dies with UNEXPECTED_EOF, which looks like a
        # network fault and proves nothing about authorization.
        session = None
        mtls_note = None
        if creds is not None:
            session = google.auth.transport.requests.AuthorizedSession(creds)
            os.environ.setdefault("GOOGLE_API_USE_CLIENT_CERTIFICATE", "true")
            try:
                session.configure_mtls_channel()
            except Exception as exc:                    # noqa: BLE001
                mtls_note = f"configure_mtls_channel raised {type(exc).__name__}: {exc}"
            if not getattr(session, "is_mtls", False):
                # Explicit callback over the mounted SPIFFE material, for the
                # case where the env switch alone does not take.
                cert = f"{SPIFFE_DIR}/certificates.pem"
                key = f"{SPIFFE_DIR}/private_key.pem"
                try:
                    session.configure_mtls_channel(
                        lambda: (open(cert, "rb").read(), open(key, "rb").read()))
                    mtls_note = (mtls_note or "") + " | fell back to explicit SPIFFE callback"
                except Exception as exc:                # noqa: BLE001
                    mtls_note = (mtls_note or "") + f" | explicit callback failed: {exc}"

        for t in targets:
            url = t["url"]
            # Never echo a URL that could carry a credential.
            entry: Dict[str, Any] = {"label": t.get("label", url),
                                     "url": url.split("?")[0],
                                     "attempted_at": _now()}
            if session is None:
                entry.update(outcome="ERROR", body_excerpt="no credentials")
                results.append(entry)
                continue
            try:
                # An arm may need the destination's own credential rather than
                # an OAuth bearer. Weather takes an API key; AuthorizedSession
                # always attaches OAuth, so that arm uses a plain session. The
                # two credentials are different layers and are kept apart:
                # the workload certificate is permission to traverse the
                # gateway, the API key is authentication to the service behind
                # it.
                mode = t.get("auth", "oauth")
                target_url = url
                if mode == "key":
                    key = os.environ.get("GOOGLE_WEATHER_API_KEY", "")
                    if not key:
                        entry.update(outcome="ERROR",
                                     body_excerpt="no GOOGLE_WEATHER_API_KEY set")
                        results.append(entry)
                        continue
                    sep = "&" if "?" in target_url else "?"
                    target_url = f"{target_url}{sep}key={key}"
                    r = requests.get(target_url, timeout=30)
                else:
                    r = session.request("GET", url, timeout=30)

                body = (r.text or "")[:400]
                hdrs = {k.lower(): v for k, v in r.headers.items()
                        if k.lower().startswith(("x-", "server", "via", "www-auth"))}
                # Classify by WHO answered, not by status code alone. An
                # IAP-generated response means the gateway refused before the
                # destination was reached. Anything carrying the destination's
                # own server markers means egress was permitted, whatever the
                # service then decided about the caller's credential.
                iap_denied = hdrs.get("x-goog-iap-generated-response") == "true"
                reached = (not iap_denied) and bool(hdrs.get("server"))
                entry.update(
                    outcome=("GOVERNANCE_DENIED" if iap_denied
                             else "DESTINATION_REACHED" if reached else "UNATTRIBUTED"),
                    destination_reached=reached,
                    http_status=r.status_code, body_excerpt=body, headers=hdrs)
            except Exception as exc:                    # noqa: BLE001
                entry.update(outcome="ERROR", http_status=None,
                             body_excerpt=f"{type(exc).__name__}: {exc}")
            results.append(entry)
        return {"credential_kind": cred_kind,
                # The identity that matters is the Agent Identity Google
                # issued, not the tenant service account the metadata server
                # reports -- that one is infrastructure, not the caller.
                "agent_engine_id": os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID"),
                "derived_identity_hint": self.derived_identity_hint(),
                "is_mtls": bool(getattr(session, "is_mtls", False)),
                "mtls_note": mtls_note,
                "transport_environment": self.transport_environment(),
                "results": results}

    def derived_identity_hint(self) -> Dict[str, str]:
        """A *hint*, explicitly not evidence.

        This string is assembled from constants and environment, so it says
        what the identity should be, not what Google issued. Presenting it as
        observed identity would let a wrong deployment agree with itself.
        Authoritative identity is `spec.effectiveIdentity` from a control-plane
        GET on the Reasoning Engine.
        """
        eng = os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID", "<unset>")
        loc = os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION", self.location)
        return {
            "kind": "derived-hint-not-evidence",
            "agent_engine_id": eng,
            "expected_principal": (
                f"principal://{TRUST_DOMAIN}/resources/aiplatform/projects/"
                f"{PROJECT_NUMBER}/locations/{loc}/reasoningEngines/{eng}"),
            "authoritative_source": (
                "GET .../reasoningEngines/{id} -> spec.effectiveIdentity"),
        }

    def transport_environment(self) -> Dict[str, Any]:
        """What the runtime was handed to authenticate itself with.

        Reported so a reader can tell a governed refusal from a
        misconfiguration: a 403 means the gateway made a decision, while a TLS
        failure means the client never presented the certificate the gateway
        was waiting for.
        """
        keys = sorted(k for k in os.environ
                      if any(m in k.upper() for m in
                             ("CERT", "SPIFFE", "MTLS", "AGENT", "IDENTITY",
                              "GOOGLE_API")))
        out: Dict[str, Any] = {"env_keys": keys}
        # Values only for switches, never for anything that could be material.
        out["env_values"] = {k: os.environ[k] for k in keys
                             if "KEY" not in k.upper() and "TOKEN" not in k.upper()}
        for path in ("/var/run/secrets/workload-spiffe-credentials",
                     "/var/run/gke-spiffe", "/etc/ssl/agent"):
            try:
                out.setdefault("paths", {})[path] = os.listdir(path)
            except Exception as exc:                    # noqa: BLE001
                out.setdefault("paths", {})[path] = f"<{type(exc).__name__}>"
        return out

    # --- the actual work --------------------------------------------

    def run_shift(self, run_id: str, briefing: Optional[str] = None,
                  stale_control: bool = False) -> Dict[str, Any]:
        """One bounded shift: the model proposes, Harbor decides.

        `stale_control` reproduces the #20 control inside the managed runtime:
        real external truth lands between the proposal and verification, and
        the candidate is refused for being bound to a world that has moved.
        """
        from app.agents.actors import build_actor_agent
        from app.agents.probe import BRIEFING, SEEDED_ACTORS
        from app.core.firestore_store import FirestoreStateStore
        from app.core.store import UNFENCED
        from app.core.verify import verify_and_commit
        from app.demo import disrupted_weather_fixture, weather_fixture
        from app.scenarios import harborwindow

        store = FirestoreStateStore(run_id, state=harborwindow.build_state(),
                                    project=self.project,
                                    database=self._database())
        seeded = harborwindow.seed(store, weather_fixture(), UNFENCED,
                                   claim_for=SEEDED_ACTORS)
        if seeded is None:
            raise RuntimeError("seeding aborted: a work item could not be claimed")
        harbor, island = seeded

        # A configured BaseLlm rather than a bare name, so the model endpoint
        # can differ from the runtime's region. `require_model_floor` reads
        # through it via `agent_model_id`, so the Gemini 3.5 floor is still
        # enforced on the real model id.
        from google.adk.models import Gemini
        model_obj = Gemini(model=self.model, client_kwargs={
            "vertexai": True, "project": self.project,
            "location": self.model_location})
        agent, _toolkit = build_actor_agent(store, SCENARIO, ACTOR, UNFENCED,
                                            model_obj)
        run = _run_actor_on_own_loop(agent, briefing or BRIEFING,
                                     session_id=f"shift-{run_id}")
        plan_id = run.plan_ids[-1] if run.plan_ids else None

        decision: Dict[str, Any] = {"plan_id": plan_id}
        if plan_id is None:
            decision["verifier_decision"] = "no proposal"
        elif stale_control:
            facts = store.state.facts
            d = disrupted_weather_fixture()
            lease = store.advance_revision(
                "weather-agent", "marine forecast updated",
                {"harbor": facts["harbor"], "island": facts["island"]},
                event_id=f"{run_id}-marine-update")
            fence = lease.fence
            store.emit("MARINE_WEATHER_UPDATED", "weather-agent",
                       {"harbor": facts["harbor"], "island": facts["island"]})
            verifier = harborwindow.make_verifier(
                store, d.hourly(facts["harbor"]), d.hourly(facts["island"]))
            committed = verify_and_commit(store, plan_id, verifier, fence)
            store.complete_event(f"{run_id}-marine-update", "weather-agent", fence)
            decision["verifier_decision"] = "accepted" if committed else "rejected"
        else:
            verifier = harborwindow.make_verifier(store, harbor, island)
            committed = verify_and_commit(store, plan_id, verifier, UNFENCED)
            decision["verifier_decision"] = "accepted" if committed else "rejected"

        state = store.refresh()
        plan = store.get_plan(plan_id) if plan_id else None
        return {
            "run_id": run_id,
            "started_at": _now(),
            # Four attributions, kept apart on purpose.
            "runtime_infrastructure": self.whoami(),
            "harbor_actor_role": ACTOR,
            "model_requested": self.model,
            "model_versions_reported": run.model_versions,
            "verifier_actor": "verifier",
            # What the model was allowed to do.
            "tools_offered_to_model": run.tools_offered,
            "model_tool_calls": [c["name"] for c in run.tool_calls],
            "model_turns": len(run.turns),
            # What it proposed and what Harbor did about it.
            "candidate_plan": None if plan is None else {
                "id": plan.id, "created_by": plan.created_by,
                "actions": plan.actions, "metrics": plan.metrics,
                "basis_revision": plan.basis_revision,
                "verified": plan.verified,
                "rejection_reason": plan.rejection_reason},
            **decision,
            "authoritative_state": {
                "committed_plan_id": state.committed_plan_id,
                "revision": state.revision,
                "work_window_claimed_by": state.work[WORK_ID].claimed_by},
            "trace": [{"seq": e["seq"], "kind": e["kind"], "actor": e["actor"],
                       "payload": {k: v for k, v in (e["payload"] or {}).items()
                                   if k in ("plan_id", "work_id", "reason",
                                            "revision", "severe_hours")}}
                      for e in store.trace()],
        }

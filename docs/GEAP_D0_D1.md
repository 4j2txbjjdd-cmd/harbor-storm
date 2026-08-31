# D0 / D1 — Harbor on the managed control plane

Harbor's bounded-actor architecture, run against Google's real Agent Runtime.
The claim being tested is narrow and specific:

> Google chooses who the actor is. Google restricts what that actor may reach.
> Gemini proposes. Harbor decides what becomes true.

The baseline is the pre-control-plane freeze `core-freeze-1`, engineering commit
`cf91551`. **No frozen file was modified by the work described here.** All of it
is additive: `app/geap/` (the runtime adapter) and `geap/` (deploy scripts and
evidence). A separate, later change touched five files for presentation only and
no operational-core file; that delta is itemised in
[docs/CORE_FREEZE.md](CORE_FREEZE.md). Those are stated provenance facts about
the engineering history. This repository is the submitted tree itself, not that
history, so nothing below asks a reader to resolve a tag or a commit range.

## D0 — one real Agent Identity runtime: PASS

`geap/d0_create_runtime.py` creates the smallest legitimate runtime: a display
name and `identity_type=AGENT_IDENTITY`, with **no `service_account` key at
all**. Agent Identity requires its absence, and an absent key is the only way to
be sure it was never set.

The gate is met on a **control-plane GET**, not on the create response
(`geap/d0_readback.json`):

```json
{
  "name": "projects/801248256447/locations/us-central1/reasoningEngines/1562121799313915904",
  "spec": {
    "identityType": "AGENT_IDENTITY",
    "effectiveIdentity": "agents.global.org-648972411952.system.id.goog/resources/aiplatform/projects/801248256447/locations/us-central1/reasoningEngines/1562121799313915904"
  }
}
```

`spec` contains exactly two keys: `identityType` and `effectiveIdentity`. There
is no `serviceAccount` field, and the identity is bound to this one Reasoning
Engine.

### What is *not* identity evidence

The metadata server inside the runtime reports
`ne21cbb00db953e63p-tp@appspot.gserviceaccount.com`. That is the **tenant
infrastructure service account** that hosts the managed runtime. It is not the
Agent Identity, it is not what IAM evaluates, and it must never be quoted as the
caller. `runtime_app.whoami()` reports it under `tenant_infrastructure_sa` for
exactly this reason, and `derived_identity_hint()` is labelled
`derived-hint-not-evidence` because it is assembled from constants — a wrong
deployment would otherwise agree with itself.

Authoritative identity always comes from `spec.effectiveIdentity` on a
control-plane read.

## D1 — governed vertical slice: PASS

### The managed actor path (D1.1, D1.5, D1.6)

`app/geap/runtime_app.py` hosts three things in one process and keeps them
apart: the bounded actor (an existing `LlmAgent` from `build_actor_agent`
holding an existing `ActorToolkit`), the deterministic verifier
(`verify_and_commit`, ordinary code with no tool wrapper), and authoritative
state (`FirestoreStateStore`). The model is offered exactly five tools —
`claim_work`, `read_facts`, `report_constraint`, `propose_plan`, `read_trace` —
and nothing else, re-audited against the outgoing request on every call. There is
no model-facing verify, no commit, no revision advance, and no direct
authoritative Firestore mutation.

Accept path (`geap/d1_shift_accept.json`), engine `3244216260136796160`
(**Engine A**):

```
tools_offered_to_model : claim_work, read_facts, report_constraint, propose_plan, read_trace
model_tool_calls       : claim_work, read_facts, read_trace, report_constraint, propose_plan, read_trace
model_versions_reported: gemini-3.5-flash
candidate              : reserve_boat 14 / load_cargo 320 / depart 14
verifier_decision      : accepted
authoritative_state    : committed_plan_id=harbor-agent-plan-1, revision=0
```

Independently re-read from Firestore from a different machine, database `harbor`:

```
 9  CLAIMED             window-agent  {'work_id': 'window'}
10  CONSTRAINT_REPORTED window-agent
11  PLAN_PROPOSED       window-agent  {'plan_id': 'harbor-agent-plan-1'}
12  PLAN_VERIFIED       verifier      {'revision': 0}
13  PLAN_COMMITTED      verifier      {'revision': 0}
```

Stale control (`geap/d1_shift_control.json`), same runtime: the model proposed
the same correct plan, external truth advanced the world, and Harbor refused it —
`stale: plan bound to revision 0, world is at revision 1`, `committed_plan_id:
None`, no `PLAN_VERIFIED` written.

Four attributions stay distinct and are never collapsed into "the agent": Google
runtime identity, Harbor actor role (`window-agent`), model identity
(`gemini-3.5-flash` as the server reported it), verifier identity (`verifier`).

**IAM governs this runtime.** `roles/datastore.user` is held by seven
agent-identity `principal://` members, one per engine
(`geap/iam_project_agent_principals.json`). It also carries a pre-existing
Compute Engine default service account that Harbor did not grant and that is not
the identity the runtime presents — that member is named and reasoned about in
[docs/TOKEN_SHARING_RATIONALE.md](TOKEN_SHARING_RATIONALE.md), and it is *not*
visible in the capture above, which is filtered to the agent-identity trust
domain and says so in its own `note` field. So the narrow claim is the one that
holds: this runtime reached Firestore as its Agent Identity. I do not claim that
all Firestore access in the project is Agent-Identity-bound; the earlier
wording, "granted *only* to the agent-identity principal", overstated that.
Firestore moved 401 → 403 → success as token
sharing was enabled and the grant propagated — the 401 and the two 403 legs are
in `geap/firestore_iam_enforcement_legs.json`, the success in
`geap/d1_shift_accept.json`. The runtime reaches Firestore because Google's
identity was authorized, not because a service account existed.

### Governed egress (D1.2, D1.3, D1.4)

The authorization edge is four resources, and all four must exist. Endpoint IAM
alone does nothing:

```
Agent Runtime (Agent Identity)
  -> harbor-egress-gw            AGENT_TO_ANYWHERE, SECURE_WEB_GATEWAY
  -> authzPolicy                 REQUEST_AUTHZ / CUSTOM, targets the gateway
  -> authzExtension              service=iap.googleapis.com, iapPolicyVersion=V1
  -> endpoint IAM                roles/iap.egressor on the registered endpoint
```

Before the policy and extension existed, endpoint IAM was never consulted and
the gateway answered every request with `default_denied` — including weather.
A denial that hits everything is not governance evidence.

The probe below is the **pre-rotation** governed run, engine
`4557684054584983552`, `is_mtls: True` (`geap/d1_egress_final.json`). The same
allow/deny pair was re-proven after credential rotation on the engine this
submission cites as **Engine B**, `2414533581910048768` — see *Credential
rotation* and *Honest limits*. One identity, one gateway, one transport:

| arm | endpoint IAM | outcome | HTTP | attribution |
|---|---|---|---|---|
| `weather.googleapis.com` | `iap.egressor` **granted** | `DESTINATION_REACHED` | **200** + forecast | `server: ESF`, `server-timing: gfet4t7`, **no** IAP header |
| `cloudresourcemanager.googleapis.com` | **absent** | `GOVERNANCE_DENIED` | 403 | `x-goog-iap-generated-response: true`, *"Egress request is not authorized."* |
| `bigquery.googleapis.com` (unregistered) | n/a | `GOVERNANCE_DENIED` | 403 | *"...unregistered in the Agent Registry."* |

Classification is by **who answered**, not by status code: an IAP-generated
response means the gateway refused before the destination was reached; the
destination's own server markers mean egress was permitted.

Google's own gateway logs agree (`geap/gw_logs_final.json` — the Weather
arm's `httpRequest.requestUrl` carries an API key as a query parameter, so
that one value is stored as `key=[REDACTED]`; nothing else in the artifact
was altered):

```
GET 200  weather.googleapis.com              registry=...endpoints/...3e88-93525e6955a6   cert=true
GET 403  cloudresourcemanager.googleapis.com registry=...endpoints/...dffe-b2901c86a27a   cert=true
GET 403  bigquery.googleapis.com             registry=(none - unregistered)               cert=true
```

Read those records for what they contain: they name the gateway and the registry
endpoint, and they **do not carry an engine id**. Attributing them to a
particular engine comes from the controlled invocation and its time window
together with the deploy record — not from the log itself, and not from
`derived_identity_hint`, which is labelled `derived-hint-not-evidence` for
exactly this reason.

**Routing control.** Identical probe code on a *non*-gateway runtime
(`8409845032730755072`, `geap/d1_egress_nogw2.json`) reaches all three freely:
`cloudresourcemanager` 200, `bigquery` 200. Same code, same identity type; the
gateway binding is the only variable.

**Two credential layers, kept apart.** The workload certificate is permission to
traverse the gateway; the Weather API key is authentication to the service
behind it. The allow arm uses the key and no bearer, because
`AuthorizedSession` always attaches OAuth and Weather rejects that bearer — an
earlier run returned 401 *from ESF*, which already proved egress was permitted.

### Credential rotation (2026-08-28)

The Weather API key used by the allow arm was exposed in an earlier evidence
artifact: `geap/gw_logs_final.json` carried it inside the Cloud Logging record's
own `httpRequest.requestUrl`. The probe redacted the URL *it* wrote; it did not
redact the one Google wrote. Rewriting history to remove that value took it out
of the repository but did not un-publish it, so the credential was treated as
compromised on its own merits.

Rotated in this order, the replacement proven working before the old key was
retired:

1. replacement key created, restricted to `weather.googleapis.com` at creation
2. the governed-egress runtime redeployed (`2414533581910048768`); the re-proof
   in step 3 ran on that deployment
3. governed pair re-proven through the same Agent Identity and gateway —
   weather **200** with a real forecast, cargo **403** IAP-generated
   (`geap/d1_egress_rotated.json`, `geap/gw_logs_rotated.json`)
4. secret scan **over this repository's own content** — zero matches for either
   key value and zero `AIza`-shaped strings. That scope is the whole point of
   the limit below: scanning a repository says nothing about copies that already
   left it, which is why the key was rotated rather than only redacted
5. only then: the old key retired
6. old key re-tested — **400 `INVALID_ARGUMENT`, "API key expired"** where it
   had returned 200 immediately before; the replacement still 200

Step 6 is what the retirement claim rests on: the observed loss of function of
the old credential. Publication cannot be undone, so nothing here claims the
exposed value was destroyed — only that it no longer authenticates.

Gateway log capture now scrubs query-string credentials *before* anything
reaches disk, rather than relying on the probe to have redacted its own copy:

- `app/geap/log_scrubber.py` — recursive sanitiser. Redacts credential-valued
  query parameters (`key`, `api_key`, `apikey`, `access_token`, …) while keeping
  scheme, host, path, every benign parameter and the *name* of the secret one,
  so the evidence still shows that a key was sent and to where. Credential-named
  structured fields (`authorization`, …) are replaced wholesale.
- **Fail closed.** `write_sanitized` re-scans the sanitised object for
  credential shapes (`AIza[0-9A-Za-z_-]{35}`, `ya29.…`, bearer headers, PEM
  private keys) and raises `SecretResidueError` *before opening the file*. A
  rejected capture leaves no artifact: a partially-redacted evidence file is
  worse than none, because it reads as safe. The writer does not trust the
  redactor. A control that depends on someone being suspicious is not a control.
- `geap/capture_gateway_logs.py` — the CLI. The Cloud Logging response is parsed
  in memory, sanitised, checked, and only then serialised. Exit 2 means
  something survived and nothing was written.
- `tests/test_log_scrubber.py` — 11 tests over synthetic keys only, covering the
  field that actually leaked (`httpRequest.requestUrl`), arbitrary nesting,
  survival of benign parameters and of the substantive gateway evidence, and
  both fail-closed paths.

`geap/gw_logs_rotated.json` was regenerated through that CLI, not hand-edited:

```
PYTHONPATH=. .venv/bin/python geap/capture_gateway_logs.py \
    --out geap/gw_logs_rotated.json --limit 6 --freshness 6h
```

The weather record it wrote keeps `location.latitude`, `location.longitude` and
`hours=1`, and carries `key=[REDACTED]`.

### Honest limits

- **Which engine proved what.** The submission's canonical split is
  **Engine A = `3244216260136796160`**, the managed actor proof, which is *not*
  gateway-bound, and **Engine B = `2414533581910048768`**, the governed-egress
  proof, which runs no actor. The governed allow/deny pair was first proven
  before rotation on `4557684054584983552`; after the Weather key was rotated,
  the same pair was re-proven on Engine B — same gateway, same IAM geometry, new
  credential. The pre-rotation evidence stands as recorded
  (`geap/d1_egress_final.json`, `geap/gw_logs_final.json`): it is an earlier,
  independent instance of the same result, not something the rotated run
  replaces. Engine B is the one the rest of this package cites.
- **Part of the two-engine split is mechanical.** The gateway-bound engine's
  own denials include `iamcredentials.mtls.googleapis.com` (403, unregistered —
  visible in `geap/gw_logs_rotated.json`). Anything the actor path needs, Vertex
  AI and Firestore included, has to be registered and bound before one engine
  can do both jobs. That is the concrete reason the halves are split, not a
  reluctance to combine them.
- **The two halves run on two engines: at the time of this record, this was
  not demonstrated end-to-end on one engine.** *(Superseded 2026-08-31 — see
  the convergence addendum at the end of this document. The paragraph below
  stands as the accurate record of the state it describes.)* Engine A (`3244216260136796160`) carries no gateway binding, has
  token sharing on, and reaches Firestore. The governed-egress half is
  gateway-bound and runs no actor — first on `4557684054584983552`, which also
  had token sharing on, and after rotation on Engine B (`2414533581910048768`).
  The split is therefore not a token-sharing split. A single engine doing both
  requires the actor path's Google API dependencies to be reachable through the
  governed egress configuration. That convergence was not completed here, and
  nothing in this package claims one engine proved the complete actor plus
  Gateway path.
- **IAP audit logs only record the unregistered denials.** After enabling
  DATA_READ for `iap.googleapis.com`, `AuthorizeUser` entries appear with
  `granted: false` for unregistered endpoints only, and `authenticationInfo` is
  empty. The allow and the registered-but-unbound denial produce no IAP audit
  line, so the decision evidence rests on the gateway logs and response
  attribution above rather than on an IAP `granted: true` record.

## Cloud resources created

| type | name | region | purpose |
|---|---|---|---|
| ReasoningEngine | `1562121799313915904` | us-central1 | D0 identity probe |
| ReasoningEngine | `2753323900753412096` | us-central1 | D1 iteration (401 token sharing) |
| ReasoningEngine | `2248920742487916544` | us-central1 | D1 iteration (event-loop) |
| ReasoningEngine | `1830085977142460416` | us-central1 | D1 iteration (model region) |
| ReasoningEngine | `3244216260136796160` | us-central1 | **Engine A — D1 accept + stale control; not gateway-bound** |
| ReasoningEngine | `1047585541886836736` | us-central1 | gateway-bound (first) |
| ReasoningEngine | `1492316005089673216` | us-central1 | **gateway-bound engine under test** |
| ReasoningEngine | `8409845032730755072` | us-central1 | **non-gateway routing control** |
| ReasoningEngine | `4557684054584983552` | us-central1 | **gateway-bound engine, pre-rotation allow/deny proof** |
| ReasoningEngine | `2414533581910048768` | us-central1 | **Engine B — gateway-bound, post-rotation allow/deny proof; runs no actor** |
| AgentGateway | `harbor-egress-gw` | us-central1 | AGENT_TO_ANYWHERE egress |
| Registry service | `harbor-weather` → endpoint `agentregistry-…-3e88-93525e6955a6` | us-central1 | allow candidate |
| Registry service | `harbor-cargo-ops` → endpoint `agentregistry-…-dffe-b2901c86a27a` | us-central1 | deny control (unbound) |
| authzExtension | `harbor-iap-authz` | us-central1 | IAP enforcement, V1 |
| authzPolicy | `harbor-iap-request-authz` | us-central1 | REQUEST_AUTHZ/CUSTOM on the gateway |
| GCS bucket | `harbor-storm-fleet-agent-staging` | us-central1 | agent staging |

Failed/superseded engines are retained deliberately: deleting them would destroy
the IAM and log evidence the diagnosis rests on.

Project audit config: DATA_READ + ADMIN_READ enabled for `iap.googleapis.com`,
to surface IAP decisions. This work changed no other service's audit
configuration.

IAM: the grants are **not** symmetric across engine principals.
`roles/datastore.user` and `roles/aiplatform.user` are each held by the same
seven agent-identity `principal://` members
(`geap/iam_project_agent_principals.json`). `roles/iap.egressor` is held by four
members, and only on the weather registry endpoint — the cargo endpoint's policy
carries no bindings at all (`geap/iap_endpoint_policies.json`). The two sets are
not the same set: Engine A is among the seven and holds no `iap.egressor`;
Engine B holds `iap.egressor` and is not among the seven.

Scope of the negatives, because an unscoped one would be false here. Within
those two captures there is no project-wide `iap.egressor` grant, and neither
`roles/datastore.user` nor `roles/aiplatform.user` contains a `principalSet`
member. A `principalSet://` member *does* appear in the same capture, on
`roles/aiplatform.agentDefaultAccess` across the platform container — so "no
`principalSet`" as a blanket statement would be wrong. The project-IAM capture
is filtered to principals under the agent-identity trust domain and says so;
the endpoint capture is the full IAP IAM readback of the two registry
endpoints, scoped to those endpoints by construction. Neither is evidence
about the project's other members, and neither is offered as a statement about
basic project roles.

## Known limits of this evidence

These are properties of what was demonstrated, recorded so a reader does not have
to discover them by reading the artifacts.

- **Registry granularity.** Per-tool MCP authorization is not expressible in
  `agentregistry` v1 — authorization is per registered server/endpoint. Actor-level
  bounds therefore come from `ActorToolkit`, not from the registry.
- **Token sharing.** `GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=False`
  is set. Read the polarity carefully: the *prevention* is off, so token sharing
  is permitted. What that relaxes is certificate-bound-token protection, not
  Agent Identity IAM enforcement, and Google's own Runtime→Gateway example
  combines it with
  `identity_type=AGENT_IDENTITY`. It is needed for Firestore-over-gRPC; it is not
  needed for the gateway path. Full reasoning in `docs/TOKEN_SHARING_RATIONALE.md`.
- **The event loop.** Agent Runtime invokes handlers on a running loop, so
  `run_actor`'s `asyncio.run()` raises. This is handled in the host adapter
  (`_run_actor_on_own_loop`) rather than by editing the frozen execution seam.
- **Model region.** `gemini-3.5-flash` is not a publisher model in `us-central1`.
  The runtime is regional; the model is configured separately against the global
  endpoint via an explicit `Gemini(client_kwargs={"vertexai": True, ...})`. The
  frozen `agent_model_id` reads through the `BaseLlm`, so the 3.5 floor is still
  enforced on the real model id.
- **The managed stale control is narrower than the local one.** It rejects on
  revision before any content verifier runs, so deleting `disrupted_weather_fixture`
  would not change the managed evidence. The revision fence is what that artifact
  proves; the content verifier is proven locally instead
  (`app.demo harborwindow --pretty`, and `tests/test_verification_membrane.py`).
- **Telemetry instrumentation is Google's, not Harbor's.** Runtime logs report
  telemetry enabled with the OpenTelemetry instrumentation packages absent
  (`opentelemetry-instrumentation-grpc`, `-httpx`, `-google-genai`). Cloud Trace
  still receives spans from Agent Runtime itself; Harbor authored no
  instrumentation and does not claim OpenTelemetry as an integration.


## Addendum: the convergence (2026-08-31)

The limitation above was closed exactly the way it predicted. A new engine,
`harbor-converged` (`6110651869841850368`), was deployed with the actor app,
Agent Identity, token sharing for GCP services, and a binding to
`harbor-egress-gw` (`geap/d2_deployed_converged.json`). Its first invocation
was refused by the gateway — *"unregistered in the Agent Registry"* — and the
convergence proceeded denial-first from there: each destination the actor
path needs was named by a gateway refusal, then registered in the Agent
Registry and bound with a per-endpoint `roles/iap.egressor` grant for the
engine's Agent Identity. The sequence the gateway dictated:
`iamcredentials.mtls.googleapis.com`, then `telemetry.mtls.googleapis.com`
and the mTLS model-plane hosts (`aiplatform.mtls.googleapis.com`,
`us-central1-aiplatform.mtls.googleapis.com`), with
`firestore.googleapis.com` and `aiplatform.googleapis.com` registered
alongside. The initial denials are preserved in
`geap/gw_logs_converged_probe.json`.

With the bindings in place, `run_shift` completed end-to-end on the
converged engine: `gemini-3.5-flash` server-reported, the five bounded
tools, the verifier accepting and committing `harbor-agent-plan-1`
(`geap/d2_converged_accept.json`), and the stale control refused on the same
engine — *"stale: plan bound to revision 0, world is at revision 1"*, nothing
committed (`geap/d2_converged_control.json`). The gateway's own records for
the window are all `ALLOWED`/200, attributed to the newly registered
endpoints (`geap/gw_logs_converged.json`). Engines A and B stand above as
the original record; the deny control (`harbor-cargo-ops`, no bindings) was
not touched.

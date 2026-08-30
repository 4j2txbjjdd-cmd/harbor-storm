# Devpost submission fields

*Working sheet for the person filling in the form. Every entry below was checked
against the tree and against live cloud state on **2026-08-28**. Nothing here is
aspirational: if a technology could not be shown to run in the submitted path, it
was dropped and the reason is recorded.*

*Amended **2026-08-30**: the wall-clock sentinel (`app/sentinel.py`,
`tests/test_sentinel.py`) was added as purely additive files, and the local
Pub/Sub-push ingress demo was documented — see "Long-horizon operation" below
and README §3. Later the same day, **ReliefRun** was added — a third, additive
instantiation of the substrate (`app/scenarios/reliefrun.py`,
`app/relief_demo.py`, `tests/test_reliefrun.py`): a disaster-relief mission on
a hazard-gated corridor, informed by the dynamics of the August 2026 Nepal
floods, with fictional seeded data. Finally the portal (`app/portal.py`,
`app/static/relief.html`, `tests/test_portal.py`) put ReliefRun on the web
over the frozen API, and the service was deployed to Cloud Run — see Built
With row 19; that service URL is the value for the Devpost hosted-URL field.
Test counts updated accordingly; GEAP cloud-state rows are unchanged and
still dated 2026-08-28.*

---

## Built With — 18 tags

Devpost asks for implementation technology, capped at 25 tags. These 18 are each
exercised in the path being submitted.

| # | tag | what makes it true |
|---|---|---|
| 1 | `Python` | the entire implementation; 3.12 in the venv, 3.11+ required |
| 2 | `Gemini 3.5 Flash` | `geap/d1_shift_accept.json` → `model_versions_reported: ["gemini-3.5-flash"]`, reported by the server, not configured by me |
| 3 | `Google Agent Development Kit` | `google-adk==2.8.0`; imported at `app/agents/actors.py:147` (`LlmAgent`), `app/agents/tools.py:134` (`FunctionTool`), `app/agents/execution.py:238` (`Runner`), `app/geap/runtime_app.py:354` (`Gemini`) |
| 4 | `Vertex AI` | `vertexai.Client(...)` in `geap/d1_invoke.py`; `GOOGLE_GENAI_USE_VERTEXAI=true` on the probe path; `aiplatform.googleapis.com` enabled |
| 5 | `Agent Runtime` | 15 live Reasoning Engines in `us-central1`; the actor proof ran on `3244216260136796160`, which is **not** Gateway-bound, and governed egress was proved separately on `2414533581910048768`, which runs no actor — the combined path is not demonstrated end-to-end on one engine |
| 6 | `Agent Identity` | control-plane GET on the engine returns `identityType: AGENT_IDENTITY` with **no** `serviceAccount` key and an engine-bound `effectiveIdentity` (`geap/d0_readback.json`) |
| 7 | `Agent Gateway` | engine `2414533581910048768` carries `spec.deploymentSpec.agentGatewayConfig.agentToAnywhereConfig → harbor-egress-gw`; 8 engines bound, and the actor engine `3244216260136796160` is not one of them |
| 8 | `Agent Registry` | two registered endpoints returned live by `agentregistry.googleapis.com`: `harbor-weather` and `harbor-cargo-ops`. Registration is not an authorization allowlist — `harbor-cargo-ops` is registered and still denied; per-endpoint IAP IAM is what authorizes egress |
| 9 | `Google Cloud` | project `harbor-storm-fleet` (`801248256447`) |
| 10 | `Cloud Firestore` | `google-cloud-firestore==2.29.0`; authoritative store; independent readback of run `geap-d1-shift-4` returns `committed_plan_id = harbor-agent-plan-1` |
| 11 | `Google Weather API` | `weather.googleapis.com` is the registered endpoint that also carries `roles/iap.egressor`, so it is the destination egress is authorized to reach; 200 with a real forecast and `server: ESF` in `geap/d1_egress_rotated.json`; also the live weather lane (`app.live_gate`) |
| 12 | `Identity-Aware Proxy` | `roles/iap.egressor` on the weather endpoint; denials carry `x-goog-iap-generated-response: true`; authz extension `harbor-iap-authz` targets `service: iap.googleapis.com` |
| 13 | `Google Cloud IAM` | in the captured policy read, filtered to the agent-identity trust domain, `roles/aiplatform.user` is bound to seven per-engine `principal://` members and nothing else, and `roles/datastore.user` to those same seven; the unfiltered project policy adds a pre-existing compute service account on `roles/datastore.user` that Harbor did not grant. Neither Harbor-granted role carries a `principalSet` or a project-wide role. `roles/iap.egressor` granted per engine on one registry endpoint only |
| 14 | `Google Cloud Network Security` | `networksecurity.googleapis.com` holds authzPolicy `harbor-iap-request-authz` (`action: CUSTOM`) targeting `harbor-egress-gw` |
| 15 | `Google Cloud Network Services` | `networkservices.googleapis.com` holds authzExtension `harbor-iap-authz` (`iapPolicyVersion: V1`, `timeout: 1s`) |
| 16 | `Cloud Logging` | Google's own gateway decisions (`geap/gw_logs_rotated.json`) and the Firestore IAM enforcement legs (`geap/firestore_iam_enforcement_legs.json`) are read from Cloud Logging |
| 17 | `GitHub` | `4j2txbjjdd-cmd/harbor-storm` — the repository this submitted tree is published from, and the one the reproduce commands below clone. Development history is kept in a separate private engineering repository; what is published here is the frozen submitted tree, not that history |
| 18 | `pytest` | `pytest==9.1.1`; 287 passing tests |
| 19 | `Cloud Run` | service `harbor-storm` in `us-central1` serving the portal publicly at <https://harbor-storm-801248256447.us-central1.run.app> (deployed 2026-08-30); one pinned instance per `deploy/service.yaml`'s trace-ordering rationale; Firestore-durable runs (database `harbor`) |
| 20 | `Cloud Build` | built image `…/harbor/harbor-storm:portal-5bf0794` from the submitted tree, 2026-08-30 |

### Dropped after investigation

**`OpenTelemetry` — DROPPED. The spans exist, and they are Google's, not mine.**

The SDK is genuinely in `requirements.txt` (`opentelemetry-api`, `-sdk`,
`-semantic-conventions` — pulled in transitively by `google-adk`). Three checks
decide it. The third is the reverse of what an earlier version of this section
claimed, and the correction is recorded rather than quietly swapped:

1. **No repo code touches it.** `grep` across `app/`, `geap/` and `tests/` for
   `opentelemetry`, `otel` or `tracer` returns nothing; `Span` matches only
   HTML `<span>` markup and a `transitSpan` helper in
   `app/static/dashboard.html`, neither of which is tracing. There is no
   exporter, no provider, no manual span.
2. **Google's own runtime logs say the instrumentation packages are absent.**
   From `resource.type="aiplatform.googleapis.com/ReasoningEngine"`, repeatedly:
   > `WARNING: telemetry enabled but proceeding without gRPC instrumentation, because opentelemetry-instrumentation-grpc has not been installed`

   and the same for `-httpx` and `-google-genai`.
3. **Cloud Trace is *not* empty.** This section previously said a
   `cloudtrace.googleapis.com` query returned **0 traces**. That was wrong and is
   falsifiable in one command. Over `2026-08-25` → `2026-08-29` the project holds
   **2 traces and 23 spans**, all actor-side, emitted by Agent Runtime — none of
   them from the governed-egress engine `2414533581910048768`, which runs no
   actor:

   ```
   7  call_llm                        2  invoke_workflow window_agent
   7  generate_content gemini-3.5-flash   2  invoke_agent window_agent
   1  execute_tool claim_work         1  execute_tool read_facts
   1  execute_tool report_constraint  1  execute_tool propose_plan
   1  execute_tool read_trace
   ```

   They carry OpenTelemetry / GenAI semantic-convention attributes
   (`gen_ai.agent.description`, `gcp.vertex.agent.*`, `cloud.*`).

The tag is still dropped, and now for a reason that survives checking. Harbor
wrote no instrumentation, and did not even turn telemetry on: the flag those
spans come from, `GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=true`, is set by
Agent Engine on 14 of the 15 engines and appears nowhere in
`geap/d1_deploy_runtime.py`. The spans are a managed-platform output this project
*reads*, not a library it *built with*.

They are worth knowing about anyway, because they are Google-side corroboration
of two claims the submission does make: the model executing inside the managed
Runtime is `gemini-3.5-flash`, and the tool surface offered to it is exactly the
five bounded tools — `claim_work`, `read_facts`, `report_constraint`,
`propose_plan`, `read_trace`, each appearing as its own `execute_tool` span.

**`Artifact Registry` — reinstated 2026-08-30.** The analysis below was true
when written: nothing pulled the image. It is preserved because the state it
describes has since changed — the Cloud Run service deployed on 2026-08-30
pulls `…/harbor/harbor-storm:portal-5bf0794` from this registry, so the tag
is now legitimately exercised (count it as tag 21).

**`Artifact Registry` — DROPPED. Real, but not in the submitted path.**
*(historical, superseded above)*

This one is subtler than "never deployed". The repository `harbor` **does** exist
and **does** hold an image:
`us-central1-docker.pkg.dev/harbor-storm-fleet/harbor/harbor-storm`, pushed
2026-08-27T06:09:52. So a container was genuinely built and stored.

It is still dropped, because it belongs to an abandoned branch of the work:

- `deploy/deploy.sh` pushes that image and then runs `gcloud run deploy`.
  **`gcloud run services list` returns zero services** — the Cloud Run deploy
  never happened, so nothing ever pulled that image.
- The submitted path is Agent Runtime, which does not use Artifact Registry at
  all. It stages through a bucket:
  `geap/d1_deploy_runtime.py:19` → `AGENT_STAGING_BUCKET` /
  `gs://harbor-storm-fleet-agent-staging`, passed as `staging_bucket`.

`run.googleapis.com` and `artifactregistry.googleapis.com` are still *enabled* on
the project. Enabled is not exercised.

### Not listed, deliberately

- **Conceptual tags** — *Zero Trust*, *Multi-Agent Systems*, *Enterprise AI*.
  These describe the idea, not the build. They belong in the story text.
- **Cloud Pub/Sub** — `google-cloud-pubsub` is in `requirements.txt` and
  `deploy/pubsub.sh` exists, but the managed service belongs to the same
  undeployed Cloud Run path. Same rule as Artifact Registry. The *push
  endpoint* (`POST /pubsub/push`) is exercised — locally, with curl-delivered
  envelopes, including messageId deduplication (README §3) — but no real
  Pub/Sub subscription delivers to it, so the tag stays off.

---

## The one-line honest limitation

Put this on the form, and say it out loud in the video. It costs nothing and it
is the sentence that makes the rest of the claims credible:

> Managed actor execution and governed-egress proof currently use separate Agent
> Runtime instances; each control is independently demonstrated.

Do not let it drift into something softer. The actor runs on engine
`3244216260136796160`, which is not Gateway-bound; governed egress was proved on
engine `2414533581910048768`, which runs no actor. The combined actor-plus-Gateway
path is **not demonstrated end-to-end on one engine**. Collapsing the two onto one
engine would require registering Vertex AI and Firestore as egress destinations,
which was not done.

**A second framing that must not drift.** The `failOpen: false` beat is a
**configuration property, not a demonstrated behaviour.** No IAP outage was
induced. State that whenever it appears, or a judge will reasonably assume an
outage was tested.

---

## Claims-to-evidence index

Sourced from the claim map in [docs/ARCHITECTURE.md](ARCHITECTURE.md) — same 17
claims, no new ones. This is the table to have open while answering questions.

**[A]** = engine `3244216260136796160`, the actor proof, **not** gateway-bound.
**[B]** = engine `2414533581910048768`, the governed-egress proof, which runs no
actor. Two engines, two proofs — no row claims one engine did both, and the
combined actor-plus-Gateway path is **not demonstrated end-to-end on one engine**.

In rows 6–8, `authz=ALLOWED` / `authz=DENIED` is shorthand for
`jsonPayload.authzPolicyInfo.result` on the gateway log record. Those records
name the gateway, not the calling engine: the attribution of those requests to
**[B]** comes from the controlled invocation and its time window, not from the
log itself.

| # | claim | artifact that proves it |
|---|---|---|
| 1 | Agent Identity issued by Google, bound to one engine | `geap/d0_readback.json` — `identityType: AGENT_IDENTITY`, no `serviceAccount` key, engine-bound `effectiveIdentity` |
| 2 **[A]** | A real Gemini executes inside the managed Runtime | `geap/d1_shift_accept.json` — `model_versions_reported: ["gemini-3.5-flash"]`, server-reported |
| 3 **[A]** | The tool surface is bounded — exactly five | `geap/d1_shift_accept.json` — `tools_offered_to_model`, read from the outgoing `LlmRequest` |
| 4 | No verify / commit / revision-advance / peer-transfer authority in the model-facing surface | `tests/test_agents.py`; `app/agents/execution.py::assert_no_authority_tools` audits the outgoing request |
| 5 **[B]** | The governed-egress engine's egress traverses the Gateway | Scoped to engine `2414533581910048768`, **not** the actor engine. `geap/d1_deployed_gw5.json` names `agentGateways/harbor-egress-gw`, and every record in `geap/gw_logs_rotated.json` arrives through it. The committed control-plane readback showing `agentGatewayConfig` is for the earlier gateway-bound engine `1047585541886836736` (`geap/d1_gw_readback.json`); for `2414533581910048768` it is reproducible live. **The actor engine `3244216260136796160` is not gateway-bound.** |
| 6 **[B]** | Weather **allowed** | `geap/d1_egress_rotated.json` (200, real forecast, `server: ESF`) + `geap/gw_logs_rotated.json` (`authz=ALLOWED`) |
| 7 **[B]** | Cargo **denied** | same pair — 403, `x-goog-iap-generated-response: true`, `authz=DENIED`; `geap/iap_endpoint_policies.json` shows the cargo endpoint has **no bindings at all** |
| 8 **[B]** | Unregistered **denied** | same pair — 403 with `x-goog-iap-generated-response: true`, body *"Egress request is not authorized. The endpoint is either incorrect or unregistered in the Agent Registry. Only registered endpoints are allowed."*; the matching gateway record carries an empty `agentGatewayInfo` — no `agentRegistryResource` attribution |
| 9 | The gateway is the only variable | `geap/d1_egress_nogw2.json` — same probe on the **non**-gateway control engine `8409845032730755072` (neither A nor B) reaches both denied destinations. **Show rows 2–3 only**; row 1 is a 401 for an unrelated auth reason |
| 10 | IAM names the agent principal, and that is what the runtime presents | `geap/iam_project_agent_principals.json` — both granted roles bound to per-engine `principal://` members. Read the artifact's own scope note before quoting it: it is **filtered to principals under the agent-identity trust domain**, so "and nothing else" is a claim about that filter, not about the whole project policy. Be exact on camera: within the filter, `roles/aiplatform.user` has those seven and nothing else, and `roles/datastore.user` the same seven; the unfiltered read (`gcloud projects get-iam-policy harbor-storm-fleet`) additionally shows `roles/datastore.user` carrying a pre-existing `…-compute@developer…` service account Harbor did not grant and does not authenticate as — so Firestore access is not exclusively Agent-Identity-bound. Neither Harbor-granted role carries a `principalSet` or a project-wide role; the file's single `principalSet://` sits on Google's default `roles/aiplatform.agentDefaultAccess`, which I did not create |
| 11 | Identity enforcement survives token sharing | `geap/firestore_iam_enforcement_legs.json` — the **401 → 403 → 403** enforcement legs; **the 403 is the proof**. The success that follows is a different artifact, `geap/d1_shift_accept.json`; the legs file is a `severity=ERROR` query and holds no success leg |
| 12 **[A]** | The verifier accepts and commits | `geap/d1_shift_accept.json` — `PLAN_PROPOSED window-agent` → `PLAN_VERIFIED verifier` → `PLAN_COMMITTED verifier`; `authoritative_state.committed_plan_id: harbor-agent-plan-1` |
| 13 **[A]** | The verifier refuses a stale proposal | `geap/d1_shift_control.json` — `verifier_decision: "rejected"`; `PLAN_REJECTED` carries `stale: plan bound to revision 0, world is at revision 1`; `authoritative_state.committed_plan_id: null` |
| 14 | The verifier refuses physical infeasibility | `app.demo harborwindow --pretty` — `PLAN_REJECTED … harbor wind 42 kph over limit at hour 12` |
| 15 | Firestore is authoritative | independent readback from a different machine — `committed_plan_id` and the full trace |
| 16 **[B]** | `failOpen: false` | `geap/failclosed/authz_extension_readback.json` — `failOpen` absent from the readback (proto3 omits false), where the create-time record `geap/d1_iap_extension.json` still shows `failOpen: true`; the update legs are in `geap/failclosed/authz_extension_audit_provenance.json`. **Configuration property, not a demonstrated behaviour** — no IAP outage was induced. |
| 17 | The substrate is not scenario-specific | `app.gate` — StormSlot and HarborWindow both 5/5 mechanical, on the same six hard gates: five mechanically checked and one manual demo-legibility gate |

---

## Long-horizon operation (added 2026-08-30)

Not one of the 17 claims above — a separate, additive claim with its own
evidence: **commitment does not end scrutiny, across wall-clock time.** The
revocation semantics (`COMMIT_REVOKED`, revision fencing, event dedup) are in
the frozen core; `app/sentinel.py` carries them onto a real clock by polling
the forecast and routing a changed observation into the frozen `disrupt()`
path, and `POST /pubsub/push` is the same path pushed instead of polled —
at-least-once delivery handled by messageId deduplication, duplicate acks,
and 400-to-dead-letter for poison messages. Evidence:
`tests/test_sentinel.py` (13 tests: unchanged forecast is a pure no-op, a
changed one revokes and replans through the verifier, an observation applies
at most once across restarts, the sentinel holds no authority), plus the
runnable sentinel and curl sequences in README §3. The sentinel is additive —
no frozen file moved — and is declared in the README's provenance section at
its engineering SHA.

---

## Fleet track mapping — the seven recommended GEAP components

The Fortified Enterprise Fleet resources page recommends seven Gemini
Enterprise Agent Platform components by name. Five are exercised in the
submitted path; two are not used, and each of those has a principled answer,
not an absence. Structure the description around this list — it is the
checklist a Fleet judge will hold the submission against.

**Exercised — 5 of 7:**

| component | what makes it true |
|---|---|
| Agent Registry | two endpoints returned live by `agentregistry.googleapis.com`. The sharper claim: **registration is discovery, not authorization** — `harbor-cargo-ops` is registered and still denied, because per-endpoint IAP IAM authorizes, not the catalog |
| Agent Runtime | a real Gemini 3.5 Flash **executes inside it** against the five-tool bounded surface — exercised, not merely provisioned. The track defines Runtime as "long-running async execution": the managed half is the actor executing in Runtime, the wall-clock half is the sentinel's revocation loop (README §3). The two halves are demonstrated separately and are honestly not yet combined in the cloud |
| Agent Identity | `identityType: AGENT_IDENTITY`, no `serviceAccount` key, engine-bound `effectiveIdentity`; the 401→403→403 token-sharing enforcement legs |
| Agent Gateway | `harbor-egress-gw`; ALLOWED and DENIED decisions captured from Google's own gateway logs |
| Agent Observability | the track's gloss is "audit logs + reasoning-chain traces", and both exist from **two independent record-keepers**: Harbor's own attributed event trace (basis revisions, named refusal reasons, machine-checked integrity) and Google's — Cloud Trace spans naming `gemini-3.5-flash` and each of the five tools as its own `execute_tool` span, plus gateway decisions in Cloud Logging |

**Not used — 2 of 7, with the stance stated:**

- **Memory Bank.** Harbor's invariant is *session memory is not authoritative;
  the store and the event log are.* Cross-session context that cannot be
  re-verified is exactly what the membrane exists to distrust. Persistent
  state is Firestore, one contract on both backends (`334 passed`, none
  skipped).
- **Model Armor.** Its purpose is guardrails against prompt injection and
  tool poisoning. Harbor contains that threat class structurally: the
  model-facing surface carries no verify / commit / revision-advance tool
  (`assert_no_authority_tools` audits the outgoing request;
  `tests/test_agents.py`), and inbound messages are evidence, not
  instruction — a poisoned prompt can propose and a poisoned message can
  inform, but neither can commit. **Do not claim this replaces Model Armor.**
  Claim the threat it filters is architecturally contained, and point at the
  test.

The one-line component summary for the form: *five of the seven recommended
GEAP components are exercised in the submitted path — Runtime by actual actor
execution, not provisioning — and the two not used are answered by
architecture: authoritative state instead of session memory, and structural
authority containment instead of input filtering.*

---

## Reproduce commands a judge will run

All seven need **no credentials and no Google Cloud account**. Measured on
2026-08-28 on the submitted tree; rows 6–7 added 2026-08-30.

```bash
git clone https://github.com/4j2txbjjdd-cmd/harbor-storm.git
cd harbor-storm
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

| # | command | expected output |
|---|---|---|
| 1 | `.venv/bin/python -m pytest tests -q` | `287 passed, 47 skipped` (~1.2s) |
| 2 | `.venv/bin/python -m pytest tests/test_live_gate.py -q` | `11 passed` |
| 3 | `.venv/bin/python -m app.gate` | `stormslot — 5/5 mechanical gates, SURVIVES` and `harborwindow — 5/5 mechanical gates, SURVIVES` |
| 4 | `.venv/bin/python -m app.demo harborwindow --pretty` | 16-line trace ending `COMMITTED -> harbor-plan-2` |
| 5 | `.venv/bin/python -m app.demo stormslot --pretty` | 16-line trace ending `COMMITTED -> stormslot-plan-2` |
| 6 | `.venv/bin/python -m app.sentinel harborwindow --ticks 3 --interval 0 --disrupt-at-tick 2` | tick 1 `unchanged`; tick 2 `CHANGED -> applied` with `COMMIT_REVOKED` then `PLAN_COMMITTED -> harbor-plan-3`; tick 3 `unchanged` |
| 7 | `.venv/bin/python -m app.relief_demo --disrupt --pretty` | 34-event trace: mission refused at first light on a named hazard, committed at 9:00, revoked by the barrier-lake alert, re-committed at 12:00 — `COMMITTED -> relief-plan-3` |

**Command 2 never touches the network, and that is the point of the test.** The
live-gate tests run against controlled providers so that a green suite proves the
gate's lane discipline and nothing about Google Weather. One of them makes
`GoogleWeatherProvider` unconstructable and then runs the seeded gate for both
scenarios and both disrupted demos through it, so the deterministic lane fails
loudly if it ever acquires a live provider — including indirectly through config.
The live weather proof is a separate, credentialed lane.

One further check needs a JDK and `firebase-tools`, but still no Google Cloud
account — it runs the same suite against the Firestore emulator:

```bash
PATH="/opt/homebrew/opt/openjdk/bin:$PATH" firebase emulators:exec \
  --only firestore --project harbor-storm-local \
  'FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 GOOGLE_CLOUD_PROJECT=harbor-storm-local \
   .venv/bin/python -m pytest tests -q'
```

| # | command | measured |
|---|---|---|
| 8 | the emulator run above | `334 passed`, none skipped, 14.2s (measured 2026-08-30 with the sentinel and ReliefRun suites; 2026-08-28 pre-sentinel: `305 passed`) |

**The 47 skips are one cause, not 47 problems.** Every one is
`tests/test_store_contract.py: set FIRESTORE_EMULATOR_HOST to run` — the same
store contract run against the Firestore backend, which skips without an
emulator. Under the emulator all 334 pass with nothing skipped, and that was
actually run on the submitted tree rather than inferred from 287 + 47. Say this
before a judge asks.

**Frozen-core integrity**, the one that answers "did you rewrite the core to make
the cloud story work?"

This repository publishes the frozen submitted tree without its engineering
history, so the answer is stated as fact rather than as a diff to run here.
The submitted code and evidence are content-identical to engineering SHA
`687eebfd26f64d87f3c8db49756f838dc90bc02a` (the judge-facing documents were
finalized after that SHA, and three purely additive layers were added later at
their own engineering SHAs: the sentinel — `app/sentinel.py`,
`tests/test_sentinel.py` — at `4296831`, ReliefRun —
`app/scenarios/reliefrun.py`, `app/relief_demo.py`, `tests/test_reliefrun.py`
— at `893f759`, and the portal — `app/portal.py`, `app/static/relief.html`,
`tests/test_portal.py` — at `9803336`). Comparing `app` and `tests` (modified,
deleted or renamed files only — the added files are declared above, not hidden
by this filter):

| against | files changed under `app` and `tests` |
|---|---|
| `submission-freeze-2`, the freeze that was submitted | **none** — nothing has moved since |
| `core-freeze-1` (`cf91551`), the pre-GEAP baseline | **5** — `app/api.py`, `app/gate.py`, `app/config.py`, `app/providers/routes.py`, `tests/test_api.py` |

Those five are the deliberate presentation-only delta between the two freezes,
documented in [docs/CORE_FREEZE.md](CORE_FREEZE.md). Nothing in the verified core
was rewritten to make the managed work succeed; the GEAP work is purely additive.

**Why the dated numbers above can be trusted at all.** Evidence collected from a
moving tree is void. Before any run whose numbers are quoted here, the tree had
to be a known committed SHA with no uncommitted or untracked changes — an
untracked `conftest.py` or `sitecustomize.py` can change what a run does while
the committed SHA still looks certified. Every quoted number is stamped with the
HEAD SHA and capture time. The gate fails closed: if a precondition does not
hold, evidence collection stops rather than proceeding with a caveat. The
reasoning is set out in [`../EVIDENCE.md`](../EVIDENCE.md).

---

## Open items only a person can close

- Record and edit the video; upload it. Shot list and lanes are in
  [docs/DEMO_CAPTURE_MAP.md](DEMO_CAPTURE_MAP.md).
- Submit the Devpost form itself.
- **Two freeze tags, and the difference between them is stated.** Both are
  engineering-repository provenance, not objects in this publication (which
  omits the engineering history): `core-freeze-1` (`cf91551`) is the historical operational freeze
  and has not moved, and `submission-freeze-2` is what was submitted — the code
  and evidence published here, engineering SHA `687eebfd`. The delta between them is five
  files and is presentation only: `app.gate`'s printed footer and one docstring
  line, `GET /scenarios`'s `selected` value and note, the routes seam's two
  docstrings and its `NotImplementedError` message, and the test that pinned the
  old `selected is None` contract. Every one of them previously announced that
  the scenario choice had not been made, or cited a document that has since been
  removed; they now name HarborWindow as the flagship. Control flow, call structure and
  the machine-readable surface are unchanged — `app.gate --json` is byte-identical
  and `POST /runs` still resolves the scenario from the request body. See
  [docs/CORE_FREEZE.md](CORE_FREEZE.md).

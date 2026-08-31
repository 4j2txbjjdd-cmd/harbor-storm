# GEAP cloud inventory

Every live resource in `harbor-storm-fleet` that carries an architectural claim:
what it is for, which evidence it produced, and whether it can go.

This file exists because purpose is not stored in the control plane. IDs, flags,
IAM and gateway bindings are all re-derivable from Google, and were re-derived
for this document rather than copied. **Purpose**, **generation** and
**superseded-by** are not re-derivable from any API. Recording them here is what
lets the repository answer "why does this engine exist" and "which engine
produced this evidence file" from something it owns.

Fifteen Reasoning Engines are live. Eight are cited by an evidence artifact
carried in this package; seven are not. That asymmetry is the reason this file is
a table and not a paragraph, and it is countable from the provenance table below.
Five of the seven produced no committed artifact at all — `3291504056224186368`
and the four `harbor-grpc-*` D1.5 probes. The other two are the failed D1
iterations, whose deployment records belong to the engineering record and are not
carried here; where that is a row's only artifact, the row says so rather than
pointing at a file a reader cannot open.

Re-derived 2026-08-28, against engineering SHA
`b1e7d6bb72f3c71eead8aa5a59ef380e2efb0e3b`. Project `harbor-storm-fleet`
(number `801248256447`), region `us-central1` unless stated. Every value below is
stamped with that capture, because a re-derived number quoted without the state
it was read from is not a number anyone can check.

## How every column here was re-derived

```bash
# engines: identity, env flags, gateway binding
# WARNING: the raw LIST payload carries deployment environment variables, and an
# environment variable can hold a credential value in cleartext. Never cat this
# payload, screen-share it, or redirect it into the repo. Project the fields you
# need -- the projection below reads variable *names* only, never values:
curl -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/harbor-storm-fleet/locations/us-central1/reasoningEngines" \
  | python3 -c "import json,sys
for e in json.load(sys.stdin)['reasoningEngines']:
    ds = e.get('spec',{}).get('deploymentSpec',{})
    env = {v['name'] for v in ds.get('env',[])}
    print(e['name'].split('/')[-1], e.get('displayName'),
          'gw=' + ('yes' if ds.get('agentGatewayConfig') else 'no'),
          'key=' + ('yes' if 'GOOGLE_WEATHER_API_KEY' in env else 'no'))"
# NOTE: gateway binding lives at spec.deploymentSpec.agentGatewayConfig. This
# LIST response does carry it -- 8 of the 15 entries have it -- but the binding
# column was not taken from the list. Every row was confirmed by a per-engine
# GET on all fifteen ids, and the two agree. Read the field explicitly rather
# than inferring binding from an engine's name or its display name.

# project IAM (datastore.user / aiplatform.user per agent principal)
gcloud projects get-iam-policy harbor-storm-fleet --format=json

# iap.egressor on a registry endpoint — NOT an agentregistry method.
# agentregistry has no getIamPolicy at all; the policy lives on IAP's
# iap_web/agentRegistry resource path, which is discoverable from the
# SetIamPolicy audit entry, not from the API's discovery document.
curl -X POST -d '{}' -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://iap.googleapis.com/v1/projects/801248256447/locations/us-central1/iap_web/agentRegistry/endpoints/<ENDPOINT>:getIamPolicy"

gcloud services api-keys list --project=harbor-storm-fleet   # metadata only
curl -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://networkservices.googleapis.com/v1beta1/projects/harbor-storm-fleet/locations/us-central1/agentGateways"
curl -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://networksecurity.googleapis.com/v1beta1/projects/harbor-storm-fleet/locations/us-central1/authzPolicies/harbor-iap-request-authz"
curl -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://agentregistry.googleapis.com/v1/projects/harbor-storm-fleet/locations/us-central1/services"
```

### Reading the token-sharing flag correctly

`GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES` is a *prevention*
flag, and `d1_deploy_runtime.py --no-token-sharing` **removes the variable
entirely** rather than setting it true. So:

| env readback | meaning |
|---|---|
| `…PREVENT_AGENT_TOKEN_SHARING… = "False"` present | prevention off → **token sharing ON** |
| variable absent | platform default → **token sharing OFF** |

Absence is the off state. Reading a missing variable as "not configured"
inverts the flag, which is how two engines below came to be documented backwards.

Sharing **ON** means the agent's token is *permitted* to reach GCP client
libraries. It does not switch IAM off: the identity the runtime presents is still
evaluated, which is what `geap/firestore_iam_enforcement_legs.json` records — the
403 legs are refusals of a *named* principal, and an unenforced identity does not
produce those.

### Agent Identity principal

Derivable from the engine id — no per-engine lookup needed:

```
principal://agents.global.org-648972411952.system.id.goog/resources/aiplatform/projects/801248256447/locations/us-central1/reasoningEngines/<ENGINE_ID>
```

`spec.effectiveIdentity` returns that same string **without** the `principal://`
scheme. The IAM member form requires the scheme; the readback omits it. They are
the same identity.

## Reasoning Engines — configuration (re-derived from the control plane)

`GW` = bound to `harbor-egress-gw`. `Share` = token sharing per the table above.
`Key` = `GOOGLE_WEATHER_API_KEY` present (the two engines that carry it do *not* hold the same value — see below). `Cert` = `GOOGLE_API_USE_CLIENT_CERTIFICATE`.

| engine id | display name | created (UTC) | GW | Share | Key | Cert | IAM held |
|---|---|---|---|---|---|---|---|
| `1562121799313915904` | harbor-d0-identity-probe | 08-27 17:41:56 | no | — | no | no | **none** |
| `3291504056224186368` | harbor-window-agent-nogw | 08-27 17:52:24 | no | OFF | no | no | **none** |
| `2753323900753412096` | harbor-window-agent-nogw | 08-27 17:55:20 | no | OFF | no | no | datastore.user, aiplatform.user |
| `2248920742487916544` | harbor-window-agent-v2 | 08-27 18:00:39 | no | ON | no | no | datastore.user, aiplatform.user |
| `1830085977142460416` | harbor-window-agent-v3 | 08-27 18:05:55 | no | ON | no | no | datastore.user, aiplatform.user |
| `3244216260136796160` | harbor-window-agent-v4 | 08-27 18:15:08 | no | ON | no | no | datastore.user, aiplatform.user |
| `1047585541886836736` | harbor-window-agent-gw | 08-27 18:36:19 | **yes** | ON | no | no | datastore.user, aiplatform.user, **iap.egressor** (weather) |
| `1492316005089673216` | harbor-window-agent-gw2 | 08-27 18:51:32 | **yes** | ON | no | no | datastore.user, aiplatform.user, **iap.egressor** (weather) |
| `8409845032730755072` | harbor-window-agent-nogw2 | 08-27 18:56:37 | no | ON | no | no | datastore.user, aiplatform.user |
| `4557684054584983552` | harbor-egress-probe-v4 | 08-28 05:18:09 | **yes** | **ON** | yes | yes | **iap.egressor** (weather) only |
| `2414533581910048768` | harbor-egress-probe-rotated | 08-28 08:19:40 | **yes** | ON | yes | yes | **iap.egressor** (weather) only |
| `6042183081756983296` | harbor-grpc-edge-probe | 08-28 09:42:23 | **yes** | OFF | no | yes | **none** |
| `1019543597332037632` | harbor-grpc-edge-probe-sharing | 08-28 09:56:35 | **yes** | ON | no | yes | **none** |
| `1140014887364198400` | harbor-grpc-authority-probe | 08-28 10:07:36 | **yes** | ON | no | yes | **none** |
| `2292936391971045376` | harbor-grpc-auth-probe | 08-28 10:15:41 | **yes** | ON | no | yes | **none** |

**Eight of the fifteen are bound to `harbor-egress-gw`; seven are not.** The
count is stated so it can be checked against the column rather than trusted.

All fifteen are `identityType: AGENT_IDENTITY` with **no** service account. Every
engine that carries `iap.egressor` carries it on the weather endpoint only.

`FIRESTORE_DATABASE=harbor` and `STATE_BACKEND=firestore` are set on all but
`1562121799313915904` (no agent code) and `3291504056224186368` (deployed before
the variable was added — the omission is what dates it).

## Reasoning Engines — provenance (not re-derivable; recorded here)

| engine id | purpose | generation / what it fixed | evidence it produced | superseded by | safe to delete? |
|---|---|---|---|---|---|
| `1562121799313915904` | D0 identity probe. No agent code, no gateway — proves Agent Identity is issued without a service account. | D0, first managed resource. | `geap/d0_readback.json` — the control-plane readback, which is the claim; the create response is not | — | **No.** Sole D0 anchor and the only engine with an empty spec, which is the proof. |
| `3291504056224186368` | First D1 deploy attempt, no gateway. | Pre-`FIRESTORE_DATABASE`. Its record was overwritten by the retry three minutes later, which reused `--out geap/d1_deployed_nogw.json`. | **none — never committed** | `2753323900753412096` | **Yes.** No IAM, no evidence, no citation. It was the only surviving trace that a first attempt happened; this row is now that trace. |
| `2753323900753412096` | D1 attempt 1 (recorded). | Failed 401: the agent token is not handed to GCP client libraries by default, so Firestore authenticated as nobody. | `geap/d1_deployed_nogw.json` | `2248920742487916544` | **No.** Its IAM grants with sharing OFF are the evidence that the 401 was a token-sharing failure and not a missing grant. |
| `2248920742487916544` | D1 attempt 2. | Token sharing added. Failed: `asyncio.run()` called on a live event loop. | deployment record from the engineering run; **not carried in this package** | `1830085977142460416` | **No.** Named in the resource table in `docs/GEAP_D0_D1.md` as part of the D1 iteration record. |
| `1830085977142460416` | D1 attempt 3. | Event-loop fix (`_run_actor_on_own_loop`). Failed 404: `gemini-3.5-flash` is not a publisher model in `us-central1`. | deployment record from the engineering run; **not carried in this package** | `3244216260136796160` | **No.** Named in the resource table in `docs/GEAP_D0_D1.md` as part of the D1 iteration record. |
| `3244216260136796160` | **D1 actor proof.** Harbor's bounded actor running on the managed control plane. | `model_location=global`. First engine to complete a shift and refuse a stale candidate. | `geap/d1_deployed_v4.json`, `geap/d1_shift_accept.json`, `geap/d1_shift_control.json` | — | **No. Primary D1 evidence.** |
| `1047585541886836736` | First gateway-bound engine. | First `iap.egressor` grant on the weather endpoint. | `geap/d1_deployed_gw.json`, `geap/d1_gw_readback.json` — the readback is the only artifact in this package that carries `spec.deploymentSpec.agentGatewayConfig` from a live engine | `1492316005089673216` | **No.** Holds a live `iap.egressor` binding that is part of the endpoint policy's history. |
| `1492316005089673216` | Gateway-bound engine under test. | Full IAM (datastore + aiplatform) alongside the gateway. | `geap/d1_deployed_gw2.json`, `geap/gw_logs_authz.json` | `4557684054584983552` for the governed proof | **No.** Its deploy record and the gateway log for its authz window are both carried here. |
| `8409845032730755072` | **Non-gateway routing control.** The counterfactual: same identity, same IAM, no gateway. | Shows the allow/deny split is the gateway's doing, not the destination's. | `geap/d1_deployed_nogw2.json`, `geap/d1_egress_nogw2.json` | — | **No. Counterfactual — the proof is worthless without it.** |
| `4557684054584983552` | **Original governed allow/deny proof.** | Gateway + weather key + `iap.egressor` on weather only. | `geap/d1_deployed_gw4.json`, `geap/d1_egress_final.json`, `geap/gw_logs_final.json` | Not superseded — `2414533581910048768` re-proves it on a new credential; both stand. | **No.** |
| `2414533581910048768` | **Post-rotation governed proof, and the engine the fail-closed triad ran on.** | Same geometry as `4557684054584983552`, rotated Weather key. | `geap/d1_deployed_gw5.json`, `geap/d1_egress_rotated.json`, `geap/gw_logs_rotated.json`, `geap/failclosed/http_triad_client.json`, `geap/failclosed/http_triad_gateway.json` | — | **No. Source of the submitted governed-egress and fail-closed evidence.** |
| `6042183081756983296` | D1.5 gRPC control: sharing OFF, no IAM. | First of four D1.5 probes. Invoked once, 09:49:52Z. | **none** — a `geap/d15_grpc_control.json` was intended and never written | — | **Yes, with one caveat.** No IAM, no committed evidence. Its runtime logs survive deletion; its deployment readback does not — and this row is now that readback. |
| `1019543597332037632` | D1.5 token-sharing isolation: sharing ON, no IAM. | Isolates sharing from IAM. Invoked once, 10:00:10Z. | **none** — intended `geap/d15_grpc_sharing.json`, never written | — | **Yes**, same caveat. |
| `1140014887364198400` | D1.5 authority isolation, A/B on a single engine. | Invoked once, 10:11:00Z. | **none** — intended `geap/d15_authority_arms.json`, never written | — | **Yes**, same caveat. |
| `2292936391971045376` | **Abandoned D1.5 probe.** Deployed and invoked once (10:19:29Z); D1.5 was suspended before its result was captured. | Last deploy in the project. | **none.** The result of its one invocation was never written down. | — | **Yes**, same caveat. Nothing depends on it. |

D1.5 is **suspended, not concluded.** The four `harbor-grpc-*` engines are the
only physical trace of it. None of their results were committed, and **gRPC is
not part of the demonstrated path** — nothing in this package rests on a D1.5
result, and the engines are listed here so the inventory is complete, not because
they prove anything.

## The two proof surfaces, and the gap between them

Two engines in the tables above carry the submitted managed proofs, and they are
**not the same engine**:

- **Engine A — `3244216260136796160`.** The actor proof: Harbor's bounded actor
  running on the managed control plane, the first engine to complete a shift and
  refuse a stale candidate. It carries **no** gateway binding — `GW` = no in the
  configuration table.
- **Engine B — `2414533581910048768`.** The governed-egress proof: gateway-bound,
  holding `iap.egressor` on the weather endpoint only. It runs **no** actor.

**At the time of this inventory this was not demonstrated end-to-end on one engine.** *(Superseded 2026-08-31: engine `6110651869841850368` converged the two halves — see `docs/GEAP_D0_D1.md`'s addendum. The paragraph below records the pre-convergence state accurately.)* Engine A is not
gateway-bound; Engine B does not run the actor. Converging them requires the
actor path's Google API dependencies to be reachable through the governed egress
configuration. That work was stopped deliberately and is not claimed. Any reading
of the tables that has one engine proving both halves is a misreading of the `GW`
column.

## Gateway-log attribution

`geap/gw_logs_*.json` and `geap/failclosed/http_triad_gateway.json` carry **no
engine id** — a gateway log record identifies the destination endpoint, not the
caller. Attribution above is by timestamp, cross-checked against Reasoning Engine
invocation logs, which do carry the engine id:

| evidence file | log window (UTC) | engine invoked in that window |
|---|---|---|
| `geap/gw_logs_authz.json` | 08-28 05:13:44–05:13:45 | `1492316005089673216` |
| `geap/gw_logs_final.json` | 08-28 05:30:01 | `4557684054584983552` |
| `geap/gw_logs_rotated.json` | 08-28 08:23:24–08:32:25 | `2414533581910048768` |
| `geap/failclosed/http_triad_gateway.json` | 08-28 10:43:54 | `2414533581910048768` |

Reproduce with:

```bash
gcloud logging read 'resource.type="aiplatform.googleapis.com/ReasoningEngine"
  AND timestamp>="<START>" AND timestamp<="<END>"
  AND textPayload=~"POST /api/reasoning_engine"' \
  --project=harbor-storm-fleet --format='value(resource.labels.reasoning_engine_id)'
```

Only one engine appears in each window, so the attribution is unambiguous. It
would stop being unambiguous the moment two engines are exercised concurrently.

## Non-engine resources

| type | name / id | purpose | key properties | evidence | safe to delete? |
|---|---|---|---|---|---|
| AgentGateway | `harbor-egress-gw` | Governed egress path for every bound engine. | `googleManaged.governedAccessPath: AGENT_TO_ANYWHERE`; created 08-27 17:45:59Z, updated 08-27 22:52:04Z. The transport is Google-managed: the gateway terminates mTLS on a Google-owned service attachment and reaches IAP through a Google-managed service agent, neither of which Harbor created. | every `gw_logs_*` file | **No.** Eight live engines bind to it. |
| authzExtension | `harbor-iap-authz` | Hands gateway authorization decisions to IAP. | `service: iap.googleapis.com`, `timeout: 1s`, `metadata.iapPolicyVersion: V1`, **`failOpen` absent = false**; created 08-27 19:29:09Z, updated 08-28 09:44:13Z | `geap/d1_iap_extension.json` (pre-change, `failOpen: true`), `geap/failclosed/authz_extension_readback.json` (now) | **No.** The fail-closed claim is a property of this resource — and it is a *configuration* property, read back from the control plane; no outage was induced to observe the behaviour. |
| authzPolicy | `harbor-iap-request-authz` | Binds that extension to the gateway. | `action: CUSTOM`, `policyProfile: REQUEST_AUTHZ`, target `agentGateways/harbor-egress-gw`; created 08-27 20:25:57Z, updated 08-28 09:43:53Z | `geap/d1_iap_policy.json` | **No.** Without it the extension is not consulted. |
| Registry service | `harbor-weather` → endpoint `agentregistry-00000000-0000-0000-3e88-93525e6955a6` | The **allow** arm. `https://weather.googleapis.com`, `HTTP_JSON`. | Holds `roles/iap.egressor` for exactly four principals: `1047585541886836736`, `1492316005089673216`, `2414533581910048768`, `4557684054584983552` | attribution field in every gateway ALLOW record | **No.** |
| Registry service | `harbor-cargo-ops` → endpoint `agentregistry-00000000-0000-0000-dffe-b2901c86a27a` | The **deny control** — registered, deliberately unbound. `https://cloudresourcemanager.googleapis.com`. | IAM policy is **empty** (`etag: ACAB`, no bindings). Verified, not assumed. | attribution field in the registered-but-denied record | **No. Deliberately unbound — deleting it deletes the deny control.** |
| API key | Weather API key for the allow arm | Authenticates the allow arm to the weather destination. | Restricted by `apiTargets` to `weather.googleapis.com` **only** — the key is scoped to one destination, so possessing it buys nothing else in the project. Rotated on 2026-08-28. **No key value was read at any point of the rotation check — metadata and a digest comparison only — and none is recorded anywhere in this repository.** Key identifiers are deliberately not published here. | the `Key` column in the configuration table; the rotation argument below | **No.** The allow arm cannot authenticate without it. |
| Audit config | project-level | Surface IAP decisions. | `DATA_READ` + `ADMIN_READ` for `iap.googleapis.com` **only**. No other service's logging changed. | `geap/d1_iap_decisions.json` | **No.** |

The two registry rows are the whole point of the deny control: **registration is
not an allowlist**. `harbor-cargo-ops` is registered and still denied, because
what governs egress is the per-endpoint IAP IAM policy — bindings on the weather
endpoint, no bindings on the cargo endpoint — and not the fact of being in the
registry at all.

### The rotation, and how it was checked without reading a key

The two governed engines both set `GOOGLE_WEATHER_API_KEY`, and it is tempting to
assume they hold the same value. They do not. Establishing that took no key value
at all — the argument uses metadata and one equality test:

1. Exactly two weather keys have ever existed in this project: one created
   08-27 13:25:07Z, and its replacement created 08-28 08:16:50Z. Key **metadata**
   shows both; `gcloud services api-keys list` returns no key material.
2. The two engines' `GOOGLE_WEATHER_API_KEY` values are **not equal**. Compared
   by digest, never printed.
3. The earlier governed engine was last written 08-28 05:21:07Z — about three
   hours before the replacement key existed — so it cannot be holding the
   replacement.
4. The post-rotation engine therefore holds the replacement, by (2).

The ordering is the part worth keeping. The replacement credential was proven on
a live allow arm — a 200 with a real forecast, through the Gateway, logged
`ALLOWED` at the weather registry endpoint — **before** the previous key was
removed. Rotation was a demonstrated cutover, not a swap and a hope. That is also
what makes the governed path checkable at all: the allow arm's authentication is
a destination credential, and the Gateway's allow/deny decision is not, so
changing the credential does not change what the governed-egress proof shows.

One consequence for reproducibility, stated so nobody is surprised. The original
governed proof was captured before the rotation, and its committed evidence
stands as a record of what happened at that timestamp — re-running that probe
today is not expected to reproduce a 200. `2414533581910048768` is the live allow
arm; it is the engine the submitted governed-egress evidence comes from.

### Staged deploy packages cannot attribute a package to an engine

Every deploy writes the same three object paths into the staging bucket, so what
is staged is the **last** deploy only — here, the abandoned D1.5 probe
`2292936391971045376`, whose staged objects were written in the seconds before
its 10:15:41 create time. Staged package contents say nothing about the other
fourteen engines, and never did. No claim in this package rests on them.

### Project-wide IAM, stated so it can be checked

In the project IAM policy read on 2026-08-28, `roles/iap.egressor` appears
**nowhere** at project level — it exists only on the weather registry endpoint.
That policy contains exactly one `principalSet` grant,
`roles/aiplatform.agentDefaultAccess` on
`principalSet://…/attribute.platformContainer/aiplatform/projects/801248256447`,
which is a platform default and was not added by GEAP work. `roles/datastore.user`
carries the per-engine agent principals **plus** one pre-existing compute service
account that predates this work — so Firestore access is not exclusively
Agent-Identity-bound at the IAM level, and this document does not claim it is.
No Owner or Editor appears on any agent principal in that policy.

## Why `geap/ENGINE_*` is excluded

`.gitignore` excludes `geap/ENGINE_*` and `geap/PRINCIPAL_*`. They are label→id
pointers a deploy step wrote for its own next step — scratch, not artifacts —
and they name only six of the fifteen engines, in the deploy tooling's shorthand
rather than the project's. Committing them would add a second, narrower naming
scheme for resources this document already identifies by id. The provenance table
above is the mapping, and it is the one the repository owns.

## What the control plane did not agree with

Re-derivation contradicted the earlier record in four places. The control plane
wins on configuration; only purpose and supersession come from the notes. A
fifth entry records what independent verification then found wrong in this
document itself.

1. **Fifteen engines, not thirteen.** `2292936391971045376`
   (`harbor-grpc-auth-probe`) was expected and is recorded above.
   `3291504056224186368` was not expected at all — a second
   `harbor-window-agent-nogw`, created 17:52:24Z, three minutes before the one
   the repo records. The retry reused the same `--out` path and overwrote its
   deployment record. It holds no binding in the project IAM policy, produced no
   evidence file, and is named in no committed document other than this one.
   It does **not** change which engine produced which evidence:
   `geap/d1_deployed_nogw.json` names `2753323900753412096`, and that is the
   engine whose IAM supports the 401 diagnosis.

2. **`4557684054584983552` runs with token sharing ON, not off.** An earlier note
   recorded it as off — "the governed egress on `4557684054584983552`
   (gateway-bound, token sharing off)". The live readback carries
   `GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES = "False"`, which is
   sharing **on** — `--no-token-sharing` was not passed. Its published
   allow/deny result is unaffected: the governed proof turns on the gateway
   binding and the endpoint IAM grant, and the destination is authenticated by
   API key, not by the shared token. The false clause was **not** inert: its
   parenthetical set up a sharing-on / sharing-off contrast between the two
   engines, implying the actor / governed-egress split was partly a
   token-sharing split. It was not, and the real reason — the actor path's
   Google API dependencies must be reachable through the governed egress
   configuration — survives the correction untouched. `docs/GEAP_D0_D1.md` now
   states the readback value, so this package does not carry two current
   documents that disagree about one engine's configuration. The engine that
   genuinely runs with sharing off is `6042183081756983296`.

3. **Four gateway bindings the notes did not record.** All four `harbor-grpc-*`
   engines are bound to `harbor-egress-gw`. The notes described them by their
   sharing and IAM arms only, and an engine named `harbor-grpc-edge-probe` does
   not announce a gateway binding — that is what made it easy to miss. The
   binding is a field, not an inference: read
   `spec.deploymentSpec.agentGatewayConfig` on each engine and count.

4. **No `d15_*` evidence file was ever written — a declared absence, not a broken
   link.** `geap/d15_gw_logs.json`, `d15_grpc_control.json`,
   `d15_grpc_sharing.json` and `d15_authority_arms.json` are filenames that were
   reserved for D1.5 and never filled: no such file exists in this repository's
   `geap/` tree, and none exists anywhere in the engineering record either. The
   four D1.5 engines each ran exactly once and nothing was written down. So the
   D1.5 observations — a `run_shift` measurement and an ALLOWED
   Firestore-over-gRPC record — are **unbacked by any committed artifact** and
   are named here only so the gap is visible. They are quarantined, not banked:
   **gRPC is not part of the demonstrated path**, and no other document in this
   package cites a D1.5 result.

   That quarantine also withdraws an attribution. An earlier note put those two
   observations on `1492316005089673216`. That engine does not carry
   `GOOGLE_API_USE_CLIENT_CERTIFICATE`, which every one of the four D1.5 probes
   does, and no artifact carried in this package ties it to a gRPC transport — so
   the provenance row for it now claims only what its committed artifacts
   support: a gateway-bound engine with full IAM.

5. **Three errors independent verification found in this file.** They are
   recorded rather than quietly patched, because this document's whole claim is
   that it can be checked. A control that depends on someone being suspicious is
   not a control — so the errors are listed, and every column above is stated in
   a form that can be re-derived and disagreed with.

   - The API-key row said the rotated key was injected into **both** governed
     engines, and that the previous key was "gone". Both halves were wrong. The
     two engines carry *different* `GOOGLE_WEATHER_API_KEY` values, and the
     earlier engine was last written about three hours before the replacement key
     was created, so it cannot be holding it. Key metadata also showed the
     retirement of the previous key was less complete than the note claimed. The
     inventory now says only what live state supports, and says it without
     publishing key identifiers.
   - The header's cited/uncited split disagreed with the provenance table
     underneath it — the header said nine and six where the table gave ten and
     five. A summary that disagrees with the table it summarises is a defect, not
     a rounding. The header now counts artifacts actually carried in this
     package, which is the narrower set a reader can open, and the count is
     checkable against the table.
   - The re-derivation section said `agentGatewayConfig` is absent from the LIST
     response. It is not: the LIST response carries it on 8 of the 15 entries.
     The binding counts were right — they came from per-engine GETs — but the
     stated reason for needing them was not.

Taking this inventory was read-only. Nothing was deleted, modified, redeployed or
re-granted while it was taken, and no credential was created, rotated, revoked or
read. Classification only.

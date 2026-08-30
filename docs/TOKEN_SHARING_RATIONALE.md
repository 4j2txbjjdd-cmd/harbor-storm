# `GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=False`

*Written for a reader who suspects this flag is a security shortcut. The
position is stated plainly and the evidence is left to do the work.*

## What the flag is

Agent Runtime does not, by default, hand an agent's credential to Google client
libraries. With the default in force, `google-cloud-firestore` authenticates as
nothing and Firestore answers **401**. Setting the flag to `False` lifts that
restriction, so the credential Google issued to this agent is the one the client
library presents.

Read the name carefully, because the polarity is easy to invert. The flag
*prevents* token sharing; setting it to `False` therefore means token sharing is
**permitted**. It does **not** mean Agent Identity IAM is switched off. Those are
two different mechanisms, and separating them is the whole subject of the next
section.

## Two different protections, and only one is affected

| | affected by the flag |
|---|---|
| **Certificate-bound token protection (CAA)** — binding the token to the workload certificate so it cannot be replayed away from this workload | **Yes.** This is what the flag relaxes. |
| **Agent Identity IAM enforcement** — whether Google's IAM evaluates *this specific agent's* identity when it reaches a resource | **No.** Demonstrated below. |

Conflating these is the natural mistake, so the claim is not asserted — it is
shown three ways.

**Which engines the evidence below comes from.** The identity readback is the D0
probe `1562121799313915904`. The governed allow/deny pair is engine
`2414533581910048768`, which carries the Gateway binding and runs no actor. The
managed actor itself ran on a third engine, `3244216260136796160`, which is not
Gateway-bound. Nothing here shows one engine doing both: the actor path and the
governed-egress path are **not demonstrated end-to-end on one engine**, and the
argument in this document does not depend on their being the same engine — it is
about what the flag does and does not relax on whichever engine holds it.

## Evidence 1: the identity is what Google issued, read from the control plane

Not from my deployment config — from a `GET` on the Reasoning Engine
(`geap/d0_readback.json`, which reads back engine `1562121799313915904`, the D0
identity probe):

```json
"spec": {
  "identityType": "AGENT_IDENTITY",
  "effectiveIdentity": "agents.global.org-648972411952.system.id.goog/resources/aiplatform/projects/801248256447/locations/us-central1/reasoningEngines/1562121799313915904"
}
```

`spec` contains exactly two keys. There is **no `serviceAccount` field at all** —
Agent Identity requires its absence, and an absent key is the only way to be sure
one was never set. The identity is bound to one Reasoning Engine.

## Evidence 2: the 401 → 403 → success sequence

This is the load-bearing observation. It was originally recorded only as prose;
it is now witnessed by Cloud Logging records captured in
`geap/firestore_iam_enforcement_legs.json`.

| leg | when | exception | call site | what it means |
|---|---|---|---|---|
| **401** | 2026-08-27T17:59:45Z | `Unauthenticated: 401 Request had invalid authentication credentials` | `runtime_app.py:186` → `firestore_store.py:182` | flag at platform default — the credential is not handed to `google-cloud-firestore`, so **no principal reaches Firestore** |
| **403** | 2026-08-27T18:03:55Z | `PermissionDenied: 403 Missing or insufficient permissions` | `runtime_app.py:186` → `firestore_store.py:182` | flag `False` — a principal now reaches Firestore and **IAM refuses it** |
| **403** | 2026-08-27T18:33:42Z | `PermissionDenied: 403 Missing or insufficient permissions` | `firestore_store.py:346` (`create_work`) | same, before the grant on that exact principal had propagated |
| **success** | — | — | — | **different artifact** — `geap/d1_shift_accept.json`: `authoritative_state.committed_plan_id: harbor-agent-plan-1` |

The first three rows come from `geap/firestore_iam_enforcement_legs.json`. The
success row does **not**, and cannot: that file is a `severity=ERROR` query, so a
successful run is structurally absent from it. Cite the two files separately.

The grant that matters names the **agent-identity principal for this engine**,
one `principal://` member per engine — witnessed in
`geap/iam_project_agent_principals.json`:

```
principal://agents.global.org-648972411952.system.id.goog/resources/aiplatform/
  projects/801248256447/locations/us-central1/reasoningEngines/<ENGINE_ID>
```

Stated precisely, because the absolute version of this sentence was wrong.
`roles/aiplatform.user` is bound to seven per-engine `principal://` members and
nothing else. `roles/datastore.user` is bound to those same seven **plus**
`serviceAccount:801248256447-compute@developer.gserviceaccount.com`, a
pre-existing Compute Engine default service account that Harbor did not grant and
that is not the identity this runtime presents. Neither binding contains a
`principalSet` or a project-wide role. `geap/iam_project_agent_principals.json`
is filtered to the agent-identity trust domain and says so in its own `note`
field, so read the unfiltered policy — `gcloud projects get-iam-policy
harbor-storm-fleet` — if you want the full membership.

That extra member does not touch the argument below. The runtime authenticates
as its Agent Identity principal, not as the compute service account; what the
403 shows is that Google evaluated *that* principal.

**The 403 is the proof.** If the flag defeated Agent Identity IAM enforcement,
that state could not exist: an unenforced identity yields either 401 (no
credential) or success (credential accepted without evaluation). A 403 means
Google evaluated a *specific* identity against a *specific* policy and refused
it. The subsequent success, after nothing changed except that grant propagating,
means Google then evaluated the same identity and allowed it.

The runtime reaches Firestore because the identity Google issued was authorised —
not because a service account existed, and not because enforcement was off.

## Evidence 3: the governed path does not route through the shared token

An earlier version of this section claimed the governed allow/deny pair had been
obtained on engines deployed **both ways** — one with the flag set, one without —
and concluded that the governance claim held with certificate binding fully in
force. That control does not exist. Both engines carry
`GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES = "False"`, confirmed by
control-plane GET on each:

| engine | flag at probe time | weather (bound) | cargo (unbound) | evidence |
|---|---|---|---|---|
| `4557684054584983552` | set to `False` | 200, real forecast | 403 IAP-generated | `geap/d1_egress_final.json` |
| `2414533581910048768` | set to `False` | 200, real forecast | 403 IAP-generated | `geap/d1_egress_rotated.json` |

No governed arm was ever run with the flag unset, and none is claimed. What the
two rows do show is reproducibility: same gateway, same registry state, same
transport, two different engines, two different Weather credentials, identical
allow/deny result.

What remains is a **mechanism argument, not an experiment**, and it is labelled
that way deliberately:

- The allow/deny decision is taken by the Agent Gateway and IAP against the
  engine's **Agent Identity principal**, evaluated as `roles/iap.egressor` on the
  registry endpoint. `geap/iap_endpoint_policies.json` shows the weather endpoint
  naming four such principals and the cargo endpoint carrying no bindings at all.
- The permitted destination authenticates with an **API key**, not with the
  agent's OAuth token — so the token that the flag makes shareable is not the
  credential the weather arm presents.
- The denial arrives from IAP *before the destination is reached*
  (`x-goog-iap-generated-response: true`), so no token was presented on that arm
  at all.

That is a reason to expect flag-independence. It is not a measurement of it, and
this document does not claim one. Testing it would mean deploying a governed
engine with the flag unset and re-running the triad; that has not been done.

The flag is required for the *actor* path, where `google-cloud-firestore` speaks
gRPC to authoritative state.

## What the flag genuinely costs

It relaxes certificate binding on the token. A token that would otherwise be
usable only from this workload becomes usable by client libraries running in it.
That is a real reduction in exfiltration resistance and is not waved away here.

What this repository has **not** demonstrated is the negative case — I did not
attempt to replay a token off-workload with the flag set, and I do not claim
that property either way.

## Why it is set

Google's own Agent Runtime → Agent Gateway documentation configures this flag
alongside `identity_type=AGENT_IDENTITY` in the same example. It is the
documented way to run a managed agent that uses Google client libraries under an
Agent Identity, which is precisely what the actor path does.

Where it is set, with the reasoning, in `geap/d1_deploy_runtime.py`:

```python
# With Agent Identity the agent's credential is NOT handed to Google client
# libraries by default, so google-cloud-firestore authenticates as nothing and
# Firestore answers 401. Sharing it is what makes IAM grants written against
# the agent-identity principal actually govern this runtime's reads and
# writes -- which is the whole point: the identity Google issued is the one
# Firestore checks.
"GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES": "False",
```

## Supporting artifacts

| file | proves |
|---|---|
| `geap/d0_readback.json` | control-plane identity: `AGENT_IDENTITY`, no `serviceAccount`, engine-bound `effectiveIdentity` |
| `geap/firestore_iam_enforcement_legs.json` | the 401 / 403 / 403 legs with timestamps, exceptions and call sites. **No success leg** — it is a `severity=ERROR` query |
| `geap/iam_project_agent_principals.json` | the two granted roles bound per agent-identity principal. Filtered to that trust domain, per its own `note`: live `roles/datastore.user` also carries a pre-existing compute service account. No principalSet or project-wide role in either binding; the file's one `principalSet://` is Google's default `roles/aiplatform.agentDefaultAccess` |
| `geap/iap_endpoint_policies.json` | weather endpoint carries `roles/iap.egressor`; **cargo endpoint has no bindings at all** |
| `geap/d1_egress_final.json` | governed pair on `4557684054584983552`, flag set to `False` |
| `geap/d1_egress_rotated.json` | governed pair on `2414533581910048768`, flag set to `False` — identical result on a rotated credential |

## The one-sentence answer

> The flag relaxes certificate *binding* on the token, not identity
> *enforcement*: Firestore returned 403 to this agent under a grant that named
> this agent's principal, and returned success once that grant propagated — which
> could not happen if the identity were unenforced. The governed allow/deny proof
> is decided by IAP against that same principal, on an arm whose destination
> authenticates by API key rather than by the shared token.

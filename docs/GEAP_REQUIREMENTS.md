# Fortified Enterprise Fleet — track requirements

> **This document states the track's requirements and the constraints Harbor
> holds itself to. It is not the architecture.** For what Harbor actually
> builds and runs, and the evidence behind each claim, see
> [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) — that is the canonical current
> topology and wins over anything here.

Build a scalable network of institutional agents that hook into official enterprise
infrastructure. Teams must demonstrate how agents are cataloged for cross-department
use, how they safely maintain context across weeks of asynchronous operations, and how
they interact with production data without violating enterprise compliance, data
sovereignty, or security policies.

## The platform surface, and what Harbor actually claims

The list below is the **platform's catalogue** of Google Enterprise Agent Platform
surfaces. It is not a checklist Harbor must complete: a submission is judged on
what it demonstrates, not on how many product names it touches. This section
therefore separates three different things, so no reader has to guess which
category a surface falls into.

### 1. Submitted stack — implemented, exercised, and evidenced

Read this table with the engine record in hand, because it is the first thing
a reader meets:

- **Engine A = `3244216260136796160`** — the original managed actor proof. It
  is **not** gateway-bound.
- **Engine B = `2414533581910048768`** — the original governed-egress proof. It
  runs **no** actor.
- **Engine C = `6110651869841850368`** (`harbor-converged`, 2026-08-31) — the
  convergence: the same gateway-bound engine runs the actor end-to-end.
  Accept and stale-control runs plus the Gateway's ALLOWED records:
  `geap/d2_converged_accept.json`, `geap/d2_converged_control.json`,
  `geap/gw_logs_converged.json`, `geap/gw_logs_converged_firestore.json`.

The rows below cite the original A/B artifacts because each control was first
demonstrated in isolation; **the combined actor-plus-Gateway path is now also
demonstrated on engine C**. The convergence record, including exactly what its
committed gateway logs do and do not capture, is in
[`docs/GEAP_D0_D1.md`](GEAP_D0_D1.md)'s addendum.

| surface | what Harbor demonstrates | evidence |
|---|---|---|
| **Agent Runtime** | Harbor's bounded actor runs as a managed Reasoning Engine; a real Gemini 3.5 Flash completes a shift | `geap/d1_shift_accept.json` (**Engine A**, `3244216260136796160`, not gateway-bound) |
| **Agent Identity** | `identityType: AGENT_IDENTITY`, **no** service account, engine-bound `effectiveIdentity` | `geap/d0_readback.json` |
| **Agent Gateway** | governed egress on a gateway-bound engine; allow/deny decided by Google | `geap/gw_logs_rotated.json`, `geap/failclosed/http_triad_gateway.json` (**Engine B**, `2414533581910048768`, runs no actor) |
| **Agent Registry** | two registered endpoints, one bound for egress and one deliberately unbound; registration is what separates *unregistered, refused outright* from *registered but not authorized* | `geap/iap_endpoint_policies.json` (both endpoint ids and their IAP IAM), and the registry endpoint named on the decision records for registered destinations in `geap/gw_logs_rotated.json` — the unregistered denial carries no registry attribution, which is itself the point |

Registration is not an authorization allowlist. A registered endpoint is only
reachable if per-endpoint IAP IAM also grants `roles/iap.egressor`: the cargo
endpoint is registered, carries no bindings at all, and is refused.

### 2. Additional Google surfaces the submitted path exercises

Identity-Aware Proxy (the authorization decision behind the Gateway), Google Cloud
IAM (`roles/iap.egressor` granted per registered endpoint, to named engine
principals — the governing grant is per-endpoint, not per project), Cloud
Network Security and
Network Services (the `authzPolicy` / `authzExtension` pair), Cloud Firestore
(authoritative operational truth), Vertex AI and Gemini 3.5 Flash, the Google
Weather API, Cloud Logging (Google's own gateway decision records), and Cloud
Trace, which receives spans emitted by Agent Runtime. The full list with
per-entry justification is `docs/SUBMISSION_FIELDS.md`.

### 3. Not claimed as implemented

Harbor does **not** claim these, and no artifact in this repository asserts them:

- **Memory Bank** — not configured. The `memoryBankConfig` visible in an engine
  readback is Google's empty default (`{"generationConfig": {}}`). Harbor's
  cross-session continuity is Firestore, which is operational truth rather than
  contextual memory, and that is a deliberate design position rather than a
  substitute claim.
- **Model Armor** — not deployed. Harbor's guardrail is structural rather than
  inline: the model is offered exactly five tools — `claim_work`, `read_facts`,
  `report_constraint`, `propose_plan`, `read_trace` — and holds no verify,
  commit, peer-transfer, revision-advance or direct authoritative Firestore
  mutation authority, audited on the outgoing request
  (`app/agents/execution.py::assert_no_authority_tools`). That constrains what a
  successful injection could *do*; it is not the same control as inline prompt-
  injection screening, and is not presented as one.
- **Agent Observability as a Harbor-authored integration** — Cloud Trace does hold
  spans from the managed actor runs (Engine B runs no actor and emits none of
  them), but Agent Runtime emits them; Harbor wrote no
  instrumentation and does not list OpenTelemetry as something it built with.

Absence here is scope, not failure. Each line above states what *is* true in
place of the surface, so the boundary is legible rather than quietly skipped.

## Active architecture constraint

**GEAP wraps and governs the existing HarborWindow / StormSlot operational system.
It does not replace the application core.**

### Preserve — non-negotiable

- authoritative operational state
- bounded agent scopes
- Firestore as operational truth
- deterministic verification-before-commit
- event application semantics and idempotency — replaying an event must not
  double-apply it. This is a property of the core's event handling
  (`tests/test_event_idempotency.py`), not of a transport: **Pub/Sub is not part
  of the submitted architecture**, and the legacy Cloud Run / Pub/Sub packaging
  is retained for regression coverage only, as the Prohibited list below says
- state-revision / stale-plan protection
- dual HarborWindow and StormSlot scenarios
- deterministic mock fallback
- observable rejection -> verification -> authoritative commit sequence

### Prohibited

- Do **not** replace the above with a central supervisor, a generic enterprise
  workflow, or a GEAP-native abstraction merely because one exists.
- **If Memory Bank were added, it would be contextual continuity, not operational
  truth.** Firestore remains operational truth. It is not implemented here.
- **Agent Runtime governs execution, but runtime agents still receive no verification
  or authoritative-commit authority.** The verification membrane holds under GEAP.
- **Cloud Run** is **not** the enterprise agent control plane. The legacy Cloud Run /
  Pub/Sub packaging that remains in the repository is retained for regression
  coverage only; see `docs/ARCHITECTURE.md`.

## Note on `state-revision / stale-plan protection`

This was the last constraint in the Preserve list to be met: early revisions of
the store had no revision field on `CandidatePlan`, and `commit_plan()` checked
only `plan.verified`, so a plan verified against a world that had since changed
could still commit. It is now enforced — a candidate is bound to the revision it
was built from and cannot verify or commit against a later one. The invariant and
its evidence are in [`docs/ARCHITECTURE.md`](ARCHITECTURE.md); the regression
tests are `tests/test_stale_plan.py`.

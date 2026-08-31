# Harbor — HarborWindow / StormSlot

**One weather-sensitive harbour departure, owned by three parties who each hold a
different piece of the truth, and none of whom may authorise it alone.**

A small island run needs a sailing slot. The **window-agent** knows the marine
weather window. The **cargo-agent** knows manifest weight against vessel
capacity. The **harbormaster-agent** knows slots, bookings and the island's
landing cutoff. No one of them can clear a departure, because no one of them can
see the whole constraint. Gemini 3.5 Flash reasons over what an actor is allowed
to read and **proposes** — it cannot verify and it cannot commit.

> **A proposal can be correct and still lose the right to become true because the
> world changed.**

That is the mechanism the whole system exists to enforce:

```text
proposal @ revision N
  → world moves to N+1
  → proposal becomes stale
  → no authoritative commit
  → re-observe / replan
  → deterministic verification
  → commit
```

The verifier recomputes from authoritative facts. It ignores whatever the model
asserted about its own plan, and no model can reach it. A plan bound to revision
N cannot commit against revision N+1 — not because the model behaved, but because
the store refuses it.

**HarborWindow is the flagship scenario. StormSlot is the transfer evidence** —
the same substrate, same verifier, same fencing, same trace, moved to a container
crossing port → truck → warehouse, to show the architecture is not specific to
one workflow. **ReliefRun is the third instantiation** — a disaster-relief
mission on a hazard-gated corridor, added 2026-08-30 and described
[below](#the-third-instantiation-reliefrun).

- **Why this problem is real:** [`docs/WHY_HARBOR.md`](docs/WHY_HARBOR.md)
- **Every claim, its evidence, and whether it is live or committed:** [`EVIDENCE.md`](EVIDENCE.md)
- **The submitted topology:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

Verify it yourself in about a minute, offline, with no Google Cloud account —
start at [Reproduce it](#reproduce-it).

## What the substrate carries

Eight mechanisms end to end:

1. external operational state
2. event-driven weather disruption
3. multiple bounded actors
4. atomic work claims
5. candidate plans
6. deterministic feasibility verification
7. authoritative state changes only after verification
8. replayable event trace

## Reproduce it

**Most of this system verifies offline, with no credentials and no Google Cloud
account.** That is deliberate: the seeded path is the reference implementation
and the live providers are an upgrade to a working path, never the only path. A
reviewer can confirm the verification membrane, both scenarios, the six hard
gates — five mechanically checked and one manual demo-legibility gate — and the
full test suite without signing in to anything.

### 1. Set up (any platform, ~1 minute)

Python 3.11+ is required (`google-adk` needs ≥3.10; macOS system `python3` is
3.9 and too old).

```bash
git clone https://github.com/4j2txbjjdd-cmd/harbor-storm.git
cd harbor-storm
python3.12 -m venv .venv          # or any python >= 3.11
.venv/bin/pip install -r requirements.txt
```

*macOS/Homebrew:* `python3.12` may not be on `PATH`; use
`/opt/homebrew/bin/python3.12` instead.

### 2. Verify — no credentials needed

| command | expected output | time |
|---|---|---|
| `.venv/bin/python -m pytest tests -q` | `315 passed, 47 skipped` | ~1.2s |
| `.venv/bin/python -m app.gate` | `stormslot — 5/5 mechanical gates, SURVIVES`<br>`harborwindow — 5/5 mechanical gates, SURVIVES` | ~0.1s |
| `.venv/bin/python -m app.demo harborwindow --pretty` | 16-line trace ending `COMMITTED -> harbor-plan-2` | ~0.1s |
| `.venv/bin/python -m app.demo stormslot --pretty` | 16-line trace ending `COMMITTED -> stormslot-plan-2` | ~0.1s |
| `.venv/bin/python -m app.sentinel harborwindow --ticks 3 --interval 0 --disrupt-at-tick 2` | tick 1 `unchanged`; tick 2 `CHANGED -> applied` with `COMMIT_REVOKED` then `PLAN_COMMITTED -> harbor-plan-3`; tick 3 `unchanged` | ~0.1s |
| `.venv/bin/python -m app.relief_demo --disrupt --pretty` | 34-event trace ending `COMMITTED -> relief-r1-p3` — see [ReliefRun](#the-third-instantiation-reliefrun) | ~0.1s |
| `.venv/bin/python -m app.fleet_demo --disrupt --pretty` | 25-event trace: naive board refused, fleet committed, bridge fails + surge pulse, assignment revoked, reallocated — `COMMITTED -> fleet-r1-p3` | ~0.1s |
| `.venv/bin/python -m app.metrics relieffleet --disrupt` | coordination numbers folded from the trace: 1 revocation with its quoted reason, reallocation span, absorbed redeliveries | ~0.1s |

**About the 47 skips.** All 47 are the same suite —
`tests/test_store_contract.py`, which runs one store contract against both the
in-memory and Firestore backends. Without a Firestore emulator the Firestore half
skips by design. Run them with the emulator (section 5) and the skips become passes.

**What to look for in the demo output.** The trace is the point, not the final
line. `app.demo harborwindow --pretty` shows the whole membrane in 16 events:

```
 10  PLAN_PROPOSED            window-agent         {"plan_id": "harbor-plan-1"}
 11  PLAN_REJECTED            verifier             {"plan_id": "harbor-plan-1", "reason": "harbor wind 42 kph over limit at hour 12"}
 12  REPLAN_STARTED           window-agent         {"reason": "harbor wind 42 kph over limit at hour 12"}
 13  PLAN_PROPOSED            window-agent         {"plan_id": "harbor-plan-2"}
 14  PLAN_VERIFIED            verifier             {"plan_id": "harbor-plan-2"}
 15  PLAN_COMMITTED           verifier             {"plan_id": "harbor-plan-2"}
 16  SAILING_RESCHEDULED      harbormaster-agent   {"from_hour": 12, "to_hour": 14}
```

A proposal is refused on a named physical reason, the system replans, and only
the verified plan commits. `PLAN_PROPOSED` is attributed to an agent;
`PLAN_VERIFIED` and `PLAN_COMMITTED` are attributed to `verifier`. Those are
different actors on the record because they are different actors in fact.

Both scenarios are byte-identical across runs — same seed, same trace.

**Or watch it in a browser.** The same seeded membrane has a small dashboard —
still offline, still no credentials:

```bash
.venv/bin/python -m uvicorn app.api:app --port 8000
```

Open <http://127.0.0.1:8000/>, pick a scenario, press **Run**, then
**Storm arrives early**. The booked hour turns red, the verifier's refusal is
quoted on screen — *harbor wind 42 kph over limit at hour 12* — and the
committed plan moves the departure, with the full numbered event trace
underneath. The header badges state exactly which backends are in play
(`state: memory`, `weather: mock`, `deterministic replay`), so what you are
watching is the reference path, not a staged recording.

### 3. Long-horizon operation — the sentinel

Commitment does not end scrutiny. The revocation semantics are in the frozen
core — a committed plan whose world moves is revoked by the verifier on a
named physical reason (`COMMIT_REVOKED`) and the fleet replans — and the
sentinel carries them across wall-clock time. It polls the forecast on an
interval; when the observation changes it hands the new truth to the same
disruption path the seeded demos use. It emits nothing, mutates nothing, and
holds no verify or commit tool: every authoritative consequence of a tick is
the verifier's, because it is the same code path.

```bash
.venv/bin/python -m app.sentinel harborwindow --interval 60
```

seeds a run, commits a plan, and then watches. On the seeded lane
`--disrupt-at-tick N` stages the forecast change for a deterministic demo; on
the live lane (`WEATHER_PROVIDER=google`) that flag is refused, because there
the real forecast is the disruption. Observations are content-addressed — the
disruption event id is derived from the forecast itself — so a repeated or
crash-replayed observation applies at most once, landing on the trace as
`DUPLICATE_EVENT_IGNORED` and moving nothing.

With `STATE_BACKEND=firestore` the run is durable: kill the sentinel,
restart it with `--run-id <the-run>`, and it re-observes and reconciles on
the first tick — it cannot know what moved while nothing was watching, so it
asks the world rather than trusting its memory. `tests/test_sentinel.py`
holds all of this to contract.

**Event-driven ingress, same membrane.** The API also carries a Pub/Sub push
endpoint — the shape a production subscription would deliver to — and it
treats a disruption as evidence, never as an instruction: the message supplies
facts, and the verifier still decides. With the dashboard server from step 2
running:

```bash
RUN=$(curl -s -X POST localhost:8000/runs -H 'content-type: application/json' \
  -d '{"scenario":"harborwindow"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
DATA=$(printf '{"run_id":"%s"}' "$RUN" | base64)
curl -s -X POST localhost:8000/pubsub/push -H 'content-type: application/json' \
  -d "{\"message\":{\"data\":\"$DATA\",\"messageId\":\"m-001\"},\"subscription\":\"local\"}"
```

Expected: `"outcome": "applied"` with `committed_plan_id: harbor-plan-3` — the
envelope revoked the standing commitment (`COMMIT_REVOKED` on the trace, with
the wind reading that did it) and the fleet replanned. Send the **same**
envelope again and the response is `"outcome": "duplicate"`: at-least-once
delivery is expected, deduplication is on the delivery identity
(`messageId`), and the refusal itself is on the record as
`DUPLICATE_EVENT_IGNORED`. A malformed envelope returns 400 rather than a
silent ack, so it retries and then dead-letters instead of poisoning the run.
The sentinel and the push endpoint are the same disruption path on two
clocks: one polls, one is pushed.

### 4. Determinism check

The seeded lane must not depend on the network. This is asserted mechanically,
not by inspection: one test makes the live weather adapter unconstructable and
then runs every gate for both scenarios plus both demos through it.

```bash
.venv/bin/python -m pytest tests/test_live_gate.py -q      # 11 passed
```

### 5. Firestore contract suite (optional — needs a JDK + firebase-tools)

```bash
PATH="/opt/homebrew/opt/openjdk/bin:$PATH" firebase emulators:exec \
  --only firestore --project harbor-storm-local \
  'FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 GOOGLE_CLOUD_PROJECT=harbor-storm-local \
   .venv/bin/python -m pytest tests -q'
```

Expected: `362 passed` with **none skipped** — the emulator turns the 47 skips
above into passes. Measured on 2026-08-28 against the frozen tree
(engineering SHA `687eebfd26f64d87f3c8db49756f838dc90bc02a`): `305 passed` in
14.3s; measured again on 2026-08-31 with the sentinel, ReliefRun, portal and
ReliefFleet suites included: `362 passed` in 10s (re-measured after the audit fixes; 113s cold-JVM on first run).
The same contract runs against both backends, so the in-memory store and
Firestore cannot drift on the question that decides whether a write is
authoritative.

### 6. Live Google paths (optional — needs your own Google Cloud project)

These require credentials and **your own** project; the identifiers below are
mine and must be substituted. None of this is needed to verify anything above.

**Live weather.** Needs a Google Weather API key:

```bash
WEATHER_PROVIDER=google GOOGLE_WEATHER_API_KEY=<your-key> \
  .venv/bin/python -m app.live_gate --provider live
```

Expected: `LIVE PASS` for both scenarios. Exit 2 means the run was blocked and
**nothing was proven** — there is deliberately no fallback to the seeded
forecast.

**Real Gemini actor.** Needs Vertex AI enabled and ADC
(`gcloud auth application-default login`):

```bash
GOOGLE_GENAI_USE_VERTEXAI=true GOOGLE_CLOUD_PROJECT=<your-project> \
GOOGLE_CLOUD_LOCATION=global .venv/bin/python -m app.agents.probe
```

Expected: `ALL AS EXPECTED` — one candidate accepted by the deterministic
verifier, and one refused as stale.

**Managed Agent Runtime.** Reproducing the deployed governed path
(Agent Identity → Agent Gateway → IAP → endpoint IAM) additionally requires
deploying a Reasoning Engine into your own project. See
`docs/GEAP_D0_D1.md` for the exact deployment and the evidence it produced, and
`geap/d1_deploy_runtime.py` for the deployment script.

**Three engines carry the managed evidence.** Engines A and B are the
original two-proof record; engine C is their convergence:

- **Engine A — `3244216260136796160`** is the original actor proof: a real
  Gemini 3.5 Flash runs Harbor's bounded actor against a five-tool surface and
  cannot reach the verifier or the store. Not Gateway-bound.
- **Engine B — `2414533581910048768`** is the original governed-egress proof:
  Agent Identity, Agent Gateway, IAP and per-endpoint IAM decide what that
  identity may reach. Runs no actor.
- **Engine C — `6110651869841850368`** runs the actor **and** is
  Gateway-bound — the combined path, demonstrated end-to-end on one engine
  on 2026-08-31.

**Converged, 2026-08-31.** Engine C — `6110651869841850368`
(`harbor-converged`) — runs the actor **and** is Gateway-bound: a real
Gemini 3.5 Flash executed `run_shift` end-to-end on it against registered
endpoints holding per-endpoint `iap.egressor` grants. The verifier accepted
and committed on that engine
([`geap/d2_converged_accept.json`](geap/d2_converged_accept.json)), and the
stale control was refused on the same engine
([`geap/d2_converged_control.json`](geap/d2_converged_control.json)). The
Gateway's own captured `ALLOWED`/200 records cover the **model-plane and
telemetry** egress of those runs
([`geap/gw_logs_converged.json`](geap/gw_logs_converged.json)) and the
**Firestore** egress, attributed to the registered `harbor-state-store`
endpoint
([`geap/gw_logs_converged_firestore.json`](geap/gw_logs_converged_firestore.json)).
**Credential egress is the one leg without a captured success record:** it is
denial-proven pre-binding — the gateway refused `iamcredentials` by name
before the endpoint was registered and bound — and its success afterward is
inferred from the completed run, not from a captured log line. The
convergence was reached denial-first: every destination was first named by a
gateway refusal ([`geap/gw_logs_converged_probe.json`](geap/gw_logs_converged_probe.json))
and only then registered and bound — nothing reached the actor's world until
the control plane was explicitly told it could.

[`EVIDENCE.md`](EVIDENCE.md) tags each managed row with the engine it came
from; the original two-proof record and the convergence procedure are in
[`docs/GEAP_D0_D1.md`](docs/GEAP_D0_D1.md).

### What each check proves

| check | claim |
|---|---|
| `app.gate` | six hard gates: five mechanically checked and one manual demo-legibility gate — the mechanical five include *weather changes a decision* and *deterministic replay* |
| `test_verification_membrane.py` | authoritative state moves only through the verifier |
| `test_stale_plan.py` | a plan bound to an old revision cannot commit |
| `test_agents.py` | no actor holds a commit or verify tool; scopes are disjoint |
| `test_fence_mandatory.py` | no authoritative write without a declared fence |
| `test_claim_contention.py` | losing a claim and failing to resolve one are different outcomes |
| `test_live_gate.py` | the seeded lane cannot reach the network |
| `test_sentinel.py` | commitment does not end scrutiny: a changed observation revokes and replans through the verifier, applies at most once across restarts, and the sentinel itself holds no authority |
| `test_relieffleet.py` | the fleet assignment is one candidate: slot reuse, double assignment, missing missions and bed overruns refused; a bridge failure or hazard pulse revokes the committed assignment and the fleet reallocates; edge state derives from the trace, never from mutated facts |
| `test_seismic.py` | a second evidence stream: a threshold quake maps to edge alerts under declared policy, revokes through the verifier, and the same USGS event id can never apply twice |
| `test_metrics.py` | the coordination numbers are a pure fold over the trace |
| `test_reliefrun.py` | the third instantiation passes the same membrane assertions: calm-forecast negative control, named-reason refusals, verify-before-commit, post-commit revocation, deterministic replay, disjoint actor scopes |
| `test_portal.py` | the web lane keeps the contracts: at-most-once observation under redelivery, verifier-only authority, the frozen app served unchanged underneath, and the seeded control refused on the live lane |
| `test_log_scrubber.py` | evidence capture redacts credentials before writing, and fails closed |

## The third instantiation: ReliefRun

Added 2026-08-30, informed by the dynamics of the August 2026 Nepal floods: a
glacier collapse and landslide sent a surge down a border valley, destroyed
the road corridor, and left an upstream barrier lake whose breach risk could
invalidate any rescue mission planned after the first flood. The failure mode
that matters is exactly the one this substrate exists to refuse — **a mission
can be correct when planned and wrong at dispatch, because the mountain moved
in between.**

The scenario's data is seeded and fictional (`CORRIDOR_A`, `VILLAGE_X`); it
demonstrates the mechanism class, not the event, out of respect for an
operation that is still under way. One relief mission on a hazard-gated
corridor: `hazard-agent` owns the corridor windows and operating limits,
`logistics-agent` owns payload against vehicle capacity, `ops-agent` owns
convoy windows, hospital beds, the extraction count and the access cutoff.
Three disjoint scopes, atomic claims — two teams cannot allocate the same
vehicle — and one deterministic verifier that recomputes the whole transit
from authoritative facts.

```bash
.venv/bin/python -m app.relief_demo --disrupt --pretty
```

The mission booked at first light is refused on a named reason (*corridor
water hazard 24 mm over limit at hour 6*), replans into the 9:00 window and
commits. Then the barrier-lake alert lands: the committed mission is revoked
by the verifier (*water hazard 26 mm over limit at hour 9*), claims are
reaffirmed, and the fleet re-commits into the 12:00 window. The calm-forecast
negative control is in `tests/test_reliefrun.py`: under a benign forecast the
first-light departure survives untouched. The sentinel watches this scenario
too, so the same story runs across wall-clock ticks.

**It is deployed, and it is live.** The lane runs at
<https://harbor-storm-801248256447.us-central1.run.app/relief> — Cloud Run,
one pinned instance (see `deploy/service.yaml` for why), runs durable in
Firestore (database `harbor`), and the frozen classic dashboard at the root
of the same service. The deployed instance reads **real Google Weather for
real Rasuwa-region coordinates** (supplied via `SITES_JSON`; `/relief/config`
reports them), and a Cloud Scheduler job re-observes the standing mission
every 30 minutes: most observations confirm the commitment against the real
forecast, and a moved forecast revokes it through the same verifier —
unattended. The page's badges state exactly which lane the instance is on;
the deterministic seeded lane remains the reference path locally. Locally the same surface is
`.venv/bin/python -m uvicorn app.portal:portal --port 8001`. One note for
reproducers: Google's frontend intercepts `/healthz` on `run.app` domains —
use `/config`.

ReliefRun is additive: `app.demo`, `app.gate`, the config surface and the API
are byte-identical to the frozen tree, and the scenario is deliberately not
part of the two-scenario decision gate. It exists to show the mapping is
direct — sailing slots become convoy windows, cargo capacity becomes payload
and hospital beds, marine weather becomes corridor hazard — and nothing in
the membrane had to change to carry it. The web lane is `app/portal.py`: it
mounts the frozen API unchanged and adds `/relief/*` on top, with
observations content-addressed exactly as in the sentinel, so an external
clock (for example a Cloud Scheduler job calling
`POST /relief/runs/<id>/observe`) can re-verify the standing mission on real
time and a redelivered observation applies at most once.

## From one mission to a fleet: ReliefFleet

Added 2026-08-31, on the same membrane. Three relief missions compete for two
trucks and one helicopter across shared convoy windows, a shared hospital-bed
pool, and a road network whose edges can fail. The candidate plan is the
**fleet assignment** — every mission mapped to a vehicle and a window — so one
committed plan is the fleet's operational truth, revision-fenced like any
other plan. Trucks and the helicopter fail on different physics (water on the
road vs wind aloft), and a failed bridge cuts every truck route that crosses
it.

```bash
.venv/bin/python -m app.fleet_demo --disrupt --pretty
```

The naive board (everything at first light) is refused on a named reason; the
planner reallocates and the fleet commits. Then the second surge lands — a
barrier-lake pulse floods the truck windows while a bridge fails — and the
committed assignment is revoked by the verifier; in the reallocation the
bridge village flies in the same window and the helicopter takes a second
sortie at noon. Two agencies cannot hold the same vehicle: each vehicle is a
claimable work item held by a distinct team, a rival claim is refused on the
record naming the holder, and the verifier independently rejects any
assignment that reuses a (vehicle, window) slot.

Two design points, both auditor-driven: **edge state is derived from the
record** — a failure is an `EDGE_FAILED` event folded over the seeded
baseline (`current_edges`), never a mutation of facts, so no backend can
silently lose a disruption on re-read; and plan ids are revision-qualified
(`fleet-r1-p3`), so the trace never holds one id naming two proposals.

**A second evidence stream: seismic.** `app/providers/seismic.py` carries a
seeded mock (the deterministic reference) and a live USGS FDSN adapter
(public, no key). A quake at or above a declared threshold maps to edge
alerts under policy — the provider never touches the store; the alerts enter
as `EDGE_FAILED` evidence and the verifier decides what survives, deduplicated
by USGS event id. `tests/test_seismic.py` runs the end-to-end: quake → edge
failure → committed assignment revoked → helicopter reallocation.

**Numbers a coordinator would ask for** fold straight off the trace
(`app/metrics.py`): revocations with their quoted reasons, reallocation span,
refused double-allocations, absorbed redeliveries — no instrumentation,
because the trace is the record.

## Frozen core

This repository is a snapshot of a frozen tree, published without its
engineering history. The freeze points cannot be diffed against here — the
commits this repository carries all post-date first publication and are the
declared additions below — so the provenance is stated as fact rather than
left to be re-derived:

- **The pre-GEAP operational core was frozen first**, at engineering commit
  `cf91551` (named `core-freeze-1`): fencing, claim-contention classification, the
  deterministic verifier, real Gemini 3.5 execution, live Google Weather in both
  scenarios, and deterministic replay.
- **Every file submitted here existed at engineering SHA
  `687eebfd26f64d87f3c8db49756f838dc90bc02a` and is content-identical to it,
  apart from the judge-facing prose (`README.md`, `EVIDENCE.md`, `docs/`) and
  `.gitignore`, which were finalized after that SHA for this snapshot; the
  sentinel (`app/sentinel.py`, `tests/test_sentinel.py`), added later at
  engineering SHA `429683114373b4fe197d9fc1da34509747bb2a5d`; and ReliefRun
  (`app/scenarios/reliefrun.py`, `app/relief_demo.py`,
  `tests/test_reliefrun.py`, plus the sentinel gaining it as a watchable
  scenario), added at engineering SHA `893f759`; and the portal
  (`app/portal.py`, `app/static/relief.html`, `tests/test_portal.py`), added
  at engineering SHA `9803336`.**
- **The delta between them, in `app/` and `tests/`, is five files and is
  presentation only:** `app/api.py`, `app/gate.py`, `app/config.py`,
  `app/providers/routes.py`, `tests/test_api.py` — docstrings, two judge-facing
  strings and one exception message that still said the scenario choice had not
  been made, plus the test that pinned the old wording. `app.gate --json` is
  byte-identical across that change and no verification logic differs.
- **Everything else added after the core freeze is additive**, not a rewrite of
  frozen code: the managed-runtime adapter `app/geap/` with
  `tests/test_log_scrubber.py`, the wall-clock re-observation harness
  `app/sentinel.py` with `tests/test_sentinel.py`, the ReliefRun
  instantiation with `tests/test_reliefrun.py`, the portal with
  `tests/test_portal.py`, and the ReliefFleet instantiation
  (`app/scenarios/relieffleet.py`, `app/fleet_demo.py`, `app/metrics.py`,
  `app/providers/seismic.py`) with its three test suites, added at
  engineering SHA `691c1fc`. No frozen file was modified to make any of them
  work — the sentinel drives the frozen `disrupt()` path and holds no
  authority of its own, ReliefRun reuses the frozen membrane without being
  wired into `app.demo`, `app.gate` or the API, and the portal mounts the
  frozen API unchanged underneath its added routes.

The delta is declared instead of absorbed because that is the honest form: editing
those strings and then claiming the original freeze had never moved would have been
the dishonest one. The point of freezing the core before the managed work started
is that the managed layer had to be built *around* the verification core rather
than by quietly reshaping it — and that is only checkable if the baseline is a
fixed, named commit.

[`docs/CORE_FREEZE.md`](docs/CORE_FREEZE.md) records the freeze in full: what each
of the five changed files changed, the evidence behind each frozen capability, and
what the freeze deliberately excludes.

## Competition submission state

**HarborWindow is the flagship demo scenario.** StormSlot remains a second
scenario on the same substrate, demonstrating that the verification and
coordination architecture is not specific to one workflow.

The submitted managed path uses Google Agent Runtime, Agent Identity,
Agent Gateway, Agent Registry, Gemini, Firestore, Google Weather,
Identity-Aware Proxy, and Google Cloud IAM. These surfaces were first
demonstrated as two proofs on two engines — the actor on one, governed egress
on the other — and converged on 2026-08-31: engine `6110651869841850368`
demonstrates the combined path end-to-end, as set out in
[Reproduce it](#reproduce-it).

Earlier exploration considered Cloud Run, Pub/Sub, Google Routes, and other
extensions. As of 2026-08-30, Cloud Run is exercised after all: it hosts the
public portal above. The managed Pub/Sub service and Google Routes remain
unused (the Pub/Sub *push endpoint* is exercised locally — see the
long-horizon section).

## Firestore emulator

The store contract suite runs one contract against both backends. Without
an emulator the Firestore half skips, so run it before trusting that
backend:

```bash
PATH="/opt/homebrew/opt/openjdk/bin:$PATH" firebase emulators:exec \
  --only firestore --project harbor-storm-local \
  'FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 GOOGLE_CLOUD_PROJECT=harbor-storm-local .venv/bin/python -m pytest -q'
```

Homebrew's `openjdk` is keg-only and is not on the default PATH, hence the
prefix. The alternative is the `sudo ln -sfn` symlink Homebrew suggests;
the prefix avoids needing root.

`firestore.rules` is emulator-only and denies everything — the Python
server SDK bypasses rules, so the suite is unaffected, and a stray deploy
fails shut instead of opening the database.

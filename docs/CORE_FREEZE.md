# Core freeze 1 — the pre-GEAP baseline

This document is a **provenance record, stated as fact.** The freeze discipline
it describes was carried out in the engineering history. The submitted
repository is a single snapshot of the result: it carries no tags, no branches
and no prior commits, so nothing here can be checked out or diffed against.
Every identifier below is given so the record is legible, not so it can be
re-run.

```
FREEZE_SHA      cf9155156f5cbfbc4e35cf8d9c356072abdd1fcc   ("cf91551")
NAMED           core-freeze-1   (annotated tag in the engineering history)
FROZEN          2026-08-27
SUBMITTED       687eebfd26f64d87f3c8db49756f838dc90bc02a   (code + evidence; docs finalized after)
```

This is the operational core the GEAP migration builds on. Everything below was
run at `cf91551` before the freeze was named; nothing was frozen on the strength
of a remembered result.

**The freeze names `cf91551`, not the commit that added this file.** The
baseline is the code that was verified, not the code plus a subsequent edit.
This document was written afterwards and describes the freeze; it does not
change what the freeze means.

### Why every number below is tied to a fixed tree

Evidence collected from a moving tree is void. Before any run whose numbers are
quoted here, the tree had to be a known committed SHA with no uncommitted or
untracked changes — an untracked `conftest.py` or `sitecustomize.py` can change
what a run does while the committed SHA still looks certified. Every quoted
number is stamped with the HEAD SHA and the capture time. The gate fails closed:
if a precondition does not hold, evidence collection stops rather than
proceeding with a caveat.


## The second freeze: what was actually submitted

`core-freeze-1` is provenance and does not move. What was actually submitted was
frozen separately, because deliberate changes were made after it.

```
NAMED                    submission-freeze-2
CORE_FREEZE_DELTA        5 files, presentation only
OPERATIONAL_CORE_DELTA   0
```

Five places still announced that the HarborWindow / StormSlot choice had not been
made, or cited an earlier scenario-comparison document that was withdrawn:

| file | change | AST skeleton vs `core-freeze-1` |
|---|---|---|
| `app/gate.py` | printed footer, and one docstring line citing the withdrawn document | identical |
| `app/api.py` | `GET /scenarios` → `selected` value and note text | identical |
| `app/config.py` | `make_routes` docstring | identical |
| `app/providers/routes.py` | `GoogleRoutesProvider` docstring and its `NotImplementedError` message | identical |
| `tests/test_api.py` | the assertion that pinned `selected is None`, and the test name | one node delta, `Is` → `Eq` |

All of it is metadata. `render()` appends its footer after `evaluate_all()` has
produced the report, and `app.gate --json` does not carry it — that output is
byte-identical across the change. `/scenarios` reports; `POST /runs` still
resolves the scenario from the request body. The Google Routes seam still raises
and is still unimplemented. No control flow, signature, provider implementation
or verification logic differs.

Recording the two freezes separately is the honest form. Editing the strings and
claiming the original freeze never moved would have been the dishonest one, and
leaving them would have left the headline command citing a document that no
longer exists, and the routes seam describing a decision that has since been
made as though it were still open.

**Why that document was withdrawn, since the reasoning matters more than the
file.** It held an earlier side-by-side comparison of HarborWindow and StormSlot,
intended as the procedure for choosing between them. That comparison was not a
valid basis for the choice: at that point StormSlot *measured* weather but let
nothing depend on it — the truck was dispatched into the storm hour regardless —
while HarborWindow had no rejectable proposal path at all, so it could never
demonstrate a refusal. The two failures ran in opposite directions, which made a
side-by-side score actively misleading rather than merely incomplete: each
scenario would have been flattered on exactly the dimension it could not support.
The discipline that replaced it was not a comparison but a set of hard gates
applied to both — weather must change a decision, a false proposal must be
deterministically rejectable, and authoritative state must move only after
verification. Only once both scenarios survived those gates on the common
substrate was the flagship chosen. That is why the comparison document was
withdrawn rather than updated, and why the strings that pointed at it had to go
with it. The reasoning is set out in full in
[`docs/WHY_HARBOR.md`](WHY_HARBOR.md).

Everything else in `app/` and `tests/` since `core-freeze-1` is **additive**:
`app/geap/` and `tests/test_log_scrubber.py`.

## What "frozen" means here

Not read-only. It means:

- GEAP work branched from `cf91551` rather than rewriting it, so the managed
  layer is additive and the difference between the two is always statable.
- A change to a frozen capability is a deliberate act that declares itself as
  one, not a side effect of Runtime, Identity or Gateway wiring.
- Any claim about these capabilities can be re-checked by running the command
  next to it. If a command stops passing, the freeze is broken and that is a
  finding, not a nuisance.

## Verified capabilities

Each line is a mechanism, where it lives, and the command that re-proves it.

### 1. Mandatory fencing — `app/core/store.py`

Every authoritative mutation must name the attempt it belongs to. `fence` has no
default: a caller passes the `Fence` of the event application doing the work, or
`UNFENCED` to declare on the record that the write has no delivery identity.
Omitting it, or passing `None`, raises `UnfencedMutationError`. Effects from a
superseded attempt are refused, and the refusal record travels on the exception
rather than on the store.

    .venv/bin/python -m pytest tests/test_fence_mandatory.py -q

### 2. Claim-contention classification — `app/core/store.py`

Losing a claim is an answer: another actor owns the item, `claim` returns False
and `CLAIM_REFUSED` names the claimant. Exhausting bounded retries without
establishing ownership is the *absence* of an answer, and raises
`ClaimContentionError` rather than manufacturing a refusal that names nobody.
`ClaimContentionError` and `SupersededWorkerError` are deliberately unrelated
types; conflating them would make the trace misreport which failure occurred.

    .venv/bin/python -m pytest tests/test_claim_contention.py -q

### 3. Deterministic verifier — `app/core/verify.py` (P0 invariant)

Authoritative state moves only through `verify_and_commit`. A plan is recomputed
from authoritative facts; agent-supplied metrics are ignored. Staleness is
checked first and again inside `mark_verified` and `commit_plan`, so a caller
cannot skip it by reaching past the membrane. A plan bound to revision R may not
commit once the world stands at R+1.

    .venv/bin/python -m pytest tests/test_verification_membrane.py tests/test_stale_plan.py -q
    .venv/bin/python -m app.gate          # gates 3 and 4

### 4. Real Gemini 3.5 execution — `app/agents/`

A live Vertex AI `gemini-3.5-flash` model runs as HarborWindow's `window-agent`
through the existing five-tool `ActorToolkit`, and Harbor decides the outcome.
The model is offered `claim_work, read_facts, report_constraint, propose_plan,
read_trace` and nothing else — audited from the outgoing `LlmRequest`, not from
this repo's own `tool_names()`. `require_model_floor` refuses anything below
Gemini 3.5, including `gemini-3-flash-preview` (which reads as 3.0).

    GOOGLE_GENAI_USE_VERTEXAI=true GOOGLE_CLOUD_PROJECT=harbor-storm-fleet \
    GOOGLE_CLOUD_LOCATION=global .venv/bin/python -m app.agents.probe

At freeze: accept case committed `harbor-agent-plan-1` departing 14:00, a value
the model derived from `read_facts` plus the trace. Control case refused —
`stale: plan bound to revision 0, world is at revision 1` — with
`committed_plan_id=None`. See
[`docs/GEMINI_EXECUTION_PROBE.md`](GEMINI_EXECUTION_PROBE.md).

The probe is deliberately narrow: it proves that a real model ran as a bounded
actor and that Harbor decided the outcome. It is **not** evidence for Agent
Runtime, Agent Identity or the Gateway — those are the later managed work, and
are evidenced separately.

### 5. Live Google Weather, both scenarios — `app/providers/weather.py`, `app/live_gate.py`

Real `weather.googleapis.com` responses normalise into `WeatherPoint` and drive
both scenarios through the existing `WeatherProvider` boundary. Weather is an
external fact source: it is not operational truth, not verifier authority, not
commit authority, not an agent. The gate asserts that the decision and the
observations agree, never that the weather has particular values.

    WEATHER_PROVIDER=google GOOGLE_WEATHER_API_KEY=... \
      .venv/bin/python -m app.live_gate --provider live

At freeze: HarborWindow LIVE PASS (HARBOR_A + ISLAND_B, HTTP 200, 24 points
each, Europe/Athens), StormSlot LIVE PASS (PORT_A, HTTP 200, 24 points,
Europe/Amsterdam). Observed values are not part of the freeze — live weather
changes, and re-running will show different numbers and possibly a different
committed hour. What must hold is that the checks pass. See
`docs/LIVE_WEATHER.md`.

### 6. Deterministic replay — `app/gate.py`, `app/demo.py`

The seeded lane is offline and byte-identical across replays, and cannot reach
the network even by accident: a test makes `GoogleWeatherProvider`
unconstructable and runs all six gates for both scenarios plus both demos with
disruption through it.

    .venv/bin/python -m app.gate                       # gate 6
    .venv/bin/python -m pytest tests/test_live_gate.py -q

## Evidence at the freeze SHA

```
TESTS_IN_MEMORY      247 passed, 47 skipped
TESTS_EMULATOR       294 passed, 0 skipped     (FIRESTORE_EMULATOR_HOST=localhost:8080)
GATES_DETERMINISTIC  stormslot 5/5 SURVIVES, harborwindow 5/5 SURVIVES
GATES_LIVE           harborwindow LIVE PASS, stormslot LIVE PASS
GEMINI_PROBE         ALL AS EXPECTED (accept committed, control refused stale)
SEEDED_DEMOS         harborwindow -> harbor-plan-2, stormslot -> stormslot-plan-2
KNOWN_FAILURES       none
```

Those two test counts are the counts **at `cf91551`**, not the counts a judge
will see today. The submitted tree reports 271 passed, 47 skipped in memory and
318 passed, 0 skipped against the Firestore emulator; the difference is the
additive work named above plus the wall-clock sentinel (`app/sentinel.py` with
`tests/test_sentinel.py`, added 2026-08-30), not a change to anything frozen.

Negative controls, all at the same SHA:

```
live asserted, WEATHER_PROVIDER=mock      exit 2, stdout empty, no network touched
live lane, unusable credential            exit 2, stdout empty, HTTP 400 reported
gemini-2.5-flash through the model floor  refused
gemini-3-flash-preview through the floor  refused (3.0 is below 3.5)
```

## What is NOT in this freeze

Naming these matters as much as naming what is in it. None of them is a defect;
each is unstarted or deliberately deferred.

- **The Google control plane**, all of it. This freeze is the thing the later
  control-plane work wraps. That work went on to implement Agent Runtime, Agent
  Identity, Agent Gateway and Agent Registry — and it did so across **two
  distinct Reasoning Engines, not one**. Engine `3244216260136796160`
  demonstrates the managed Gemini actor path and is **not** Gateway-bound; engine
  `2414533581910048768` demonstrates governed egress and does **not** run the
  actor. The combined path was not demonstrated end-to-end on a single engine,
  and nothing in this repository claims it was. Memory Bank is **not configured**
  and Model Armor is **not deployed** — neither is claimed as implemented
  anywhere in this repository, and no artifact here asserts either. The full
  scope, with what stands in place of each, is
  [`docs/GEAP_REQUIREMENTS.md`](GEAP_REQUIREMENTS.md); which engine proved what is
  in [`docs/ARCHITECTURE.md`](ARCHITECTURE.md).
- **Live routing.** `GoogleRoutesProvider` is unimplemented by design. StormSlot's
  live run uses live weather and the seeded route provider, and the gate says so
  in its own output.
- **Deployment.** `deploy/` and the Dockerfile are untouched and unproven. They
  are legacy Cloud Run / Pub/Sub packaging retained inside the freeze because
  `tests/test_packaging.py` asserts on them; they are not part of the submitted
  runtime topology. See `docs/ARCHITECTURE.md`.
- **Pub/Sub ingress authentication.** Not implemented in the frozen core, and
  Pub/Sub did not go on to become part of the submitted topology either.

## Follow-up register

Eight findings: known, non-blocking, and deliberately not fixed during the
freeze. Each was re-checked against the frozen code rather than carried from
memory.

Ranked, **at the freeze**, by what happens if one triggers rather than by how
likely it is: **F8** was judged most serious, because it fails open toward calm
inside the weather boundary and its consequence is a dangerous plan being
approved; **F2** next, because it is the guard the actor-authority claim rests
on. Both were latent then, which is why neither blocked the freeze. They are
recorded here as the state of the code at `cf91551`; this section is provenance,
not a current work queue.

### From the independent verification of real Gemini execution — `app/agents/execution.py`

| # | Finding | Where |
|---|---|---|
| F1 | The **requested** model id is checked against the floor; the **server-reported** `model_version` is recorded but never compared to it. A backend silently serving an older model would be recorded, not refused. | `require_model_floor` at the top of `run_actor_async`; `model_version` captured in the after-model hook |
| F2 | The authority guard is a substring **denylist** (`commit`, `verify`, `revoke`, `rebind`, `advance_revision`, `mark_verified`, `reject_plan`), not an exact allowlist of the five permitted tools. A future authority tool named outside that vocabulary would pass. | `AUTHORITY_TOOL_MARKERS`, `assert_no_authority_tools` |
| F3 | Callbacks are installed before `create_session` and `Runner(...)`, which are outside the `try/finally`. If either raises, the recorder stays attached to the caller's agent. | `run_actor_async` |
| F4 | The server's response identifier is on the wire but not in the evidence JSON. `interaction_id` is captured and has been `None` in every probe; `google-genai`'s `response_id` is not read. | `ModelTurn`, after-model hook |

At the freeze, F2 was ranked first among these: it is the guard the authority
claim rests on, and an allowlist of exactly the toolkit's five names would be
simpler and stronger than the denylist it replaces. Across the whole register F8
has the worse consequence if it ever triggers — see below.

### From claim-contention classification

| # | Finding |
|---|---|
| F5 | One zero-winner contention observation survived after the backoff improvement. Contention tuning was explicitly not reopened. |

### From live Google Weather in both scenarios

| # | Finding |
|---|---|
| F6 | A Firestore trace alone does not record which weather lane produced a run. Provenance lives on the provider and is read by the live gate. Recording the lane on the measurement event would put provider identity into scenario code and change deterministic trace payloads, so it was left out. |
| F7 | The live gate's "scenario consumed the live observations" check compared `[]` with `[]` on the calm day it was run. Mitigated by reporting observed maxima beside configured limits; the non-empty severe path is exercised offline with controlled data. A rough-weather live run would strengthen it for free. |
| F8 | `GoogleWeatherProvider._wind_kph` and `_rain_mm` **fail open on unknown or missing measurement data**, in the one direction that matters. An unrecognised unit is passed through unconverted — only `MILES*` and `INCH*` are converted, so a hypothetical `METERS_PER_SECOND` wind of 20 would be read as 20 kph rather than 72 — and a missing `wind` or `precipitation` block becomes `0.0` rather than an error. Both resolve toward calm, which can misclassify dangerous weather as safe and approve exactly the plan the verification membrane exists to reject. Latent today: Google returns `KILOMETERS_PER_HOUR` and `MILLIMETERS`, and both were observed doing so at the freeze. |

### Known limitations and follow-ups, and what became of each

These were the gaps known and recorded on 2026-08-27, when this freeze was
taken. Each is named by what it is, and carries what happened to it afterwards.
Two of them are still true of the submitted tree, and are stated as such rather
than quietly dropped.

| gap, as known at the freeze | what became of it |
|---|---|
| Control-plane migration — no managed Google work had started; this was the gate the freeze existed to unblock | **Done.** Agent Runtime, Agent Identity, Agent Gateway and Agent Registry were implemented and evidenced, across two engines rather than one. |
| Architecture documentation written against the real topology, rather than the legacy one | **Done** — [`docs/ARCHITECTURE.md`](ARCHITECTURE.md). |
| Re-run both scenarios, then select one as the flagship | **Done.** HarborWindow is the flagship; StormSlot stays on the same substrate as transfer evidence. |
| Pub/Sub ingress authentication | **Path not taken.** Pub/Sub did not become part of the submitted topology, so this describes a route that was never travelled rather than a hole in one that was. |
| Deployment documentation for the Cloud Run / Pub/Sub packaging | **Path not taken**, for the same reason. That packaging is retained in the freeze because `tests/test_packaging.py` asserts on it, and is not the submitted runtime. |
| Explicit verified effects recorded on `CandidatePlan` | **Still open.** It is not among the items the later submission work addressed. |
| `events_seen` grows without bound and is streamed whole on store open | **Still open, and still true of the submitted tree.** Real scalability debt, bounded today only because runs are short. |

Chronology, stated:

- **At `core-freeze-1`** — the operational core above, with the gaps in that
  table open and no control-plane work started.
- **Later submission work** — the managed control plane was implemented and
  evidenced, HarborWindow was selected as the flagship, and the architecture was
  documented against the real topology. Cloud Run and Pub/Sub did **not** become
  part of the submitted topology.
- **At `submission-freeze-2`** — the submitted tree, which is what this
  repository contains.

The frozen core was not rewritten to make the managed work possible. The exact
shape of that is a matter of record, and is stated here rather than left to a
command, because this repository is a snapshot with no history to diff:

- Between the submitted tree and `submission-freeze-2`, **no file under `app/`
  or `tests/` was modified, deleted or renamed.** That delta is empty.
- Between the submitted tree and `core-freeze-1` (`cf91551`), **exactly five
  files differ** — `app/api.py`, `app/gate.py`, `app/config.py`,
  `app/providers/routes.py`, `tests/test_api.py` — the presentation-only delta
  described at the top of this document, made deliberately after the managed
  work, to stop judge-facing strings and the routes seam describing a scenario
  choice that had already been made.

That delta is exactly why the submitted state was frozen separately instead of
the first freeze being quietly moved onto it.

## Re-checking the frozen capabilities

The freeze itself cannot be checked out here: this repository is a single
snapshot, not a history. What *can* be re-run is the thing the freeze was
protecting, unchanged — no file under `app/` or `tests/` was modified after
`submission-freeze-2`:

```bash
.venv/bin/python -m pytest tests -q   # 258 passed, 47 skipped
.venv/bin/python -m app.gate          # both scenarios 5/5 mechanical gates, SURVIVES
```

Before quoting a number from a run of your own, record the SHA it was taken at
and confirm the tree is clean. That is not ceremony: a stray untracked
`conftest.py` or `sitecustomize.py` changes what a run does while the commit it
is attributed to still looks certified, and a number taken from a moving tree
cannot be told apart from a good one at the moment it is read.

The two live lanes need credentials and a network, and their observed values
will differ from the ones recorded above. That difference is expected; a failing
*check* is not.

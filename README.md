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
one workflow.

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
| `.venv/bin/python -m pytest tests -q` | `258 passed, 47 skipped` | ~1.2s |
| `.venv/bin/python -m app.gate` | `stormslot — 5/5 mechanical gates, SURVIVES`<br>`harborwindow — 5/5 mechanical gates, SURVIVES` | ~0.1s |
| `.venv/bin/python -m app.demo harborwindow --pretty` | 16-line trace ending `COMMITTED -> harbor-plan-2` | ~0.1s |
| `.venv/bin/python -m app.demo stormslot --pretty` | 16-line trace ending `COMMITTED -> stormslot-plan-2` | ~0.1s |

**About the 47 skips.** All 47 are the same suite —
`tests/test_store_contract.py`, which runs one store contract against both the
in-memory and Firestore backends. Without a Firestore emulator the Firestore half
skips by design. Run them with the emulator (section 4) and the skips become passes.

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

### 3. Determinism check

The seeded lane must not depend on the network. This is asserted mechanically,
not by inspection: one test makes the live weather adapter unconstructable and
then runs every gate for both scenarios plus both demos through it.

```bash
.venv/bin/python -m pytest tests/test_live_gate.py -q      # 11 passed
```

### 4. Firestore contract suite (optional — needs a JDK + firebase-tools)

```bash
PATH="/opt/homebrew/opt/openjdk/bin:$PATH" firebase emulators:exec \
  --only firestore --project harbor-storm-local \
  'FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 GOOGLE_CLOUD_PROJECT=harbor-storm-local \
   .venv/bin/python -m pytest tests -q'
```

Expected: `305 passed` with **none skipped** — the emulator turns the 47 skips
above into passes. Measured on 2026-08-28 against the frozen tree submitted here
(engineering SHA `687eebfd26f64d87f3c8db49756f838dc90bc02a`): `305 passed` in 14.3s.
The same contract runs against both backends, so the in-memory store and
Firestore cannot drift on the question that decides whether a write is
authoritative.

### 5. Live Google paths (optional — needs your own Google Cloud project)

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

**Two engines carry the managed evidence, and they are not the same engine.**
Read this before reading the managed claims in either direction:

- **Engine A — `3244216260136796160`** is the actor proof: a real Gemini 3.5 Flash
  runs Harbor's bounded actor against a five-tool surface and cannot reach the
  verifier or the store. Engine A is **not** Gateway-bound, and it runs no
  governed-egress probe.
- **Engine B — `2414533581910048768`** is the governed-egress proof: Agent Identity,
  Agent Gateway, IAP and per-endpoint IAM decide what that identity may reach.
  Engine B does **not** run the actor.

Both halves are demonstrated. They are **not demonstrated end-to-end on one
engine**, and nothing here claims one engine did both — so the managed actor path
and the governed egress path should be read as two proofs, not as one continuous
one. [`EVIDENCE.md`](EVIDENCE.md) tags each managed row with the engine it came
from; why the two were not converged is in
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
| `test_log_scrubber.py` | evidence capture redacts credentials before writing, and fails closed |

## Frozen core

This repository is a single-commit snapshot of a frozen tree. There is no history
here to diff against, so the provenance below is stated as fact rather than left
to be re-derived:

- **The pre-GEAP operational core was frozen first**, at engineering commit
  `cf91551` (named `core-freeze-1`): fencing, claim-contention classification, the
  deterministic verifier, real Gemini 3.5 execution, live Google Weather in both
  scenarios, and deterministic replay.
- **Every file submitted here existed at engineering SHA
  `687eebfd26f64d87f3c8db49756f838dc90bc02a` and is content-identical to it,
  apart from the judge-facing prose (`README.md`, `EVIDENCE.md`, `docs/`) and
  `.gitignore`, which were finalized after that SHA for this snapshot.**
- **The delta between them, in `app/` and `tests/`, is five files and is
  presentation only:** `app/api.py`, `app/gate.py`, `app/config.py`,
  `app/providers/routes.py`, `tests/test_api.py` — docstrings, two judge-facing
  strings and one exception message that still said the scenario choice had not
  been made, plus the test that pinned the old wording. `app.gate --json` is
  byte-identical across that change and no verification logic differs.
- **Everything else added after the core freeze is additive**, not a rewrite of
  frozen code: the managed-runtime adapter `app/geap/` and
  `tests/test_log_scrubber.py`. No frozen file was modified to make the managed
  Google layer work.

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
Identity-Aware Proxy, and Google Cloud IAM. These surfaces are demonstrated as
two proofs on two engines — the actor on one, governed egress on the other, as
set out in [Reproduce it](#reproduce-it) — and no single engine demonstrates
the combined path.

Earlier exploration considered Cloud Run, Pub/Sub, Google Routes, and other
extensions. They are not part of the submitted architecture.

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

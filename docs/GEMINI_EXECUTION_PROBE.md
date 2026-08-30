# The real-model execution probe

Until this probe existed, every statement this repo made about agents was a
statement about a shape. `app/agents/actors.py` constructed `LlmAgent`s, the
scopes were disjoint, `tests/test_agents.py` asserted that no toolkit carried a
commit tool — and no model had ever run. "No agent can commit" was true of a
diagram.

`app/agents/probe.py` is the narrow proof that closes that gap, and it is
deliberately narrow. It proves one thing:

> A real Gemini model operated as a bounded Harbor actor, and Harbor — not the
> model — decided whether the transition it proposed became truth.

It does not prove live weather, managed deployment, Agent Runtime or Agent
Identity. This is a **local** run: the model is real and remote, but the runtime
around it is this repository executing locally, not Google's managed Agent
Runtime. Wherever those things are established in this package they are
established by the deployed-engine evidence and the live weather lane — see
`EVIDENCE.md` — and this probe is not evidence for any of them. What it
establishes, and all it establishes, is the boundary: a real model driving
Harbor's real toolkit still cannot verify or commit.

## Run it

```bash
GOOGLE_GENAI_USE_VERTEXAI=true \
GOOGLE_CLOUD_PROJECT=harbor-storm-fleet \
GOOGLE_CLOUD_LOCATION=global \
.venv/bin/python -m app.agents.probe --json
```

Add `STATE_BACKEND=firestore FIRESTORE_EMULATOR_HOST=localhost:8080` to run the
same probe against a Firestore-backed store. `--out FILE` writes the full
evidence JSON; `--run-prefix` pins the run id, which on Firestore is a document
name — the default is a fresh one per probe, because reusing an id resumes that
world instead of seeding a new one.

Exit status is 0 only if both cases reached the outcome they were supposed to.

## What runs

    seeded HarborWindow world  (weather measured, work items opened,
                                cargo + harbormaster constraints published)
        │                       `window` is left OPEN on purpose
        ▼
    window-agent  = existing ActorScope + existing ActorToolkit
        │           five tools: claim_work, read_facts, report_constraint,
        │                       propose_plan, read_trace
        ▼
    ADK Runner  (LOCAL process — not the managed Agent Runtime)
        │        ──► REAL Gemini 3.5+ on Vertex AI
        │        ──► the model calls those tools itself
        ▼
    propose_plan  → CandidatePlan bound to the revision the actor observed
        │
        ▼
    app/core/verify.py : verify_and_commit
        │           deterministic, outside every agent's reach,
        │           recomputes from authoritative facts
        ▼
    PLAN_VERIFIED + PLAN_COMMITTED     or     PLAN_REJECTED

**Local ADK, real model.** That `ADK Runner` row is a local process — the command
under *Run it* — calling Vertex AI over the network.
The model is real; the execution environment is not the managed Agent Runtime, so
nothing here should be read as the managed-actor proof. The managed-actor
evidence is a different artifact set on a deployed Reasoning Engine
(`docs/GEAP_D0_D1.md`, `EVIDENCE.md`), and that engine carries no gateway
binding: the actor path and the governed-egress path are **not demonstrated
end-to-end on one engine**. This probe touches neither of those questions. It
tests the authority boundary, locally, against a real model.

The model gets its safety limits from `read_facts` and everything else from
`read_trace`. It cannot see `sailing_slots` — that is `harbormaster-agent`'s
scope — so it reads the constraint the harbormaster published. The briefing
names the job and the shape of a plan action. It contains no hour, no forecast
and no limit: the departure hour has to come out of the model's own reads, or
the run proves nothing.

## The two cases

**accept.** Undisturbed world. If the candidate survives independent
recomputation, Harbor commits it. The trace attributes the claim, the constraint
and the proposal to `window-agent`, and the verdict and the commit to
`verifier`. Those are different actors on the record because they are different
actors in fact.

**refuse — the control.** Identical up to the proposal. Then real external truth
arrives through `store.advance_revision`, the same path a Pub/Sub weather event
takes, and the world moves to a new revision. The candidate was bound to the old
one, so `verify_and_commit` refuses it:

    PLAN_REJECTED  verifier  stale: plan bound to revision 0, world is at revision 1

`committed_plan_id` stays `None`. No `PLAN_VERIFIED` is ever written.

The point of the control is *which* refusal this is. The model's plan was not
sloppy — it was the correct answer to the world it read. It is refused because
the model does not get to decide when its own reasoning is still true. The probe
additionally reports what the verifier would say about the plan's content under
the new forecast (`harbor wind 44 kph over limit at hour 14`), purely as
evidence that the refusal was protecting something real. That verdict changes
nothing: staleness had already ended the question.

## The two guards

Both are in `app/agents/execution.py`, and both fail closed, because both catch
the kind of error that is otherwise caught only by someone remembering to look.

**`require_model_floor`** refuses any model below Gemini 3.5, including
`gemini-3-flash-preview` — which reads as `3`, and an unnumbered minor is 0, not
a wildcard. An older model that quietly answers is worse than an error: the run
succeeds and the claim made about it is false. `DEFAULT_MODEL` is the floor for
the same reason. `tests/test_model_execution.py` runs the negative control:
`gemini-2.5-flash` — the previous default — must raise.

**`assert_no_authority_tools`** audits the function declarations in the actual
outgoing `LlmRequest`, at the moment it is sent, not this repo's own
`tool_names()`. Auditing the toolkit would only re-check what `app/agents/tools.py`
already promises about itself.

## Tests are not this proof

`tests/test_model_execution.py` uses a scripted `BaseLlm` and proves the adapter
is wired correctly — event parsing, plan-id recovery, the guards. What it cannot
establish is the only thing this probe exists to establish — that a real model,
choosing its own tool calls, stays inside the boundary — and no test can, because
a double that answers instantly is exactly what such a proof has to exclude. A
green suite plus a red probe means the wiring is right and the claim is still
false.

## Collecting evidence from this probe

Run the probe by hand and record the HEAD SHA and the capture time alongside the
output. That is not bookkeeping.
**Evidence collected from a moving tree is void.**
Before any run whose numbers are quoted, the tree must be a known committed SHA
with no uncommitted or untracked changes — an untracked `conftest.py` or
`sitecustomize.py` can change what a run does while the committed SHA still looks
certified. Every quoted number is stamped with the HEAD SHA and the capture time.
The precondition check fails closed: if it does not hold, evidence collection
stops rather than proceeding with a caveat.

The reason that is a gate rather than a habit is the whole point.

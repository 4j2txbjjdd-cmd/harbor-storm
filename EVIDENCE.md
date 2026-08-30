# Evidence index

One page, one row per claim. This is an **index, not another architecture
document** — every row points somewhere that already exists.

Two Reasoning Engines carry the managed evidence, and they are **not the same
engine**:

| | engine | what it proves | gateway-bound |
|---|---|---|---|
| **A** | `3244216260136796160` | the actor proof — a real Gemini runs Harbor's bounded actor and cannot reach authority | **no** |
| **B** | `2414533581910048768` | the governed-egress proof — Google decides what that identity may reach | **yes** |

Engine A runs no governed-egress probe; Engine B runs no actor. Nothing here
claims one engine did both, and the managed actor path and the governed egress
path are **not demonstrated end-to-end on one engine**. Why they were not
converged is in [`docs/GEAP_D0_D1.md`](docs/GEAP_D0_D1.md).

**LIVE** = reproducible against Google right now (needs your own project).
**COMMITTED** = a file in this repository.
**LOCAL** = runs offline with no credentials.
**STATED** = a provenance fact recorded here rather than re-derivable here; this
repository publishes the frozen tree without its engineering history, so the
freeze cannot be diffed against here. The commits this repository does carry
all post-date first publication and are declared additions (prose, the
sentinel, ReliefRun, the portal) — see the provenance rows below.

## Scenario and scope

| claim | what it proves | primary artifact / doc | live or committed |
|---|---|---|---|
| HarborWindow is the flagship | the demo scenario, and the one the narrative is built on | [`docs/WHY_HARBOR.md`](docs/WHY_HARBOR.md) | COMMITTED |
| StormSlot is transfer evidence | the substrate is not specific to one workflow — both scenarios pass the same gates | `.venv/bin/python -m app.gate` → both `5/5 mechanical, SURVIVES` | LOCAL |

## The actor, and the authority it does not have  ·  ENGINE A

| claim | what it proves | primary artifact / doc | live or committed |
|---|---|---|---|
| Bounded actor scopes | three actors with genuinely disjoint `visible_facts`; `window-agent` cannot read `sailing_slots` | `app/agents/actors.py` (`ACTOR_SCOPES`); `tests/test_agents.py` | COMMITTED + LOCAL |
| Gemini 3.5 Flash executed | a real managed model ran — the version is server-reported, not asserted by me | [`geap/d1_shift_accept.json`](geap/d1_shift_accept.json) → `model_versions_reported: ["gemini-3.5-flash"]` | COMMITTED |
| Exactly five tools | the whole model-facing surface, read from the outgoing `LlmRequest`: `claim_work`, `read_facts`, `report_constraint`, `propose_plan`, `read_trace` — no model-facing verify, no commit, no revision advance, no direct authoritative Firestore mutation | [`geap/d1_shift_accept.json`](geap/d1_shift_accept.json) → `tools_offered_to_model` | COMMITTED |
| No verify / commit / peer-transfer authority | the model cannot reach the verifier or the store, audited on the request rather than promised by the toolkit | `app/agents/execution.py::assert_no_authority_tools`; `tests/test_agents.py` | COMMITTED + LOCAL |
| CandidatePlan is bound to a revision | a proposal carries the revision it was built from | [`geap/d1_shift_accept.json`](geap/d1_shift_accept.json) → `candidate_plan.basis_revision` | COMMITTED |

## The membrane: what is allowed to become true

| claim | what it proves | primary artifact / doc | live or committed |
|---|---|---|---|
| Deterministic verifier | authoritative state moves only after a recomputation from authoritative facts; model-supplied metrics are ignored | `app/core/verify.py`; `tests/test_verification_membrane.py` | COMMITTED + LOCAL |
| Stale-plan rejection | a *correct* plan is refused because the world moved | [`geap/d1_shift_control.json`](geap/d1_shift_control.json) *(engine A)* → `candidate_plan.rejection_reason: "stale: plan bound to revision 0, world is at revision 1"`; `PLAN_REJECTED` at `seq 14` | COMMITTED |
| No stale commit | the refusal actually held — nothing was written | same file *(engine A)* → `authoritative_state.committed_plan_id: null`, and no `PLAN_VERIFIED` anywhere in the trace | COMMITTED |
| State revision / fencing | the revision fence and the mandatory fence are enforced by the store, not by convention | `tests/test_stale_plan.py`; `tests/test_fence_mandatory.py` | LOCAL |
| Event idempotency | replayed events do not double-apply | `tests/test_event_idempotency.py` | LOCAL |
| Replayable trace integrity | the event log is the record, and it is internally consistent | `tests/test_trace_integrity.py` | LOCAL |
| Firestore is authoritative | the committed transition survives the process that made it — independent readback from a different machine | [`geap/d1_shift_accept.json`](geap/d1_shift_accept.json) *(engine A)* → `authoritative_state.committed_plan_id: harbor-agent-plan-1`, run `geap-d1-shift-4` | COMMITTED + LIVE |

## Google control plane  ·  ENGINE B unless noted

*How the egress rows are attributed: the gateway log records name the gateway and
the registry endpoint, and **do not carry an engine id**. Their attribution to
engine `2414533581910048768` comes from the controlled invocation and its time
window together with the deploy record, not from anything in the log itself. `authz=ALLOWED` / `authz=DENIED` below is
shorthand for the record's `jsonPayload.authzPolicyInfo.result` field.*

| claim | what it proves | primary artifact / doc | live or committed |
|---|---|---|---|
| Agent Runtime | Harbor's actor runs as a managed Reasoning Engine | [`geap/d1_deployed_v4.json`](geap/d1_deployed_v4.json) *(engine A)*; [`docs/GEAP_D0_D1.md`](docs/GEAP_D0_D1.md) | COMMITTED |
| Agent Identity *(engine `1562121799313915904` — the D0 identity probe, which is neither A nor B)* | a control-plane GET on that engine returns `identityType: AGENT_IDENTITY` and an `effectiveIdentity` bound to that one Reasoning Engine, and the `spec` it returns carries exactly those two keys — no `serviceAccount` field. The negative is scoped to that readback: it is what the control plane returns for this engine, not a claim about every engine or principal in the project | [`geap/d0_readback.json`](geap/d0_readback.json) | COMMITTED + LIVE |
| Agent Registry | two registered endpoints. Registration decides whether a destination is *known*; it is **not** an authorization allowlist — egress is authorized by a per-endpoint IAP IAM grant, which is why a registered destination with no `iap.egressor` binding is still denied | [`geap/iap_endpoint_policies.json`](geap/iap_endpoint_policies.json); live `agentregistry.googleapis.com` | COMMITTED + LIVE |
| Governed egress runs on its own engine | the gateway-bound engine is `2414533581910048768`, distinct from the actor engine | [`geap/d1_deployed_gw5.json`](geap/d1_deployed_gw5.json); `docs/ARCHITECTURE.md` claim map rows tagged **[B]** | COMMITTED + LIVE |
| Weather **allowed** | a registered destination with `roles/iap.egressor` returns a real forecast | [`geap/d1_egress_rotated.json`](geap/d1_egress_rotated.json) (200, `server: ESF`, no IAP header) + [`geap/gw_logs_rotated.json`](geap/gw_logs_rotated.json) (`authz=ALLOWED`) | COMMITTED |
| Unauthorized destination **denied** | registered but with no `iap.egressor` binding → refused by IAP before the destination | same pair — `403`, `x-goog-iap-generated-response: true`, `authz=DENIED`; and in [`geap/iap_endpoint_policies.json`](geap/iap_endpoint_policies.json) the cargo endpoint's IAP IAM policy carries no bindings at all | COMMITTED |
| Unregistered destination **denied** | not in the Agent Registry → refused, with no registry attribution in Google's log | same pair — *"…unregistered in the Agent Registry."* | COMMITTED |
| The gateway is the only variable | the same probe on a **non**-gateway engine (`8409845032730755072` — neither A nor B) reaches both denied destinations freely | [`geap/d1_egress_nogw2.json`](geap/d1_egress_nogw2.json) — rows 2–3 `ALLOWED 200` | COMMITTED |
| `failOpen: false` | authorization is configured fail-closed. **A configuration property, not a demonstrated outage** | [`geap/failclosed/authz_extension_readback.json`](geap/failclosed/authz_extension_readback.json); [`docs/GATEWAY_FAIL_CLOSED.md`](docs/GATEWAY_FAIL_CLOSED.md) | COMMITTED + LIVE |
| IAM names the agent principal | the granted roles are bound to per-engine `principal://` members | [`geap/iam_project_agent_principals.json`](geap/iam_project_agent_principals.json) | COMMITTED + LIVE |
| Cloud Trace | Agent Runtime emits spans for the managed actor runs *(actor-side spans; engine B runs no actor and emits none of them)* — `generate_content gemini-3.5-flash` and one `execute_tool` span per bounded tool. **Google's instrumentation, not Harbor's**; OpenTelemetry is not claimed as a Harbor-authored integration | live `cloudtrace.googleapis.com`; [`docs/SUBMISSION_FIELDS.md`](docs/SUBMISSION_FIELDS.md) | LIVE |

## Provenance

| claim | what it proves | primary artifact / doc | live or committed |
|---|---|---|---|
| The core was frozen before the managed work | the pre-GEAP operational core was frozen at engineering commit `cf91551` (`core-freeze-1`) before any Google managed-platform work began, so that layer had to be built *around* the verification core rather than by rewriting it — which is only checkable because the baseline is a fixed, named commit | [`docs/CORE_FREEZE.md`](docs/CORE_FREEZE.md) | STATED |
| The submitted tree | the code and evidence here are content-identical to engineering SHA `687eebfd26f64d87f3c8db49756f838dc90bc02a`; the judge-facing prose (`README.md`, this file, `docs/`) and `.gitignore` were finalized after that SHA for this snapshot. The delta from the frozen core, across `app/` and `tests/`, is **five** files — `app/api.py`, `app/gate.py`, `app/config.py`, `app/providers/routes.py`, `tests/test_api.py` — and is presentation only: `app.gate --json` is byte-identical across the change and no verification logic differs. Everything else added since the freeze is additive: `app/geap/` with `tests/test_log_scrubber.py` at that SHA, and — after first publication — the sentinel (`app/sentinel.py`, `tests/test_sentinel.py`, engineering SHA `4296831`), ReliefRun (`app/scenarios/reliefrun.py`, `app/relief_demo.py`, `tests/test_reliefrun.py`, engineering SHA `893f759`), and the portal (`app/portal.py`, `app/static/relief.html`, `tests/test_portal.py`, engineering SHA `9803336`). No frozen file was modified by any of them | [`docs/CORE_FREEZE.md`](docs/CORE_FREEZE.md) | STATED |
| Independent verification | a claim was not accepted on the strength of the run that produced it: evidence was re-collected from a detached checkout of a recorded SHA, against live cloud state, before the change it justified was accepted. The review history itself lives in the private engineering repository and is not part of this snapshot | see *Evidence is tied to fixed repository state*, below | STATED |
| Cloud resource inventory | why each live engine exists and which evidence file it produced — the part no API can answer | [`docs/GEAP_CLOUD_INVENTORY.md`](docs/GEAP_CLOUD_INVENTORY.md) | COMMITTED |

### Evidence is tied to fixed repository state

Every number quoted in this package — test counts, gate results, trace excerpts,
control-plane readbacks — was collected against a known committed SHA with no
uncommitted and no untracked changes in the tree, and is stamped with that HEAD
SHA and the capture time.

**Evidence collected from a moving tree is void.** The reason is specific rather
than ceremonial: an untracked `conftest.py` or `sitecustomize.py` can change what
a run does while the committed SHA still looks certified. A measurement taken
against state that is changing underneath it is indistinguishable from a correct
one at the moment it is read; it only becomes distinguishable later, by which
point it has already been quoted.

So the precondition check is mechanical and fails closed. If a precondition does
not hold, evidence collection stops rather than proceeding with a caveat — a
caveat being exactly the thing that stops travelling with a number once the
number gets quoted.

**A control that depends on someone being suspicious is not a control.**

## Reproduce the local half in about a minute

```bash
.venv/bin/python -m pytest tests -q                    # 287 passed, 47 skipped
.venv/bin/python -m app.gate                           # both scenarios 5/5, SURVIVES
.venv/bin/python -m app.demo harborwindow --pretty     # 16-line trace, COMMITTED -> harbor-plan-2
.venv/bin/python -m app.demo stormslot --pretty        # 16-line trace, COMMITTED -> stormslot-plan-2
.venv/bin/python -m app.sentinel harborwindow --ticks 3 --interval 0 --disrupt-at-tick 2
                                                       # wall-clock ticks: COMMIT_REVOKED -> replan -> harbor-plan-3
.venv/bin/python -m app.relief_demo --disrupt --pretty # third instantiation: barrier-lake alert revokes the
                                                       # committed mission, re-commits -> relief-plan-3
```

Full instructions, including the Firestore emulator run that turns the 47 skips
into passes, are in [`README.md`](README.md).

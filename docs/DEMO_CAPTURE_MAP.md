# Demo capture map

*Every entry marked `RECHECK: yes` was re-confirmed on **2026-08-28** by read-only
control-plane GET, `gcloud`, Firestore readback and local runs. Each now records
what was actually observed.*

**Recheck summary (2026-08-28).** All eleven RECHECK items confirmed, and the two
SHA-bound checks (#16b, #17) were re-taken at the frozen engineering SHA
`687eebfd26f64d87f3c8db49756f838dc90bc02a`: the seeded suite returns
`258 passed, 47 skipped`, the emulator suite `305 passed` with none skipped, and
the frozen-core delta against the submission freeze is **empty**. Every evidence
artifact a shot points at is present in this repository, except where a shot says
in so many words that it is not. No engine, binding or run referenced by any shot
has disappeared.

**Addendum (2026-08-30).** The wall-clock sentinel (`app/sentinel.py` with
`tests/test_sentinel.py`) and the ReliefRun instantiation
(`app/scenarios/reliefrun.py`, `app/relief_demo.py`, `tests/test_reliefrun.py`)
were added after that recheck, both additive, followed by the web portal
(`app/portal.py`) and its Cloud Run deployment. The seeded suite now returns
`287 passed, 47 skipped` and the emulator suite `334 passed` with none
skipped; every dated figure above is the pre-addition measurement.

## Read this first

**Two lanes, and do not mix them.**

- `SAFE LIVE` — deterministic, offline or ~seconds, safe to run unedited on
  camera. This is where "unedited live execution" is cheap and persuasive.
- `PRE-CAPTURE` — minute-scale model latency, IAM propagation, or nondeterministic
  cloud timing. Record beforehand and narrate. Attempting these live will strand
  you: a transient Vertex `429 RESOURCE_EXHAUSTED` already happened once in this
  build.

**Weather is calm right now.** A live weather run commits at the *booked* hour,
which is correct and shows weather as decorative. The "weather changes an
operational decision" beat only lands on the **seeded** lane. Use seeded for that
claim, live only for "these are real Google observations."

**Gateway log rule: HTTP/1.1 records only.** HTTP/2 (gRPC) records show
`authzPolicyInfo=ALLOWED` at the tunnel layer while the client was refused
in-stream. Putting one on screen would show ALLOWED beside a denied request.

**Never screenshot a raw Cloud Logging dump.** Capture through
`geap/capture_gateway_logs.py`, which sanitises before writing and refuses to
write if anything credential-shaped survives.

---

## 1. The operational problem
- **claim** Three parties own three constraints; none can authorise a departure alone.
- **action** Narration over the animation. No terminal.
- **artifact** `docs/WHY_HARBOR.md`
- **duration** ~30s of video
- **lane** PRE-CAPTURE (animation)
- **secret** none
- **RECHECK** no

## 2. Three bounded specialist actors
- **claim** Genuinely disjoint information; `window-agent` cannot read `sailing_slots`.
- **action** `.venv/bin/python -c "import json;from app.agents import describe_actors;print(json.dumps(describe_actors('harborwindow'),indent=2))"`
- **visible** three actors, `visible_facts` disjoint, `"can_commit": []`
- **duration** ~1s · **lane** SAFE LIVE
- **screen** terminal
- **artifact** `app/agents/actors.py` (ACTOR_SCOPES)
- **secret** none · **RECHECK** no

## 3. Seeded HarborWindow — a rejection *and* a commit in 16 lines
- **claim** The booked 12:00 sailing is refused on a named weather reason, replanned, and 14:00 commits.
- **action** `.venv/bin/python -m app.demo harborwindow --pretty`
- **visible** 16-line numbered trace. Critically it shows the **whole membrane**, not just a success:
  `PLAN_PROPOSED window-agent` → `PLAN_REJECTED verifier` (named reason) → `REPLAN_STARTED`
  → `PLAN_VERIFIED` → `PLAN_COMMITTED` → `SAILING_RESCHEDULED 12 → 14`, footer `COMMITTED -> harbor-plan-2`
- **duration** **0.09s cold, 0.04s warm** (measured, 3 runs) · **lane** SAFE LIVE
- **why this shot** it is the cheapest complete story in the build — refusal, replan and commit in one screen, offline, sub-second
- **screen** terminal, whole trace fits
- **artifact** deterministic; no network
- **secret** none · **RECHECK** no

## 4. Gemini 3.5 actually running inside managed Agent Runtime
- **claim** A real model executed in Agent Runtime, not locally.
- **action** PRE-RECORD `PYTHONPATH=. .venv/bin/python geap/d1_invoke.py --engine <ACTOR_ENGINE> --method run_shift --kwargs '{"run_id":"demo-N"}'`
- **visible** `model_versions_reported: ["gemini-3.5-flash"]`, `model_tool_calls`, `tools_offered_to_model`
- **duration** 60-120s · **lane** PRE-CAPTURE (model latency + 429 risk)
- **engine** `3244216260136796160` — the **actor** proof engine. It demonstrates the
  managed Gemini actor path and is **not** Gateway-bound. Governed egress is proved on a
  different engine (#11-13), which does not run the actor. The complete actor-plus-Gateway
  path is **not demonstrated end-to-end on one engine**; say so wherever the managed path
  is claimed.
- **artifact** `geap/d1_shift_accept.json`
- **secret** none · **RECHECK** ✅ confirmed 2026-08-28 — engine `3244216260136796160` is PRESENT in the live
  `reasoningEngines` list (control-plane GET) and holds `roles/aiplatform.user` + `roles/datastore.user`

## 5. The model's authority is bounded
- **claim** Five tools, no verify, no commit — audited on the outgoing request.
- **action** show `tools_offered_to_model` from the same JSON
- **visible** `claim_work, read_facts, report_constraint, propose_plan, read_trace`
- **duration** static · **lane** SAFE LIVE (file on screen)
- **artifact** `geap/d1_shift_accept.json`
- **note on screen** "read from the outgoing LlmRequest, not asserted"
- **secret** none · **RECHECK** ✅ confirmed 2026-08-28 — `tools_offered_to_model` in the artifact reads exactly
  `['claim_work', 'read_facts', 'report_constraint', 'propose_plan', 'read_trace']`; `model_versions_reported: ['gemini-3.5-flash']`

## 6. CandidatePlan → deterministic verifier → Firestore commit
- **claim** The model proposed; Harbor decided; Firestore holds the result.
- **action** show trace lines from the same run
- **visible** `PLAN_PROPOSED window-agent` → `PLAN_VERIFIED verifier` → `PLAN_COMMITTED verifier` (different actors)
- **duration** static · **lane** SAFE LIVE
- **artifact** `geap/d1_shift_accept.json`
- **field path** the committed id is `authoritative_state.committed_plan_id` (= `harbor-agent-plan-1`), **not** a
  top-level `committed_plan_id`. There is no top-level field of that name; do not point a judge at one.
- **secret** none · **RECHECK** ✅ confirmed 2026-08-28 — all three events present with those three actors

## 7. Firestore is authoritative — independent readback
- **claim** Truth is in Firestore, not in the agent's answer.
- **action** `GOOGLE_CLOUD_PROJECT=harbor-storm-fleet .venv/bin/python -c "from app.core.firestore_store import FirestoreStateStore; s=FirestoreStateStore('geap-d1-shift-4',project='harbor-storm-fleet',database='harbor'); st=s.refresh(); print(st.committed_plan_id); [print(e['seq'],e['kind'],e['actor']) for e in s.trace()]"`
- **visible** `committed_plan_id = harbor-agent-plan-1` and the trace, read from a *different machine* than the one that wrote it
- **duration** ~3s · **lane** SAFE LIVE
- **secret** none · **RECHECK** ✅ confirmed 2026-08-28 — run `geap-d1-shift-4` still exists; independent readback returned
  `committed_plan_id = harbor-agent-plan-1` and a 13-event trace ending `PLAN_PROPOSED` → `PLAN_VERIFIED` → `PLAN_COMMITTED`

## 8. Stale-plan rejection — the strongest single beat
- **claim** The model was *right*, and Harbor refused it anyway because the world moved.
- **action** PRE-RECORD `... --method run_shift --kwargs '{"run_id":"demo-control-N","stale_control":true}'`
- **visible** `verifier_decision: "rejected"`; the `PLAN_REJECTED` trace event (seq 14) carries
  `"reason": "stale: plan bound to revision 0, world is at revision 1"`; `authoritative_state.committed_plan_id: null`;
  no `PLAN_VERIFIED` anywhere in the trace
- **⚠ field name, stated precisely.** There is no **top-level** `rejection_reason`; an earlier note in this
  map over-corrected that into "there is no such field", which is also wrong. The artifact carries the reason in
  three places, all verified in `geap/d1_shift_control.json`:
  `candidate_plan.rejection_reason` = `"stale: plan bound to revision 0, world is at revision 1"`,
  the `PLAN_REJECTED` trace event at `seq 14` whose payload is `{"reason": …, "plan_id": "harbor-agent-plan-1"}`,
  and `verifier_decision: "rejected"` at the top level. Point the camera at the trace event; cite
  `candidate_plan.rejection_reason` if a judge asks where the plan itself records it.
- **duration** 60-120s · **lane** PRE-CAPTURE
- **artifact** `geap/d1_shift_control.json`
- **narration** "Gemini proposed the correct plan. Harbor still said no."
- **secret** none · **RECHECK** ✅ confirmed 2026-08-28 — artifact re-read: decision `rejected`, no `PLAN_VERIFIED`, commit null

## 9. Agent Identity — control-plane readback
- **claim** Google issued the identity; I did not configure one.
- **action** engine is **`1562121799313915904`** — pinned, not `<ENGINE>`; see the credential note below.
  ```bash
  TOKEN=$(gcloud auth print-access-token)
  curl -sS -H "Authorization: Bearer $TOKEN" \
    https://us-central1-aiplatform.googleapis.com/v1beta1/projects/801248256447/locations/us-central1/reasoningEngines/1562121799313915904 \
    | python3 -c "import json,sys; spec=json.load(sys.stdin)['spec']; print(json.dumps({k:v for k,v in spec.items() if k in ('identityType','effectiveIdentity')}, indent=2)); print('serviceAccount key present:', 'serviceAccount' in spec)"
  ```
- **visible** `identityType: AGENT_IDENTITY`, `effectiveIdentity: agents.global.org-…/reasoningEngines/1562121799313915904`, and `serviceAccount key present: False`
- **⚠ CREDENTIAL** Do **not** pipe a whole engine through `json.tool` on camera. A
  Reasoning Engine's `spec.deploymentSpec.env` can carry deployment environment values
  in cleartext, so a full GET can render a live credential on screen. Project the fields
  you need — the projection above renders only the two identity fields. Engine
  `1562121799313915904` is the one `geap/d0_readback.json` documents and is the correct
  choice for this shot.
- **duration** ~2s · **lane** SAFE LIVE
- **artifact** `geap/d0_readback.json`
- **secret** the access token is in the command — **do not show the token; scroll it off or use a prepared alias**
- **RECHECK** no

## 10. Exact-principal IAM enforcement
- **claim** Harbor's grants name per-engine agent-identity principals, and the principal the runtime presents is one of them.
- **action** show the two bindings **in full** — do not filter on `agents.global`, that hides the one member that complicates the claim and surfaces a platform-default `principalSet` that contradicts it.
  ```bash
  gcloud projects get-iam-policy harbor-storm-fleet --format=json \
    | python3 -c "import json,sys;p=json.load(sys.stdin)
  for b in p['bindings']:
      if b['role'] in ('roles/datastore.user','roles/aiplatform.user'):
          print(b['role']); [print('   ',m) for m in b['members']]"
  ```
- **visible** `roles/aiplatform.user` → seven `principal://…/reasoningEngines/<id>` members and nothing else. `roles/datastore.user` → those same seven **plus** `serviceAccount:801248256447-compute@developer.gserviceaccount.com`.
- **narration** say the extra member out loud: it is the Compute Engine default service account, it predates this work, Harbor did not grant it, and it is not what the Runtime authenticates as. Do **not** claim that all Firestore access is exclusively agent-identity-bound — that member is a second datastore principal and it exists. The claim is narrower and stronger: the agent identity is *named and evaluated*. The `403 PERMISSION_DENIED` leg in `geap/firestore_iam_enforcement_legs.json` is what demonstrates it — a named principal reached Firestore and IAM refused it. The claim is not that the binding is exclusive.
- **duration** ~3s · **lane** SAFE LIVE
- **secret** none · **RECHECK** no

## 11-13. Governed egress — the discrimination shot
- **claim** Same identity, same gateway, same transport. One destination permitted, one refused, one unregistered.
- **action** `PYTHONPATH=. .venv/bin/python geap/d1_invoke.py --engine <GATEWAY_ENGINE> --method egress_probe --kwargs "$(cat geap/targets.json)"`
- **visible**
  - weather → `DESTINATION_REACHED`, HTTP **200**, real forecast, `server: ESF`, **no** IAP header
  - cargo → `GOVERNANCE_DENIED`, HTTP **403**, `x-goog-iap-generated-response: true`, *"Egress request is not authorized."*
  - unregistered → `GOVERNANCE_DENIED`, HTTP **403**, *"…unregistered in the Agent Registry."*
- **⚠ say precisely** the cargo destination *is* registered and is still refused. Registry
  registration is not an authorization allowlist; per-endpoint IAP IAM is what governs
  egress, and the cargo endpoint simply holds no binding. The third arm is refused for a
  different reason — it is not in the Registry at all. Two different mechanisms, two
  different 403s.
- **duration** ~6s · **lane** **SAFE LIVE** — *run this one live, it is the money shot*
- **engine** `2414533581910048768` — the **egress** proof engine (rotated key + weather
  egressor + fixed classifier). It demonstrates governed egress and does **not** run the
  actor; the managed actor path is proved on engine `3244216260136796160` (#4). The two
  halves are not demonstrated end-to-end on one engine, and the cut must not imply they are.
- **artifact** `geap/d1_egress_rotated.json`
- **secret** probe now emits `url` path-only; the key is never echoed. **Do not** hand-construct the weather URL on screen.
- **RECHECK** ✅ confirmed 2026-08-28 — `gcloud iap web get-iam-policy --resource-type=agent-registry
  --endpoint=agentregistry-00000000-0000-0000-3e88-93525e6955a6 --region=us-central1` returns `roles/iap.egressor`
  with engine **2414533581910048768** among exactly four principals, etag `BwZaFy2y8tk=`. The cargo endpoint
  (`…-dffe-b2901c86a27a`) returns `{"etag": "ACAB"}` — **no bindings at all**. Byte-for-byte the state recorded in
  `geap/iap_endpoint_policies.json`. The engine is also still `identityType: AGENT_IDENTITY`, has **no**
  `serviceAccount` key, and still carries `agentGatewayConfig → harbor-egress-gw`.

## 14. Google's own record of that decision
- **claim** Not my interpretation — Google's own gateway log.
- **action** time-bound the capture so the output is exactly the triad and nothing else. `--limit 6` returned six rows
  while the narration said three; that mismatch is what this shot must not have.
  ```bash
  PYTHONPATH=. .venv/bin/python geap/capture_gateway_logs.py \
    --filter 'resource.type="networkservices.googleapis.com/Gateway" AND timestamp>="2026-08-28T10:43:00Z" AND timestamp<="2026-08-28T10:44:30Z"' \
    --out /tmp/demo_gw.json --limit 3 --freshness 7d
  ```
  For a fresh capture taken live straight after 11-13, replace the two timestamps with that probe's own window.
- **visible** the script prints `wrote /tmp/demo_gw.json: 3 record(s), sanitised before write` and then **exactly three**
  HTTP/1.1 rows — weather `GET 200` with the weather registry endpoint; cargo `GET 403` with the cargo endpoint;
  bigquery `GET 403` with `registry: (none - unregistered)`; `cert: true` on all three.
- **duration** ~5s · **lane** SAFE LIVE (immediately after 11-13)
- **artifact** `geap/failclosed/http_triad_gateway.json` — **exactly three records, all HTTP/1.1**, the same three
  `(host, status)` pairs the command prints. Use this one on screen.
- **⚠ attribution** the gateway records do **not** themselves name the calling engine. These
  rows are attributed to the egress engine by the controlled invocation and its time window
  — which is exactly why the filter is bounded to that window and why `--limit` alone is not
  enough. Narrate it that way; do not imply the log identifies the caller.
- **⚠ do not show** `geap/gw_logs_rotated.json` for this beat. It holds **six** HTTP/1.1 records: the same triad plus
  three unrelated rows from the deploy nine minutes earlier — an `iamcredentials.mtls.googleapis.com` 403 and two
  `CONNECT 240.0.0.2:443` 200s with no `authzPolicyInfo`. Narrating "three" over six rows is the exact defect this
  shot was rewritten to remove. It remains valid evidence, just not this shot's evidence.
- **secret** **must** go through this script — it sanitises before write and fails closed. Never `gcloud logging read`
  on camera. Verified: the command above writes 3 records with zero credential-shaped matches.
- **RECHECK** ✅ re-confirmed — the command was run as written and produced 3 HTTP/1.1 records whose
  `(host, status)` set is identical to `geap/failclosed/http_triad_gateway.json`.

## 15. Fail closed
- **claim** If the authorization service cannot decide, access does not fall through.
- **action** `curl -sS -H "Authorization: Bearer $TOKEN" https://networkservices.googleapis.com/v1beta1/projects/harbor-storm-fleet/locations/us-central1/authzExtensions/harbor-iap-authz`
- **visible** `service: iap.googleapis.com`, `iapPolicyVersion: V1`, and **`failOpen` absent** — proto3 omits false booleans
- **duration** ~2s · **lane** SAFE LIVE
- **⚠ FRAMING** This is a **configuration property, not a demonstrated behaviour.** I did not induce an IAP outage; outage behaviour was never experimentally exercised. Say so, or a judge will assume you did. Every other beat is an observation; do not let this one pass as a sixth.
- **artifact** `geap/failclosed/authz_extension_readback.json` — in this repository; the live command above returns the same payload.
- **secret** token in command — same handling as #9
- **RECHECK** ✅ confirmed 2026-08-28 — control-plane GET on `authzExtensions/harbor-iap-authz` returns
  `service: iap.googleapis.com`, `metadata.iapPolicyVersion: V1`, `timeout: 1s`, and **no `failOpen` key**.
  Still a configuration property. Still not a demonstrated outage.

## 16. StormSlot — the transfer proof
- **claim** The architecture is not scenario-specific.
- **action** `.venv/bin/python -m app.demo stormslot --pretty` then `.venv/bin/python -m app.gate`
- **visible** stormslot commits `stormslot-plan-2`, `SLOT_REBOOKED 15 → 14`; gate shows **both** scenarios 5/5 SURVIVES
- **duration** **0.06s + 0.07s** (measured) · **lane** SAFE LIVE
- **why it lands** both scenario traces are **16 lines with the same shape** — WEATHER_MEASURED, three disjoint claims, a named rejection, a replan, a commit. Shown back to back they read as *one substrate with two scenarios*, which is the actual claim.
- **determinism** both byte-identical across 3 runs (demo sha1 `941c730e…`, gate sha1 `0928f745…`)
- **budget** 10-20% of technical time. No new engineering — this already works.
- **secret** none · **RECHECK** no

## 16b. Test suite
- **claim** 287 passing, and the 47 skips are one deliberate cause.
- **action** `.venv/bin/python -m pytest tests -q`
- **visible** `287 passed, 47 skipped` (`258` at the 2026-08-28 recheck, before the additive sentinel and ReliefRun)
- **duration** **1.18s cold / ~0.98s warm** (measured) · **lane** SAFE LIVE
- **have ready** all 47 skips are `tests/test_store_contract.py: set FIRESTORE_EMULATOR_HOST to run` — the Firestore contract suite, skipped by design without an emulator. Say it before a judge asks.
- **secret** none · **RECHECK** ✅ confirmed 2026-08-28 — `258 passed, 47 skipped`, and all 47
  skips are the single reason `tests/test_store_contract.py: set FIRESTORE_EMULATOR_HOST to run`. Re-taken at the
  frozen engineering SHA `687eebfd26f64d87f3c8db49756f838dc90bc02a`: `258 passed, 47 skipped`, and `305 passed`
  with none skipped under the Firestore emulator.

## 17. Frozen-core integrity
- **claim** The managed layer was built *around* a frozen core, not by rewriting it.
- **action** put `docs/CORE_FREEZE.md` on screen and state the freeze record. This
  submission repository publishes the frozen tree without its engineering history —
  the commits it does carry all post-date first publication and are the declared
  additive layers (prose, sentinel, ReliefRun, portal) — so the freeze record is
  presented as **stated fact**, not as a diff a judge is asked to run. A git
  command here can show only those declared additions, never the freeze points.
- **the record, as stated**
  - the submitted code and evidence are content-identical to engineering SHA
    `687eebfd26f64d87f3c8db49756f838dc90bc02a` (the judge-facing documents were
    finalized after that SHA)
  - the pre-managed baseline, `core-freeze-1`, was `cf91551`
  - against that baseline the delta in `app/` and `tests/` is **five files** — `app/api.py`,
    `app/gate.py`, `app/config.py`, `app/providers/routes.py`, `tests/test_api.py` — and it
    is **presentation only**; the operational-core delta is zero
  - against the submission freeze the same delta is **empty**: nothing in the operational
    core moved to make the managed work possible
- **duration** static · **lane** SAFE LIVE (document on screen)
- **narration** "The core was frozen before any of the managed work started, and
  nothing in it was rewritten to make that work. One change was made afterwards,
  deliberately, to judge-facing strings that still said the scenario choice
  hadn't happened — so it was recorded as a separate, later freeze rather than
  folded into the first." `docs/CORE_FREEZE.md` names the five files and what changed
  in each, if asked.
- **⚠ do not** present the baseline delta as empty; it is not, and the difference —
  presentation only, operational core untouched — is the whole point.
- **secret** none · **RECHECK** ✅ confirmed 2026-08-28 — delta against the submission
  freeze **empty**; delta against `core-freeze-1` exactly those five files.

## 18. Evidence provenance
- **claim** Every quoted number is bound to a committed SHA and a capture time, not to a
  working tree.
- **action** show this map's `RECHECK` stamps beside the evidence block in
  `docs/CORE_FREEZE.md` — each number sits next to the SHA it was taken at and the date it
  was confirmed.
- **duration** static · **lane** SAFE LIVE (documents on screen)
- **narration** Evidence collected from a moving tree is void. Before any run whose numbers
  are quoted, the tree must be a known committed SHA with no uncommitted or untracked
  changes — an untracked `conftest.py` or `sitecustomize.py` can change what a run does
  while the committed SHA still looks certified. Every quoted number is stamped with the
  HEAD SHA and capture time. The gate fails closed: if a precondition does not hold,
  evidence collection stops rather than proceeding with a caveat.

- **note** the preflight gate that enforces this is not part of the submitted tree. What is
  submitted is its output: SHA-stamped, time-stamped numbers, and a freeze record that says
  what moved and what did not.
- **secret** none · **RECHECK** ✅ confirmed 2026-08-28 — the numbers quoted in this map
  were re-taken at engineering SHA `687eebfd26f64d87f3c8db49756f838dc90bc02a`.

---

## Shot order for a 4-minute cut

```
0:00-0:30  #1        problem              animation
0:30-1:10  #2 #3     bounded actors, seeded run moves 12:00 -> 14:00   LIVE
1:10-1:50  #4 #5 #6  managed Gemini, five tools, verifier chain        PRE-CAPTURED
1:50-2:05  #7        Firestore readback                                LIVE
2:05-2:50  #11-14    governed allow / deny / unregistered + Google log  LIVE
2:50-3:20  #8        stale rejection                                    PRE-CAPTURED
3:20-3:35  #16 #17   StormSlot transfer + frozen core                   LIVE
3:35-4:00  closing thesis card
```

`#15` (fail closed) is a one-line mention inside the 2:05-2:50 block, framed as
configuration. `#9`, `#10`, `#18` are B-roll for whichever block has room.

**Say the engine split inside the cut.** The 1:10-1:50 block runs on engine
`3244216260136796160`, which demonstrates the managed Gemini actor path and is **not**
Gateway-bound. The 2:05-2:50 block runs on engine `2414533581910048768`, which demonstrates
governed egress and does **not** run the actor. Both surfaces are real and both are shown,
but the complete actor-plus-Gateway path is **not demonstrated end-to-end on one engine**.
Cutting them back to back without saying so would let the sequence imply a single engine
did both — which is the one thing this cut must not claim.

## Evidence captured to close three gaps (2026-08-28)

An audit of the evidence found three load-bearing claims with **no committed
artifact**. All three are now witnessed and are in the repo:

| file | closes |
|---|---|
| `geap/iap_endpoint_policies.json` | weather endpoint holds `roles/iap.egressor`; **cargo endpoint has NO bindings**. Previously the deny arm was consistent with an absent grant but the absence was never proven. |
| `geap/iam_project_agent_principals.json` | project roles bound per agent-identity principal. Filtered to that trust domain, per the file's own `note` — live `roles/datastore.user` also carries a pre-existing compute service account. Scope the negative to what was checked: in the two bindings this rests on, `roles/aiplatform.user` and `roles/datastore.user`, every member is a per-engine `principal://` — no `principalSet` and no project-wide role appears **in those two**. The wider project policy was not audited, and the same file's `roles/aiplatform.agentDefaultAccess` binding does hold a platform-default `principalSet`. |
| `geap/firestore_iam_enforcement_legs.json` | the 401 → 403 → 403 legs with timestamps, exceptions and call sites. The 403 is the load-bearing leg: with the token-sharing prevention flag `False` — meaning token sharing is *permitted*, not that Agent Identity IAM is off — a named principal still reached Firestore and IAM refused it. It had existed only as prose. The file holds ERROR-severity legs only and contains **no success leg**; do not narrate it as 401 → 403 → success. The successful run is evidenced separately in `geap/d1_shift_accept.json`. |

All three are strong on screen and none needs narration beyond one line.

## Artifacts that must NOT appear on screen

Found during the evidence audit — each reads as the opposite of what it proves. The first
four are **not in this repository at all**: they were withheld from the submitted tree
precisely because they are misreadable out of context. They are named here so the reasoning
survives, not as files to open.

- An early gateway egress capture in which all three arms returned `ERROR` (SSL EOF, taken
  before mTLS was in use). Shown beside the governed pair it would invite "your deny arms
  are just transport failures."
- A later capture in which the deny arms are correct but the **allow arm is a 401** (an
  OAuth bearer that Weather rejects). That allow arm reads as a denial.
- The engine **create response**, as distinct from the gate. The gate is
  `geap/d0_readback.json`, a control-plane GET. Showing a create response would undercut the
  point that the identity was read back rather than trusted as asked for.
- The D1.5 gRPC investigation forensics are quarantined and are **deliberately absent from
  this repository**. gRPC is not part of the demonstrated path, and those records are why
  the HTTP/1.1-only log rule at the top of this map exists.
- **`geap/d1_egress_nogw2.json` needs care** — this one *is* in the repo: rows 2 and 3
  (cloudresourcemanager 200, bigquery 200) are the routing control. Row 1, weather, is a
  **401** — show rows 2–3 only, or explain that the weather arm there uses an OAuth bearer.

## Global redaction checklist

- [ ] no `gcloud auth print-access-token` output visible
- [ ] no weather API key; never hand-type a URL containing `key=`
- [ ] never render a full Reasoning Engine GET on camera — `spec.deploymentSpec.env`
      can carry deployment environment values in cleartext. Project only the fields
      you need; for the identity shot use `1562121799313915904`
- [ ] gateway logs captured **only** via `geap/capture_gateway_logs.py`
- [ ] no HTTP/2 gateway record on screen
- [ ] no `.env`; no `credentials.json`; no `/var/run/secrets/...` contents
- [ ] tenant SA (`…-tp@appspot…`) never presented as the agent identity
- [ ] run `grep -rIlE 'AIza[0-9A-Za-z_-]{35}'` over anything shown before publishing

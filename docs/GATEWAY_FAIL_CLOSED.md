# Agent Gateway authorization is configured fail-closed

The IAP authorization extension behind `harbor-egress-gw` was created with
`failOpen: true`, following Google's Runtime→Gateway example. That setting means
an authorization-extension failure is explicitly permitted through. For a
fortification story that is the wrong default: "what happens when the
authorization service cannot answer?" should not be "the request proceeds."

It is now `false`. This document records the configuration, its provenance, and
the selective-governance behaviour re-proven under it.

## What is claimed, and what is not

**Claimed.** Agent Gateway authorization is *configured* fail-closed. The
authorization extension's `failOpen` setting is false, and the HTTP
selective-governance triad remains proven under that configuration. Put
precisely: the gateway is configured so that an authorization-extension failure
is not explicitly permitted through fail-open behaviour.

**Not claimed.** No IAP outage was induced, and no experiment demonstrated
denial during an authorization-service failure. That experiment has not been
performed, and nothing here should be read as evidence of it. The distinction
matters: this is a **configuration property** plus a **demonstrated HTTP
allow/deny behaviour**, not a demonstrated outage response.

## The configuration, and why `failOpen` is absent from the readback

`geap/failclosed/authz_extension_readback.json` is the verbatim control-plane
GET. It does **not** contain a `failOpen` field:

```json
{
  "name": "projects/harbor-storm-fleet/locations/us-central1/authzExtensions/harbor-iap-authz",
  "createTime": "2026-08-27T19:29:09.593820661Z",
  "updateTime": "2026-08-28T09:44:13.095424588Z",
  "service": "iap.googleapis.com",
  "timeout": "1s",
  "metadata": { "iapPolicyVersion": "V1" }
}
```

**This absence is the `false` value, not an unknown state.** proto3 omits
boolean fields whose value is the default, and `false` is the default. The
field was not fabricated into the artifact to make it look tidy — the readback
is stored exactly as the API returned it.

That interpretation is not asserted, it is demonstrated against this same
resource and endpoint. `geap/d1_iap_extension.json`, captured on 2026-08-27
before the change, contains `"failOpen": true` explicitly. Same resource, same
API: the field is emitted when true and omitted when false.

| artifact | `failOpen` in payload |
|---|---|
| `geap/d1_iap_extension.json` (2026-08-27, before) | present, `true` |
| `geap/failclosed/authz_extension_readback.json` (now) | **absent** = `false` |

## Provenance: the change landed and has not been reverted

`geap/failclosed/authz_extension_audit_provenance.json` holds the Cloud Audit
records for this resource (operator identity redacted; the provenance value is
the method, time and resource):

```
2026-08-27T19:29:09.615Z  CreateAuthzExtension      (created with failOpen: true)
2026-08-27T19:29:09.982Z  CreateAuthzExtension
2026-08-28T09:42:30.694Z  UpdateAuthzExtension
2026-08-28T09:44:13.111Z  UpdateAuthzExtension      <- failOpen: false lands here
```

Four records, not three: the audit log carries the creation twice, and the
mutation history proper is the two `UpdateAuthzExtension` entries on 08-28.

The last `UpdateAuthzExtension` at `09:44:13.111Z` matches the resource's own
`updateTime` of `09:44:13.095Z`. The negative that matters here has to be scoped
to what was actually queried: **within the records this file contains** — the
resource's own audit set, running from its creation on 2026-08-27 to the capture
on 2026-08-28 — nothing mutates the extension after `09:44:13.111Z`. That is a
claim about the captured window, not about all time. A change made after the
capture would not appear in it, which is why the readback in *Reproducing* is
the check a reader should run rather than trusting this file.

## Behaviour re-proven under this configuration

Engine `2414533581910048768` — the rotated-key governed-egress engine, the same
one behind the earlier selective-egress evidence in `geap/d1_egress_rotated.json`
and `geap/gw_logs_rotated.json`. Agent Identity, with an engine-bound
`effective_identity` and no `serviceAccount` named in its deploy record
(`geap/d1_deployed_gw5.json`); bound to `harbor-egress-gw`. One identity, one
gateway, one transport; the client-side capture reports `is_mtls: true`.

**This engine does not run Harbor's actor.** The actor proof is the other engine,
`3244216260136796160`, which carries no gateway binding. Everything in this
section is a statement about governed egress on `2414533581910048768` and about
nothing else. The two are **not demonstrated end-to-end on one engine**, and this
document does not close that gap — it re-proves the egress behaviour under a
changed configuration, on the engine that already carried it.

Client-side (`geap/failclosed/http_triad_client.json`):

| arm | endpoint IAM | outcome | HTTP | attribution |
|---|---|---|---|---|
| `weather.googleapis.com` | `iap.egressor` **granted**, plus the destination's own API key | `DESTINATION_REACHED` | **200** + forecast | `server: ESF`, no IAP header |
| `cloudresourcemanager.googleapis.com` | **absent** | `GOVERNANCE_DENIED` | 403 | `x-goog-iap-generated-response: true` |
| `bigquery.googleapis.com` (unregistered) | n/a | `GOVERNANCE_DENIED` | 403 | *"…unregistered in the Agent Registry."* |

The artifact's own labels for those arms are `ALLOW: harbor-weather (registered,
iap.egressor GRANTED, API key auth)`, `DENY: harbor-cargo-ops (registered,
iap.egressor ABSENT)` and `DENY: unregistered destination`. The allow arm is
therefore not carried by the IAM grant alone — the destination authenticates the
request with its own API key as well, so a 200 there means the per-endpoint IAP
grant *and* the destination's own auth were both satisfied. What isolates the
grant is the deny arms: same identity, same gateway, same transport, registered
destination, `iap.egressor` absent, refused.

Gateway-side (`geap/failclosed/http_triad_gateway.json`), Google's own records:

```
weather.googleapis.com               GET 200  authz=ALLOWED  registry=…3e88-93525e6955a6  clientCertPresent=true
cloudresourcemanager.googleapis.com  GET 403  authz=DENIED   registry=…dffe-b2901c86a27a  clientCertPresent=true
bigquery.googleapis.com              GET 403  authz=DENIED   registry=(none)              clientCertPresent=true
```

`clientCertPresent` is the `jsonPayload.mtls.clientCertPresent` field of those
records, and it says exactly one thing: a client certificate was presented. It is
not a verified-chain claim. The same three records also carry
`clientCertChainVerified: "false"` and
`clientCertError: "client_cert_validation_not_performed"`. So the mutual-TLS
statement supported here is that the transport presented a client certificate and
Google logged it — not that the gateway validated that certificate's chain. The
client-side `is_mtls: true` above is likewise the capture's own field, derived
from the runtime's certificate configuration, not an independent verification.

Outcomes are classified by **who answered** — an IAP-generated response means
the gateway refused before the destination was reached; the destination's own
server markers mean egress was permitted.

## Scope of this evidence

**HTTP/1.1 only — a property of these three records, not of the capture tool.**
gRPC is not part of the demonstrated path, and nothing in this package settles
how the gateway behaves for gRPC traffic. Mixing a gRPC record into a proof about
HTTP selective governance would import that unsettled question into a claim that
does not depend on it, so the triad is confined to HTTP/1.1.

That is established the only way a reader can check for themselves: every record
in `geap/failclosed/http_triad_gateway.json` carries `httpRequest.protocol`, and
all three read `HTTP/1.1`. The file holds three records and no others. Nothing
was filtered out — the probe emitted exactly three requests inside the window,
and a time-bounded query over `2026-08-28T10:43:00Z`–`10:44:30Z` returns those
same three and nothing else.

`geap/capture_gateway_logs.py` does **not** enforce this. It filters on
`resource.type="networkservices.googleapis.com/Gateway"` and a freshness window,
and it prints the number of records *written*, never a number excluded. The
protocol discipline is a manual check applied after capture and before use. It
has to be described that way rather than as a control the tooling provides.

The distinction is load-bearing here. This gateway does carry HTTP/2 traffic, and
some of it reads the opposite way: HTTP/2 `ALLOWED` records exist for the
`cloudresourcemanager` host family that this document's deny arm rests on. No
counts are quoted for that, deliberately — the observation came from a broad
`gateway_requests` query that is not committed here, and the gateway captures
this package does commit (`geap/gw_logs_authz.json`, `geap/gw_logs_rotated.json`,
`geap/gw_logs_final.json`, `geap/failclosed/http_triad_gateway.json`) are all
HTTP/1.1, so a reader has nothing in-tree against which to check a figure. The
reasoning does not need the figure. An unfiltered capture can surface a record
showing that host allowed, sitting beside the claim that it is denied. That is
why the rule exists, and why it is checked by hand every time.

Evidence was captured with the committed scrubber
(`geap/capture_gateway_logs.py` → `app/geap/log_scrubber.py`), which sanitises
credential-bearing URLs before anything reaches disk and refuses to write at all
if a credential shape survives. That part *is* enforced in code.

## Reproducing

```bash
# configuration
curl -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  https://networkservices.googleapis.com/v1beta1/projects/harbor-storm-fleet/locations/us-central1/authzExtensions/harbor-iap-authz

# behaviour
PYTHONPATH=. .venv/bin/python geap/d1_invoke.py \
  --engine projects/801248256447/locations/us-central1/reasoningEngines/2414533581910048768 \
  --method egress_probe --kwargs "$(cat geap/targets.json)" --out /tmp/triad.json

# gateway records, sanitised before write — NOT protocol-filtered, see below
PYTHONPATH=. .venv/bin/python geap/capture_gateway_logs.py --out /tmp/gw.json --freshness 5m

# check the protocol of what you just captured before using it for anything
python3 -c "import json;print(sorted({r['httpRequest']['protocol'] for r in json.load(open('/tmp/gw.json'))}))"
```

That last step is not optional. A fresh capture is **not** filtered to HTTP/1.1;
if it returns anything other than `['HTTP/1.1']`, the extra records are outside
the scope of this document and must not be shown beside it.

Live weather changes, so the forecast body differs run to run. What must hold is
the allow/deny pattern and the authorization results.

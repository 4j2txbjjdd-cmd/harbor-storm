#!/usr/bin/env bash
# D2: converge the two-engine split onto one gateway-bound engine.
#
# Registers the actor path's Google API dependencies in the Agent Registry
# and binds roles/iap.egressor for the converged engine on each minted
# endpoint, plus the two per-engine project roles the actor needs. Run by a
# human: these are authorization changes, and they should be deliberate.
#
#   ./geap/d2_converge.sh            # do everything
#
# Idempotent-ish: re-registering an existing service id fails harmlessly;
# setIamPolicy overwrites only the endpoint policies this script owns.
# Nothing here prints or stores a credential; the bearer token is inlined
# per call from gcloud.
set -euo pipefail

PROJECT="harbor-storm-fleet"
PROJECT_NUM="801248256447"
REGION="us-central1"
ENGINE_ID="6110651869841850368"   # harbor-converged (gateway-bound, actor app)
PRINCIPAL="principal://agents.global.org-648972411952.system.id.goog/resources/aiplatform/projects/${PROJECT_NUM}/locations/${REGION}/reasoningEngines/${ENGINE_ID}"

REG="https://agentregistry.googleapis.com/v1/projects/${PROJECT}/locations/${REGION}/services"
IAP="https://iap.googleapis.com/v1/projects/${PROJECT_NUM}/locations/${REGION}/iap_web/agentRegistry/endpoints"

auth() { echo "Authorization: Bearer $(gcloud auth print-access-token)"; }

register() { # id display url
  local id="$1" display="$2" url="$3"
  echo "==> register ${id} -> ${url}"
  # Creation is a long-running operation: the POST returns an operation, and
  # the service (with its minted endpoint id) exists only once it completes.
  curl -s -X POST -H "$(auth)" -H "Content-Type: application/json" \
    "${REG}?serviceId=${id}" -d "{
      \"displayName\": \"${display}\",
      \"interfaces\": [{\"url\": \"${url}\", \"protocolBinding\": \"HTTP_JSON\"}],
      \"endpointSpec\": {\"type\": \"NO_SPEC\"}
    }"
  echo
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
    curl -sf -H "$(auth)" "${REG}/${id}" > /dev/null && return 0
    sleep 5
  done
  echo "    service ${id} still absent after 60s; investigate before binding"
  return 1
}

endpoint_of() { # service id -> minted endpoint id
  curl -sf -H "$(auth)" "${REG}/${1}" \
    | python3 -c "import json,sys; print(json.load(sys.stdin)['registryResource'].split('/')[-1])"
}

bind_egressor() { # endpoint id
  local ep="$1"
  echo "==> bind iap.egressor for converged engine on ${ep}"
  local etag
  etag=$(curl -s -X POST -H "$(auth)" -H "Content-Type: application/json" \
    -d '{}' "${IAP}/${ep}:getIamPolicy" \
    | python3 -c "import json,sys; print(json.load(sys.stdin).get('etag',''))" \
    2>/dev/null || true)
  local etag_field=""
  [ -n "${etag}" ] && etag_field=", \"etag\": \"${etag}\""
  curl -s -X POST -H "$(auth)" -H "Content-Type: application/json" \
    "${IAP}/${ep}:setIamPolicy" -d "{
      \"policy\": {
        \"bindings\": [{\"role\": \"roles/iap.egressor\",
                         \"members\": [\"${PRINCIPAL}\"]}]${etag_field}
      }
    }"
  echo
}

echo "== 1. project roles for the converged engine's Agent Identity =="
gcloud projects add-iam-policy-binding "${PROJECT}" \
  --member "${PRINCIPAL}" --role roles/aiplatform.user \
  --condition None --format "value(etag)" 
gcloud projects add-iam-policy-binding "${PROJECT}" \
  --member "${PRINCIPAL}" --role roles/datastore.user \
  --condition None --format "value(etag)" 

echo "== 2. register the actor path's destinations =="
register harbor-credentials "Agent credential minting (actor path infra)" \
  "https://iamcredentials.mtls.googleapis.com"
register harbor-model-plane "Vertex AI model plane (window-agent reasoning)" \
  "https://aiplatform.googleapis.com"
register harbor-state-store "Firestore authoritative store" \
  "https://firestore.googleapis.com"

echo "== 3. bind egressor on each minted endpoint =="
for svc in harbor-credentials harbor-model-plane harbor-state-store; do
  ep=$(endpoint_of "${svc}")
  echo "    ${svc} -> ${ep}"
  bind_egressor "${ep}"
done

echo "== 4. sanity: known-good weather arm must still be intact =="
curl -sf -X POST -d '{}' -H "$(auth)" \
  "${IAP}/agentregistry-00000000-0000-0000-3e88-93525e6955a6:getIamPolicy" \
  | python3 -c "import json,sys; p=json.load(sys.stdin); \
    print('    weather endpoint bindings:', \
          sum(len(b.get('members',[])) for b in p.get('bindings',[])), 'members')"

echo
echo "Done. IAM/IAP propagation can lag; give it a few minutes before the"
echo "re-probe. The deny control (harbor-cargo-ops) was deliberately not"
echo "touched and must stay unbound."

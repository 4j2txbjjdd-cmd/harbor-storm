#!/usr/bin/env bash
# Wire a Pub/Sub topic to the deployed service's push endpoint, with a
# dead-letter topic. The push endpoint returns 400 on a malformed message
# rather than silently acking, so without a DLQ a poison message retries
# forever -- this script creates one.
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID}"
: "${SERVICE_URL:?set SERVICE_URL to the deployed Cloud Run URL}"
REGION="${REGION:-europe-west4}"
TOPIC="${TOPIC:-harbor-disruptions}"
DLQ="${DLQ:-${TOPIC}-dead-letter}"
SUB="${SUB:-${TOPIC}-push}"

for t in "${TOPIC}" "${DLQ}"; do
  gcloud pubsub topics describe "${t}" --project "${PROJECT_ID}" >/dev/null 2>&1 || \
    gcloud pubsub topics create "${t}" --project "${PROJECT_ID}"
done

gcloud pubsub subscriptions describe "${SUB}" --project "${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud pubsub subscriptions create "${SUB}" \
    --topic "${TOPIC}" \
    --push-endpoint "${SERVICE_URL}/pubsub/push" \
    --dead-letter-topic "${DLQ}" \
    --max-delivery-attempts 5 \
    --project "${PROJECT_ID}"

echo "==> ${TOPIC} -> ${SERVICE_URL}/pubsub/push (dead-letter: ${DLQ})"
echo "trigger a disruption:"
echo "  gcloud pubsub topics publish ${TOPIC} --project ${PROJECT_ID} \\"
echo "    --message '{\"run_id\":\"<run-id>\",\"profile\":\"disrupted\"}'"

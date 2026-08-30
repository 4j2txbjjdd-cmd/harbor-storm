#!/usr/bin/env bash
# Build and deploy the demo to Cloud Run.
#
#   PROJECT_ID=my-project ./deploy/deploy.sh
#
# Every required value is checked before anything is built, so a missing
# variable fails here rather than halfway through a deploy.
set -euo pipefail

: "${PROJECT_ID:?set PROJECT_ID to your Google Cloud project}"
REGION="${REGION:-europe-west4}"
SERVICE="${SERVICE:-harbor-storm}"
IMAGE="${IMAGE:-${REGION}-docker.pkg.dev/${PROJECT_ID}/harbor/${SERVICE}}"
TAG="${TAG:-$(git rev-parse --short HEAD 2>/dev/null || echo latest)}"

command -v gcloud >/dev/null || { echo "gcloud is not installed" >&2; exit 1; }

echo "==> project ${PROJECT_ID}  region ${REGION}  image ${IMAGE}:${TAG}"

gcloud services enable run.googleapis.com firestore.googleapis.com \
  artifactregistry.googleapis.com pubsub.googleapis.com --project "${PROJECT_ID}"

gcloud artifacts repositories describe harbor --location "${REGION}" \
  --project "${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud artifacts repositories create harbor --repository-format=docker \
    --location "${REGION}" --project "${PROJECT_ID}"

gcloud builds submit --tag "${IMAGE}:${TAG}" --project "${PROJECT_ID}" .

# maxScale stays at 1 on purpose; see deploy/service.yaml for why.
gcloud run deploy "${SERVICE}" \
  --image "${IMAGE}:${TAG}" \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --platform managed \
  --allow-unauthenticated \
  --min-instances 1 --max-instances 1 \
  --set-env-vars "STATE_BACKEND=firestore,WEATHER_PROVIDER=${WEATHER_PROVIDER:-mock},GOOGLE_CLOUD_PROJECT=${PROJECT_ID}" \
  --port 8080

URL=$(gcloud run services describe "${SERVICE}" --region "${REGION}" \
  --project "${PROJECT_ID}" --format 'value(status.url)')
echo "==> deployed: ${URL}"
echo "==> config:   ${URL}/config"
curl -fsS "${URL}/healthz" && echo " healthz ok"

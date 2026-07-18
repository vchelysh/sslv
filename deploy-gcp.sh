#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:-europe-west1}"
SERVICE="${SERVICE:-sslv-watcher}"
SCHEDULER_SA="${SCHEDULER_SA:-sslv-watcher-scheduler}"
SCHEDULE="${SCHEDULE:-0 0,2,4,6,8,10,12,14,16,18 * * *}"
TIME_ZONE="${TIME_ZONE:-Europe/Riga}"

gcloud config set project "$PROJECT_ID"

gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  firestore.googleapis.com \
  cloudscheduler.googleapis.com

# Firestore Native mode must exist. This is safe only for a new project.
if ! gcloud firestore databases describe --database="(default)" >/dev/null 2>&1; then
  gcloud firestore databases create \
    --database="(default)" \
    --location=eur3 \
    --type=firestore-native
fi

gcloud iam service-accounts describe \
  "${SCHEDULER_SA}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1 || \
gcloud iam service-accounts create "$SCHEDULER_SA" \
  --display-name="SS.LV watcher scheduler"

gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --min 0 \
  --max 1 \
  --no-allow-unauthenticated \
  --set-env-vars="CONFIG_PATH=config.yaml,STORAGE_BACKEND=firestore" \
  --set-secrets="TELEGRAM_BOT_TOKEN=sslv-telegram-bot-token:latest,TELEGRAM_CHAT_ID=sslv-telegram-chat-id:latest"

SERVICE_URL="$(gcloud run services describe "$SERVICE" \
  --region "$REGION" \
  --format='value(status.url)')"

gcloud run services add-iam-policy-binding "$SERVICE" \
  --region "$REGION" \
  --member="serviceAccount:${SCHEDULER_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/run.invoker"

if gcloud scheduler jobs describe "$SERVICE" --location "$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$SERVICE" \
    --location "$REGION" \
    --schedule "$SCHEDULE" \
    --time-zone "$TIME_ZONE" \
    --uri="${SERVICE_URL}/run" \
    --http-method=POST \
    --oidc-service-account-email="${SCHEDULER_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --oidc-token-audience="$SERVICE_URL"
else
  gcloud scheduler jobs create http "$SERVICE" \
    --location "$REGION" \
    --schedule "$SCHEDULE" \
    --time-zone "$TIME_ZONE" \
    --uri="${SERVICE_URL}/run" \
    --http-method=POST \
    --oidc-service-account-email="${SCHEDULER_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --oidc-token-audience="$SERVICE_URL"
fi

echo "Deployed: $SERVICE_URL"
echo "Scheduler: 10 runs per day, timezone $TIME_ZONE"

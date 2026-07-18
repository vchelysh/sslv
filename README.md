# SS.LV Watcher

Personal SS.LV listing watcher for:

- apartment rentals;
- cars for sale;
- Telegram notifications;
- configurable YAML filters;
- deduplication in Firestore;
- deployment to Cloud Run;
- execution by Cloud Scheduler.

The watcher uses SS.LV RSS feeds to discover new listings and opens only new listing pages to read detailed fields. Keep the polling interval reasonable and use it for personal monitoring.

## 1. Configure searches

Copy the example configuration:

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml`.

Useful filters:

```yaml
price_eur:
  min: 5000
  max: 13000

year:
  min: 2014

mileage_km:
  max: 220000

rooms:
  min: 2
  max: 3

area_m2:
  min: 45

include_any: ["A4", "A3"]
required_all: ["automāt"]
exclude_any: ["pērku", "куплю"]

detail_attributes:
  "Ātr.kārba":
    contains_any: ["Automāts"]
```

`include_any`, `required_all`, and `exclude_any` search across the title, description, and extracted detail fields.

Because SS.LV labels can differ between Latvian and Russian pages, `detail_attributes` performs case-insensitive partial key matching.

## 2. Create a Telegram bot

1. Open Telegram and message `@BotFather`.
2. Run `/newbot` and copy the token.
3. Send any message to the new bot.
4. Open:

```text
https://api.telegram.org/bot<BOT_TOKEN>/getUpdates
```

5. Copy `message.chat.id`.

For GCP, store both values in Secret Manager:

```bash
printf '%s' 'BOT_TOKEN_HERE' | \
  gcloud secrets create sslv-telegram-bot-token --data-file=-

printf '%s' 'CHAT_ID_HERE' | \
  gcloud secrets create sslv-telegram-chat-id --data-file=-
```

For existing secrets, add a new version instead:

```bash
printf '%s' 'BOT_TOKEN_HERE' | \
  gcloud secrets versions add sslv-telegram-bot-token --data-file=-
```

## 3. Run locally

Use memory storage for a quick test:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp config.example.yaml config.yaml

export STORAGE_BACKEND=memory
export TELEGRAM_BOT_TOKEN='...'
export TELEGRAM_CHAT_ID='...'

python -m app.main
curl -X POST http://localhost:8080/run
```

Memory storage resets after every restart. Firestore is the production option.

By default, the first run records the current feed as a baseline and does not send all existing ads. Set:

```yaml
settings:
  first_run_send_existing: true
```

to receive current matches during the first execution.

## 4. Deploy to Google Cloud

Prerequisites:

- `gcloud` authenticated;
- a GCP project;
- Telegram secrets created in Secret Manager;
- permission to deploy Cloud Run, create Scheduler jobs, Firestore, service accounts, and IAM bindings.

```bash
cp config.example.yaml config.yaml
chmod +x deploy-gcp.sh

export PROJECT_ID='your-project-id'
export REGION='europe-west1'

./deploy-gcp.sh
```

The script:

1. enables required APIs;
2. creates Firestore if it does not exist;
3. deploys the source to Cloud Run;
4. keeps the service private;
5. creates a Scheduler service account;
6. grants it `roles/run.invoker`;
7. schedules `POST /run` every five minutes with OIDC authentication.

## 5. Required runtime permissions

The Cloud Run runtime service account needs:

```text
roles/datastore.user
roles/secretmanager.secretAccessor
```

The Scheduler service account needs:

```text
roles/run.invoker
```

Depending on your organization policies, grant these explicitly to the service account selected by Cloud Run.

## 6. Operational notes

- Keep the interval at five minutes or slower.
- Do not crawl listing pages that were already seen.
- Do not collect or republish seller phone numbers or other personal data.
- Expect occasional parser maintenance when SS.LV changes HTML.
- A failed detail-page parse does not crash the complete run; the item is evaluated using RSS data where possible.
- `/run` is private when deployed by the script and invoked by Cloud Scheduler with OIDC.

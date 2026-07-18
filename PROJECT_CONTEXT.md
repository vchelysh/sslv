# SSLV watcher — continuation context

## What this project does

`app/main.py` reads SS.LV RSS feeds, fetches listing detail pages, applies filters, stores seen listing IDs, and sends matching listings to Telegram.

## Current local configuration

- `storage_backend: memory` locally; Cloud Run uses Firestore.
- Audi search is disabled (`status: disabled`, `enabled: false`).
- Active search: Riga Centre rental apartments, exactly 4 rooms, price up to 1200 EUR/month.
- The active feed is the Centre-specific SS.LV RSS URL and currently returns up to 20 RSS entries.
- `max_items_per_feed: 20`.

## GCP deployment

- Project: `my-ss-lv`
- Region: `europe-west1`
- Cloud Run service: `sslv-watcher`
- URL: `https://sslv-watcher-486064019870.europe-west1.run.app`
- Cloud Run is private; Cloud Scheduler invokes `POST /run` with OIDC.
- Firestore Native database: `(default)`, location `eur3`.
- Scheduler job: `sslv-watcher`, timezone `Europe/Riga`, cron `0 0,2,4,6,8,10,12,14,16,18 * * *` (10 runs/day).
- Telegram values are in Secret Manager (`sslv-telegram-bot-token` and `sslv-telegram-chat-id`); no secrets belong in Git.
- The latest Scheduler test returned HTTP 200.

## Repository

- GitHub: `https://github.com/vchelysh/sslv`
- Branch: `develop`
- Terraform files are under `terraform/`.
- The Terraform configuration describes APIs, Firestore, Cloud Run, Scheduler, secrets, and IAM. It expects an already-built image via `var.container_image` and existing Secret Manager secrets.

## Important deployment note

`gcloud run deploy --source .` creates a private Cloud Storage source staging bucket (`run-sources-<project>-<region>`) and uploads source ZIP archives for Cloud Build. This is not the runtime storage and the bucket has no `allUsers` access, but image-based deployment is cleaner:

```text
docker build → Artifact Registry → Cloud Run --image
```

## Cost guardrails

- Cloud Run is configured with min instances 0 and max instances 1.
- Scheduler has one job and runs 10 times/day.
- Firestore uses the single default database and no paid features such as PITR, backups, TTL, or named databases.
- Artifact Registry should keep only a small number of images and stay under the account free storage allowance.
- GCP billing is enabled. Free tier is quota-based, not an absolute spending cap; keep budget alerts and review billing regularly.

## Recent commits

- `2e96fbe` — initial project, config, Terraform, deployment scripts
- `a09f30d` — trim Telegram secret environment values
- `dba4b46` — Terraform IAM bindings for Cloud Run and Scheduler

resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "firestore.googleapis.com",
    "cloudscheduler.googleapis.com",
    "secretmanager.googleapis.com",
  ])

  service            = each.value
  disable_on_destroy = false
}

resource "google_firestore_database" "default" {
  project     = var.project_id
  name        = "(default)"
  location_id = "eur3"
  type        = "FIRESTORE_NATIVE"
}

resource "google_service_account" "scheduler" {
  account_id   = "${var.service_name}-scheduler"
  display_name = "SS.LV watcher scheduler"
}

data "google_secret_manager_secret" "telegram_token" {
  secret_id = var.telegram_token_secret
}

data "google_secret_manager_secret" "telegram_chat_id" {
  secret_id = var.telegram_chat_id_secret
}

data "google_project" "current" {
  project_id = var.project_id
}

resource "google_cloud_run_v2_service" "watcher" {
  name     = var.service_name
  location = var.region

  template {
    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    containers {
      image = var.container_image

      env {
        name  = "CONFIG_PATH"
        value = "config.yaml"
      }
      env {
        name  = "STORAGE_BACKEND"
        value = "firestore"
      }
      env {
        name = "TELEGRAM_BOT_TOKEN"
        value_source {
          secret_key_ref {
            secret  = data.google_secret_manager_secret.telegram_token.id
            version = "latest"
          }
        }
      }
      env {
        name = "TELEGRAM_CHAT_ID"
        value_source {
          secret_key_ref {
            secret  = data.google_secret_manager_secret.telegram_chat_id.id
            version = "latest"
          }
        }
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "scheduler_invoker" {
  name     = google_cloud_run_v2_service.watcher.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_secret_manager_secret_iam_member" "runtime_token_accessor" {
  secret_id = data.google_secret_manager_secret.telegram_token.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

resource "google_secret_manager_secret_iam_member" "runtime_chat_id_accessor" {
  secret_id = data.google_secret_manager_secret.telegram_chat_id.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}

resource "google_service_account_iam_member" "scheduler_token_creator" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${google_service_account.scheduler.email}"
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-cloudscheduler.iam.gserviceaccount.com"
}

resource "google_cloud_scheduler_job" "watcher" {
  name      = var.service_name
  region    = var.region
  schedule  = var.scheduler_cron
  time_zone = var.timezone

  http_target {
    uri         = "${google_cloud_run_v2_service.watcher.uri}/run"
    http_method = "POST"

    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = google_cloud_run_v2_service.watcher.uri
    }
  }
}

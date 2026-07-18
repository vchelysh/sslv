variable "project_id" {
  type        = string
  description = "GCP project ID"
}

variable "region" {
  type    = string
  default = "europe-west1"
}

variable "service_name" {
  type    = string
  default = "sslv-watcher"
}

variable "container_image" {
  type        = string
  description = "Artifact Registry image URI"
}

variable "scheduler_cron" {
  type    = string
  default = "0 0,2,4,6,8,10,12,14,16,18 * * *"
}

variable "timezone" {
  type    = string
  default = "Europe/Riga"
}

variable "telegram_token_secret" {
  type    = string
  default = "sslv-telegram-bot-token"
}

variable "telegram_chat_id_secret" {
  type    = string
  default = "sslv-telegram-chat-id"
}

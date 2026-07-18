output "service_url" {
  value = google_cloud_run_v2_service.watcher.uri
}

output "scheduler_job" {
  value = google_cloud_scheduler_job.watcher.name
}

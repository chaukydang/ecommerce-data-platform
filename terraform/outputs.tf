output "bucket_name" {
  value = google_storage_bucket.raw.name
}

output "raw_dataset" {
  value = google_bigquery_dataset.raw.dataset_id
}

output "staging_dataset" {
  value = google_bigquery_dataset.staging.dataset_id
}

output "intermediate_dataset" {
  value = google_bigquery_dataset.intermediate.dataset_id
}

output "marts_dataset" {
  value = google_bigquery_dataset.marts.dataset_id
}


output "platform_datasets" {
  value = sort(keys(google_bigquery_dataset.platform))
}

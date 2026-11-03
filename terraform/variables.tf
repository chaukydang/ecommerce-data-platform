variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-east1"
}

variable "bucket_name" {
  description = "GCS bucket name"
  type        = string
}

variable "credentials" {
  description = "GCP service account key JSON"
  type        = string
  sensitive   = true
}
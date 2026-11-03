# Datasets for the omnichannel platform (raw layer written by src/ecomplatform, transformed layers by dbt).
locals {
  platform_datasets = {
    raw_marketplace      = "Raw marketplace orders (SIMULATED source)"
    raw_oms              = "Raw OMS orders (SIMULATED source)"
    raw_pos              = "Raw POS transactions (SIMULATED source)"
    raw_finance          = "Raw payments, refunds and settlements (SIMULATED source)"
    platform_staging     = "dbt staging models for the omnichannel platform"
    platform_intermediate = "dbt intermediate models for the omnichannel platform"
    platform_marts       = "dbt marts: fct_*, dim_*, mart_daily_sales, mart_financial_reconciliation"
  }
}

resource "google_bigquery_dataset" "platform" {
  for_each    = local.platform_datasets
  dataset_id  = each.key
  location    = var.region
  project     = var.project_id
  description = each.value
}

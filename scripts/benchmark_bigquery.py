#!/usr/bin/env python
"""Dry-run bytes-processed benchmark for partition / cluster pruning. NOT executed in CI (needs a GCP project).

    python scripts/benchmark_bigquery.py --project my-project --location us-east1
"""
import argparse

from google.cloud import bigquery

QUERIES = {
    "fct_platform_orders: full scan": "SELECT order_id, total_amount FROM `{p}.platform_marts.fct_platform_orders`",
    "fct_platform_orders: last 7 days (partition pruning)": (
        "SELECT order_id, total_amount FROM `{p}.platform_marts.fct_platform_orders` "
        "WHERE created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)"),
    "fct_platform_orders: one channel, last 7 days (+ clustering)": (
        "SELECT order_id, total_amount FROM `{p}.platform_marts.fct_platform_orders` "
        "WHERE created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY) AND channel = 'shopify_sim'"),
    "raw_marketplace.orders: full scan": "SELECT order_id FROM `{p}.raw_marketplace.orders`",
    "raw_marketplace.orders: last day of ingestion (partition on ingested_at)": (
        "SELECT order_id FROM `{p}.raw_marketplace.orders` WHERE ingested_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY)"),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--location", default="us-east1")
    a = ap.parse_args()
    client = bigquery.Client(project=a.project, location=a.location)
    cfg = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
    for name, sql in QUERIES.items():
        job = client.query(sql.format(p=a.project), job_config=cfg)
        print(f"{job.total_bytes_processed:>14,d} bytes  {name}")


if __name__ == "__main__":
    main()

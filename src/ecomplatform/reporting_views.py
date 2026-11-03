"""Render the reporting-views SQL (sql/bigquery_dashboard_views.sql) for a project / dataset naming scheme.

The simulated platform uses platform_marts + platform_reporting. The Shopify profile writes to suffixed datasets so real and
simulated data never mix (platform_marts_shopify, platform_reporting_shopify) and its pipeline-health view lists only its tables.
"""
from __future__ import annotations

import re
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[2] / "sql" / "bigquery_dashboard_views.sql"
SIM_RAW_TABLES = ["raw_marketplace.orders", "raw_oms.orders", "raw_pos.transactions", "raw_finance.payments",
                  "raw_finance.refunds", "raw_finance.settlements"]


def _health_view(project: str, reporting: str, raw_tables: list[str]) -> str:
    union = "\n  UNION ALL ".join(
        f"SELECT '{t}' AS dataset, source_record_id, ingested_at, source_updated_at, batch_id\n  FROM `{project}.{t}`"
        for t in raw_tables)
    return f"""-- BEGIN v_pipeline_health
CREATE OR REPLACE VIEW `{project}.{reporting}.v_pipeline_health` AS
WITH raw_stats AS (
  {union}
)
SELECT
  dataset,
  COUNT(*)                                         AS rows_loaded,
  COUNT(DISTINCT source_record_id)                 AS distinct_records,
  COUNT(*) - COUNT(DISTINCT source_record_id)      AS extra_versions,
  COUNT(DISTINCT batch_id)                         AS batches,
  MIN(ingested_at)                                 AS first_loaded_at,
  MAX(ingested_at)                                 AS last_loaded_at,
  MAX(source_updated_at)                           AS latest_source_update,
  TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), MAX(ingested_at), MINUTE) AS minutes_since_last_load
FROM raw_stats
GROUP BY dataset;
-- END v_pipeline_health"""


def render(project: str, marts: str = "platform_marts", reporting: str = "platform_reporting",
           raw_tables: list[str] | None = None, template: Path = TEMPLATE) -> str:
    sql = template.read_text(encoding="utf-8")
    sql = sql.replace("ecommerce-data-platform-513615", project)
    sql = sql.replace(f"{project}.platform_marts.", f"{project}.{marts}.")
    sql = sql.replace(f"{project}.platform_reporting.", f"{project}.{reporting}.")
    sql = sql.replace("`ecommerce-data-platform-513615.platform_reporting`", f"`{project}.{reporting}`")
    sql = re.sub(r"CREATE SCHEMA IF NOT EXISTS `([^`]+)\.platform_reporting`", rf"CREATE SCHEMA IF NOT EXISTS `\1.{reporting}`", sql)
    sql = re.sub(r"-- BEGIN v_pipeline_health.*?-- END v_pipeline_health", lambda _m: _health_view(project, reporting, raw_tables or SIM_RAW_TABLES),
                 sql, flags=re.S)
    return sql


def duckdb_statements(sql: str, project: str) -> list[str]:
    """BigQuery view DDL -> DuckDB statements (offline demos/tests): drop the project prefix and transpile with sqlglot."""
    import sqlglot
    sql = sql.replace(f"`{project}.", "`")
    sql = re.sub(r"`([A-Za-z0-9_]+\.[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)?)`", r"\1", sql)
    return [st.sql(dialect="duckdb") for st in sqlglot.parse(sql, read="bigquery")
            if st is not None and isinstance(st, sqlglot.exp.Create) and st.args.get("kind") == "VIEW"]

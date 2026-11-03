# Operational runbook

## Alerts → first action

| Alert category | Meaning | First action |
|---|---|---|
| `PIPELINE_FAILURE` | an ingestion run or Airflow task failed | Read `pipeline_run_audit` (`error_category`: AUTH / TRANSIENT / SCHEMA / PERMANENT / UNEXPECTED) and the Airflow log. The checkpoint did **not** advance; re-running is safe. |
| `FRESHNESS_VIOLATION` | no successful load within the target | Is the upstream API up? Is the DAG paused/stuck? Check `ingestion_checkpoints` and the last audit rows. |
| `REPEATED_RETRIES` | ≥3 API retries in one run | Upstream rate limiting or latency; consider lowering the schedule rate. |
| `SCHEMA_DRIFT` | unexpected/missing fields | Update `configs/sources.yaml` + staging model; quarantined rows are in `<table>__rejected`. |
| `DATA_QUALITY_FAILURE` | a DQ check failed | `select * from data_quality_results where passed = 0 order by id desc`. |
| `RECONCILIATION_DISCREPANCY` | unresolved CONFIRMED exceptions | See `docs/reconciliation_rules.md` investigation queries. |

Alerts are grouped per incident with a cooldown (`ALERT_COOLDOWN_MINUTES`, default 60); severity escalation is sent immediately;
a single `RECOVERED` message is sent when the incident clears. Webhook URL/secret and API tokens are never logged or stored
(`util.redact` is applied to stored errors and alert text).

## Recover / backfill

```python
from ecomplatform.orchestration import get_engine, get_sink
from ecomplatform.ingestion.runner import run_ingestion
from ecomplatform.ingestion.core.checkpoint import CheckpointStore
from datetime import datetime

# 1. simply re-run after a failure: same window, idempotent
run_ingestion("marketplace_sim", "orders", engine=get_engine(), sink=get_sink())

# 2. backfill a historical range (checkpoint is untouched unless the window ends past the watermark)
run_ingestion("marketplace_sim", "orders", engine=get_engine(), sink=get_sink(), window_start=datetime(2026, 10, 1))

# 3. force a full reload: reset the checkpoint (raw stays; duplicates are impossible)
CheckpointStore(get_engine()).reset("marketplace_sim", "orders")
```

## Reliability reporting

```python
from ecomplatform.monitoring.reliability import reliability_summary
reliability_summary(get_engine())   # runs, success %, retries, rejected, mean/max recovery minutes, open outage
```

## Success metrics (measure, don't assume)

| Metric | Where |
|---|---|
| batch completion rate, failed-run recovery time, retries | `reliability_summary()` |
| freshness compliance | `check_freshness()` / alert history in `alert_state` |
| duplicate keys, validation failure rate | `data_quality_results`, `<table>__rejected` |
| matched transaction %, unresolved discrepancy amount | `reconciliation_runs`, `ExceptionStore.unresolved_confirmed_amount()` |
| dbt model time | `target/run_results.json` |
| BigQuery bytes processed | `scripts/benchmark_bigquery.py` (see `docs/benchmarks.md`) |
| alert detection latency | `alert_state.first_seen_at` − incident start in `pipeline_run_audit` |

## Rotate secrets

API tokens / Lark webhook / GCP key are environment variables only (`.env`, Codespaces secrets, Airflow connections/secret backend in
production). Rotate by changing the variable and restarting the scheduler; nothing is stored in the metadata DB.

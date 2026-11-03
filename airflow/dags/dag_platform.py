"""Omnichannel platform DAGs (SIMULATED sources): ingest -> transform -> reconcile, plus independent monitoring.

  ingest_marketplace_orders | ingest_oms_orders | ingest_pos_transactions | ingest_financial_records   (every 30 min)
  transform_ecommerce_warehouse  (hourly: dbt build platform models) -> reconcile_financial_data (triggered)
  monitor_data_quality           (every 15 min, deliberately NOT chained to ingestion so stale loads are noticed)
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

from ecomplatform import orchestration as orch

DBT_DIR = os.getenv("DBT_PROJECT_DIR", "/opt/airflow/dbt/olist")

DEFAULT_ARGS = {
    "owner": "data-eng",
    "retries": 1,
    "retry_delay": timedelta(minutes=2),
    "on_failure_callback": orch.airflow_failure_callback,
}
COMMON = dict(start_date=datetime(2026, 11, 1), catchup=False, max_active_runs=1, default_args=DEFAULT_ARGS,
              is_paused_upon_creation=True, tags=["platform"])

INGEST_DAGS = {
    "ingest_marketplace_orders": [("marketplace_sim", "orders")],
    "ingest_oms_orders": [("oms_sim", "orders")],
    "ingest_pos_transactions": [("pos_sim", "transactions")],
    "ingest_financial_records": [("finance_sim", "payments"), ("finance_sim", "refunds"), ("finance_sim", "settlements")],
}

for dag_id, entities in INGEST_DAGS.items():
    with DAG(dag_id=dag_id, schedule="*/30 * * * *", **COMMON) as dag:
        for source, entity in entities:
            PythonOperator(task_id=f"ingest_{source}_{entity}", python_callable=orch.ingest,
                           op_kwargs={"source": source, "entity": entity})
    globals()[dag_id] = dag

with DAG(dag_id="transform_ecommerce_warehouse", schedule="@hourly", **COMMON) as transform_dag:
    dbt_build = BashOperator(
        task_id="dbt_build_platform",
        bash_command=(f"dbt build --select path:models/platform --target prod --project-dir {DBT_DIR} --profiles-dir {DBT_DIR} "
                      "--vars '{as_of: \"{{ data_interval_end.strftime('%Y-%m-%d %H:%M:%S') }}\"}'"),
        env={**os.environ, "GCP_KEY_PATH": os.getenv("GCP_KEY_PATH", "/home/airflow/.gcp/keyfile.json")},
    )
    trigger_reconcile = TriggerDagRunOperator(task_id="trigger_reconcile", trigger_dag_id="reconcile_financial_data",
                                              conf={"as_of": "{{ data_interval_end.strftime('%Y-%m-%d %H:%M:%S') }}"})
    dbt_build >> trigger_reconcile

with DAG(dag_id="reconcile_financial_data", schedule=None, **COMMON) as reconcile_dag:
    PythonOperator(
        task_id="reconcile",
        python_callable=lambda **ctx: orch.reconcile(
            datetime.strptime((ctx["dag_run"].conf or {}).get("as_of", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")), "%Y-%m-%d %H:%M:%S")),
    )

with DAG(dag_id="monitor_data_quality", schedule="*/15 * * * *", **COMMON) as monitor_dag:
    PythonOperator(task_id="freshness_and_quality", python_callable=orch.monitor)

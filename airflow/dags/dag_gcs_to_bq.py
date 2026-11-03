import os
import json
from datetime import datetime
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.bash import BashOperator
from google.oauth2 import service_account
from google.cloud import bigquery, storage
from schema import SCHEMAS

PROJECT_ID = os.getenv("GCP_PROJECT_ID")
BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")
RAW_DATASET = "olist_raw"
# These paths are relative to the INSIDE of the Docker container
DBT_PROJECT_PATH = "/opt/airflow/dbt/olist"
DBT_PROFILES_PATH = "/opt/airflow/dbt/olist"
# DBT_PROJECT_PATH = "/workspaces/e-commerce-data-pipeline/dbt/olist"
# DBT_PROFILES_PATH = "/workspaces/e-commerce-data-pipeline/dbt/olist"

def get_clients():
    key_json = os.getenv("GCP_SERVICE_ACCOUNT_KEY")
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(key_json)
    )
    bq_client = bigquery.Client(credentials=credentials, project=PROJECT_ID)
    gcs_client = storage.Client(credentials=credentials)
    return bq_client, gcs_client

def load_gcs_to_bq(gcs_uri: str, table_id: str, bq_client, table_name: str):
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        schema=SCHEMAS[table_name]
    )
    load_job = bq_client.load_table_from_uri(
        gcs_uri, table_id, job_config=job_config
    )
    load_job.result()
    print(f"✓ Loaded {gcs_uri} → {table_id}")

def delete_existing_rows(table_name: str, execution_date: str, bq_client):
    timestamp_col = {
        "orders": "order_purchase_timestamp",
        "order_reviews": "review_creation_date"
    }[table_name]
    
    table_id = f"{PROJECT_ID}.{RAW_DATASET}.{table_name}"
    
    query = f"""
        DELETE FROM `{table_id}`
        WHERE DATE_TRUNC(CAST({timestamp_col} AS DATE), MONTH) = DATE_TRUNC(PARSE_DATE('%Y-%m', @execution_date), MONTH)
    """
    
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("execution_date", "STRING", execution_date)
        ]
    )
    
    bq_client.query(query, job_config=job_config).result()
    print(f"✓ Deleted {execution_date} rows from {table_id}")

def load_partitioned_table(table_name: str, execution_date: str):
    bq_client, gcs_client = get_clients()
    delete_existing_rows(table_name, execution_date, bq_client)
    bucket = gcs_client.bucket(BUCKET_NAME)
    blobs = bucket.list_blobs(prefix=f"raw/{table_name}/")
    for blob in blobs:
        if execution_date in blob.name:
            gcs_uri = f"gs://{BUCKET_NAME}/{blob.name}"
            table_id = f"{PROJECT_ID}.{RAW_DATASET}.{table_name}"
            load_gcs_to_bq(gcs_uri, table_id, bq_client, table_name)

with DAG(
    dag_id="dag_gcs_to_bq",
    start_date=datetime(2016, 9, 1),
    end_date=datetime(2018, 10, 1),
    schedule_interval="@monthly",
    catchup=True,
    max_active_runs=1,
    is_paused_upon_creation=True,
    tags=["olist", "ingestion"]
) as dag:

    load_orders = PythonOperator(
        task_id="load_orders",
        python_callable=load_partitioned_table,
        op_kwargs={
            "table_name": "orders",
            "execution_date": "{{ ds[:7] }}"
        }
    )

    load_order_reviews = PythonOperator(
        task_id="load_order_reviews",
        python_callable=load_partitioned_table,
        op_kwargs={
            "table_name": "order_reviews",
            "execution_date": "{{ ds[:7] }}"
        }
    )

    dbt_run = BashOperator(
    task_id="dbt_run",
    bash_command=f"dbt build --select stg_orders+ stg_order_reviews --project-dir {DBT_PROJECT_PATH} --profiles-dir {DBT_PROFILES_PATH} --target prod",
    # This explicitly injects the variable into the task's environment
    env={
        "GCP_KEY_PATH": "/home/airflow/keys/keyfile.json",
        **os.environ  # This is a cleaner way to include all existing env vars
        }
    )


    load_orders >> load_order_reviews >> dbt_run
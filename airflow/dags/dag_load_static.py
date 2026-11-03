import os
import json
from datetime import datetime
from airflow import DAG
from airflow.operators.python import PythonOperator
from google.oauth2 import service_account
from google.cloud import bigquery
from schema import SCHEMAS

PROJECT_ID = os.getenv("GCP_PROJECT_ID")
BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")
RAW_DATASET = "olist_raw"

def get_bq_client():
    key_json = os.getenv("GCP_SERVICE_ACCOUNT_KEY")
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(key_json)
    )
    return bigquery.Client(credentials=credentials, project=PROJECT_ID)

def load_static_table(table_name: str):
    bq_client = get_bq_client()
    gcs_uri = f"gs://{BUCKET_NAME}/raw/static/{table_name}.parquet"
    table_id = f"{PROJECT_ID}.{RAW_DATASET}.{table_name}"
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        schema=SCHEMAS[table_name]
    )
    load_job = bq_client.load_table_from_uri(
        gcs_uri, table_id, job_config=job_config
    )
    load_job.result()
    print(f"✓ Loaded {table_name}")

with DAG(
    dag_id="dag_load_static",
    start_date=datetime(2017, 1, 1),
    schedule_interval="@once",
    catchup=False,
    is_paused_upon_creation=True,
    tags=["olist", "ingestion"]
) as dag:

    load_order_items = PythonOperator(
        task_id="load_order_items",
        python_callable=load_static_table,
        op_kwargs={"table_name": "order_items"}
    )
    load_order_payments = PythonOperator(
        task_id="load_order_payments",
        python_callable=load_static_table,
        op_kwargs={"table_name": "order_payments"}
    )
    load_customers = PythonOperator(
        task_id="load_customers",
        python_callable=load_static_table,
        op_kwargs={"table_name": "customers"}
    )
    load_sellers = PythonOperator(
        task_id="load_sellers",
        python_callable=load_static_table,
        op_kwargs={"table_name": "sellers"}
    )
    load_products = PythonOperator(
        task_id="load_products",
        python_callable=load_static_table,
        op_kwargs={"table_name": "products"}
    )
    load_geolocation = PythonOperator(
        task_id="load_geolocation",
        python_callable=load_static_table,
        op_kwargs={"table_name": "geolocation"}
    )
    load_category_translation = PythonOperator(
        task_id="load_category_translation",
        python_callable=load_static_table,
        op_kwargs={"table_name": "category_translation"}
    )

    [
        load_order_items,
        load_order_payments,
        load_customers,
        load_sellers,
        load_products,
        load_geolocation,
        load_category_translation
    ]
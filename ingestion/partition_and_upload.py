import os
import json
import pandas as pd
from google.oauth2 import service_account
from google.cloud import storage

BUCKET_NAME = os.getenv("GCS_BUCKET_NAME")
KAGGLE_DATASET = "olistbr/brazilian-ecommerce"
DOWNLOAD_DIR = "raw_data"
UPLOAD_DIR = "partitioned"

# ── 1. Column type definitions ────────────────────────────────────────────────
COLUMN_TYPES = {
    "orders": {
        "order_purchase_timestamp": "datetime",
        "order_approved_at": "datetime",
        "order_delivered_carrier_date": "datetime",
        "order_delivered_customer_date": "datetime",
        "order_estimated_delivery_date": "datetime",
        "order_id": "str",
        "customer_id": "str",
        "order_status": "str",
    },
    "order_reviews": {
        "review_id": "str",
        "order_id": "str",
        "review_score": "int",
        "review_comment_title": "str",
        "review_comment_message": "str",
        "review_creation_date": "datetime",
        "review_answer_timestamp": "datetime",
    },
    "order_items": {
        "order_id": "str",
        "product_id": "str",
        "seller_id": "str",
        "order_item_id": "int",
        "shipping_limit_date": "datetime",
        "price": "float",
        "freight_value": "float",
    },
    "order_payments": {
        "order_id": "str",
        "payment_type": "str",
        "payment_sequential": "int",
        "payment_installments": "int",
        "payment_value": "float",
    },
    "customers": {
        "customer_id": "str",
        "customer_unique_id": "str",
        "customer_zip_code_prefix": "str",
        "customer_city": "str",
        "customer_state": "str",
    },
    "sellers": {
        "seller_id": "str",
        "seller_zip_code_prefix": "str",
        "seller_city": "str",
        "seller_state": "str",
    },
    "products": {
        "product_id": "str",
        "product_category_name": "str",
        "product_name_lenght": "int",
        "product_description_lenght": "int",
        "product_photos_qty": "int",
        "product_weight_g": "float",
        "product_length_cm": "float",
        "product_height_cm": "float",
        "product_width_cm": "float",
    },
    "geolocation": {
        "geolocation_zip_code_prefix": "str",
        "geolocation_city": "str",
        "geolocation_state": "str",
        "geolocation_lat": "float",
        "geolocation_lng": "float",
    },
    "category_translation": {
        "product_category_name": "str",
        "product_category_name_english": "str",
    },
}

# ── 2. Cast columns to correct types ─────────────────────────────────────────
def cast_columns(df: pd.DataFrame, table_name: str) -> pd.DataFrame:
    if table_name not in COLUMN_TYPES:
        return df
    for col, dtype in COLUMN_TYPES[table_name].items():
        if col not in df.columns:
            continue
        if dtype == "datetime":
            df[col] = pd.to_datetime(df[col], errors="coerce").astype("datetime64[us]")
        elif dtype == "int":
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        elif dtype == "float":
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
        elif dtype == "str":
            df[col] = df[col].astype(str).replace("nan", None)
    return df

# ── 3. GCS client ─────────────────────────────────────────────────────────────
def get_gcs_client():
    key_json = os.getenv("GCP_SERVICE_ACCOUNT_KEY")
    credentials = service_account.Credentials.from_service_account_info(
        json.loads(key_json)
    )
    return storage.Client(credentials=credentials)

# ── 4. Download dataset from Kaggle ──────────────────────────────────────────
def download_dataset():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.system(f"kaggle datasets download -d {KAGGLE_DATASET} -p {DOWNLOAD_DIR} --unzip")
    print("✓ Dataset downloaded")

# ── 5. Upload a single file to GCS ───────────────────────────────────────────
def upload_to_gcs(client, local_path: str, gcs_path: str):
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(gcs_path)
    blob.upload_from_filename(local_path)
    print(f"✓ Uploaded {gcs_path}")

# ── 6. Partition transactional files by month and upload ──────────────────────
def partition_and_upload(client, filename: str, date_column: str, table_name: str):
    df = pd.read_csv(f"{DOWNLOAD_DIR}/{filename}")
    df = cast_columns(df, table_name)
    df["partition_month"] = df[date_column].dt.to_period("M").astype(str)

    os.makedirs(f"{UPLOAD_DIR}/{table_name}", exist_ok=True)

    for month, group in df.groupby("partition_month"):
        local_path = f"{UPLOAD_DIR}/{table_name}/{table_name}_{month}.parquet"
        group.drop(columns=["partition_month"]).to_parquet(local_path, index=False)
        upload_to_gcs(client, local_path, f"raw/{table_name}/{table_name}_{month}.parquet")

    print(f"✓ Partitioned and uploaded {table_name}")

# ── 7. Upload static dimension files ─────────────────────────────────────────
def upload_static(client, filename: str, table_name: str):
    df = pd.read_csv(f"{DOWNLOAD_DIR}/{filename}")
    df = cast_columns(df, table_name)
    local_path = f"{UPLOAD_DIR}/{table_name}.parquet"
    df.to_parquet(local_path, index=False)
    upload_to_gcs(client, local_path, f"raw/static/{table_name}.parquet")
    print(f"✓ Uploaded static {table_name}")

# ── 8. Cleanup local files ────────────────────────────────────────────────────
def cleanup():
    import shutil
    if os.path.exists(DOWNLOAD_DIR):
        shutil.rmtree(DOWNLOAD_DIR)
        print(f"✓ Deleted {DOWNLOAD_DIR}")
    if os.path.exists(UPLOAD_DIR):
        shutil.rmtree(UPLOAD_DIR)
        print(f"✓ Deleted {UPLOAD_DIR}")

# ── 9. Main ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    client = get_gcs_client()
    download_dataset()

    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # Transactional — partitioned by month
    partition_and_upload(client, "olist_orders_dataset.csv",        "order_purchase_timestamp", "orders")
    partition_and_upload(client, "olist_order_reviews_dataset.csv", "review_creation_date",     "order_reviews")

    # Static — uploaded once
    upload_static(client, "olist_order_items_dataset.csv",       "order_items")
    upload_static(client, "olist_order_payments_dataset.csv",    "order_payments")
    upload_static(client, "olist_customers_dataset.csv",         "customers")
    upload_static(client, "olist_sellers_dataset.csv",           "sellers")
    upload_static(client, "olist_products_dataset.csv",          "products")
    upload_static(client, "olist_geolocation_dataset.csv",       "geolocation")
    upload_static(client, "product_category_name_translation.csv", "category_translation")

    cleanup()
    print("\n✓ All files uploaded to GCS")
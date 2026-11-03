# Real data now: Olist Brazilian E-Commerce

Olist is a **real, anonymised** marketplace dataset (about 100,000 orders, 2016-2018, Brazil) published on Kaggle. It needs no developer
approval: download it once and the platform loads, models, reconciles and shows it like any other source.

What the profile loads: **orders** (amount = sum of item price + freight, status, timestamps) and **payments** (one record per payment
method used, e.g. voucher + card). Olist has no refunds, settlements or OMS feed, so those checks are off. The reconciliation compares
what each order should cost with what the customer actually paid - on real data this surfaces genuine differences.

## 1. Get the files (automatic, or by hand)

**Automatic (one command, recommended):** create a Kaggle API token (Kaggle -> your profile -> *Settings* -> *API* -> *Create new token*), then

```bash
pip install -e ".[olist,bigquery,dashboard]"
export KAGGLE_USERNAME=your_kaggle_username KAGGLE_KEY=the_key_from_the_token      # or follow Kaggle's instructions for the downloaded file
gcloud auth application-default login
make olist PROJECT=YOUR_PROJECT_ID          # = python scripts/run_profile.py --profile olist --download-olist --backend bigquery --project ...
```

**By hand:**

1. https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce -> **Download** (free Kaggle account) and unzip.
2. You need these three files in one folder: `olist_orders_dataset.csv`, `olist_order_items_dataset.csv`, `olist_order_payments_dataset.csv`.

## 2. Run

```bash
export OLIST_DATA_DIR=/path/to/the/unzipped/folder
gcloud auth application-default login            # once
python scripts/run_profile.py --profile olist --backend bigquery --project YOUR_PROJECT_ID --location US
# offline instead of BigQuery:  python scripts/run_profile.py --profile olist --backend duckdb
```
The last line printed is the dashboard command (currency shown as R$, source chip "Olist (real marketplace data)"):

```bash
DASH_DATASET=platform_reporting_olist DASH_SIMULATED=0 DASH_SOURCE_LABEL="Olist (real marketplace data)" DASH_CURRENCY="R$" \
  GCP_PROJECT_ID=YOUR_PROJECT_ID BQ_LOCATION=US python -m streamlit run streamlit_app/platform_dashboard.py
```

Data lands in `raw_olist`, `platform_marts_olist`, `platform_reporting_olist` - separate from the simulated and other real datasets.
The first load of ~100k orders + ~100k payments takes a few minutes; re-running is incremental and idempotent.

## 3. Notes

* License: Kaggle lists the dataset under CC BY-NC-SA 4.0 (attribution, non-commercial) - check the dataset page and credit Olist when you show it.
* Olist timestamps carry no timezone; they are treated as UTC. Amounts are Brazilian reais.
* A delivered order whose payments do not add up to its items is a `PAY_AMOUNT_MISMATCH`; a paid-status order without any payment is `PAY_MISSING`.
  Split payments (voucher + card) are summed, so they are not false positives.
* This is historical data: the freshness check uses a generous target and "timing" findings do not occur (everything is old).
* The code is tested with a CSV set in the exact Kaggle column layout (synthetic rows in `tests/olist_fixture.py`); the first run on the
  real files is the real-world test.

## 4. Other real datasets from Asia worth adding next

* **Pakistan's Largest E-commerce Dataset** (public domain; about 500,000 orders, 2016-2018) - order status *Completed / Cancelled / Refund*,
  price, quantity, grand total, category, payment method (https://opendata.com.pk/dataset/pakistan-largest-ecommerce-dataset, also on Kaggle).
  It has real refunds, so the refund checks would apply. No payment transactions or settlements are included.
* Marketplace seller exports (Shopee / Lazada / Tokopedia seller centre order and income reports) are the other realistic source - they
  need a seller account, so they are not public.

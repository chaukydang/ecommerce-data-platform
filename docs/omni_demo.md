# Omnichannel demo (simulated): Shopee, TikTok Shop, Lazada, Website, POS

Real data exists for one channel only (Olist, see `olist_quickstart.md`), so this profile generates a **simulated** multi-channel
business to show the platform end to end. The dashboard labels it "Simulated data" everywhere.

```bash
make omni PROJECT=your-gcp-project        # ingest -> dbt -> reporting views -> reconcile -> monitor (BigQuery)
DASH_DATASET=platform_reporting_omni DASH_SIMULATED=1 DASH_CURRENCY="₫" GCP_PROJECT_ID=your-gcp-project BQ_LOCATION=US \
  python -m streamlit run streamlit_app/platform_dashboard.py
```

What the data looks like: last 12 months (~100k orders, ~34 billion VND), five channels with their own basket size, cancel and return
rates; growth (TikTok Shop fastest), weekends, payday, mega-sale days (3.3 ... 12.12, 11.11 x3), Black Friday and the Tet slow-down.
Around 0.2% of orders carry a **planted** payment/refund problem (missing payment, wrong amount, wrong currency, duplicate,
refund above payment) so the reconciliation page has real findings. Generation is deterministic per day, so re-running is idempotent.

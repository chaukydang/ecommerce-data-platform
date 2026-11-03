# Deploy the dashboard on Streamlit Community Cloud (free)

1. **Read-only service account** (run once, in a terminal where `gcloud` is logged in):
   ```bash
   P=your-gcp-project
   gcloud iam service-accounts create dashboard-reader --project=$P
   SA=dashboard-reader@$P.iam.gserviceaccount.com
   gcloud projects add-iam-policy-binding $P --member=serviceAccount:$SA --role=roles/bigquery.jobUser
   gcloud projects add-iam-policy-binding $P --member=serviceAccount:$SA --role=roles/bigquery.dataViewer
   gcloud iam service-accounts keys create key.json --iam-account=$SA
   python scripts/json_to_streamlit_secrets.py key.json > secrets.txt
   ```
2. https://share.streamlit.io -> **Create app** -> repo `chaukydang/ecommerce-data-platform`, branch `main`,
   main file `streamlit_app/platform_dashboard.py`. In **Advanced settings**: Python 3.11, and paste the content of `secrets.txt` into *Secrets*.
3. Deploy. Delete `key.json` and `secrets.txt` from your machine afterwards (they are git-ignored, never commit them).

Notes: the BigQuery sandbox deletes tables after 60 days - rerun `make omni PROJECT=...` before then. The account is read-only
(it can run queries and read data, nothing else).

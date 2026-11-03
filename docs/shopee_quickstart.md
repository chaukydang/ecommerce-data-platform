# Shopee Open Platform connector (Southeast Asia)

The `shopee` source reads **orders**, **buyer payments** and **escrow / settlement** from the Shopee Open Platform API v2 and runs them through the
same pipeline (dbt, reconciliation, monitoring, dashboard) in separate datasets (`raw_shopee`, `platform_*_shopee`, `platform_reporting_shopee`).
Shopee's escrow detail is a genuine marketplace settlement (commission, service and transaction fees, vouchers and coins, shipping rebate), which
is exactly what the platform's settlement reconciliation is built for.

> **Status.** The connector follows Shopee's public documentation and is verified against a **local mock** of the API
> (`src/ecomplatform/simulators/shopee_mock.py`). It has not been run against Shopee itself: that needs an approved Shopee developer account.
> Confirm the response field names marked "verify" in `shopee_connector.py` on the first sandbox run.

## 1. Get access (needs Shopee's approval - allow some days)

1. Register at the Shopee Open Platform (https://open.shopee.com), complete developer verification.
2. Console -> **App Management -> create an app**. Note the **Partner ID** and **Partner Key** (sandbox credentials first).
3. Authorize a shop for the app (sandbox test shop or your own shop): open the shop-authorization link; after approval Shopee redirects with a
   `code` and `shop_id`. Exchange it once for tokens (`/api/v2/auth/token/get`, signed) to obtain the first **refresh token** (valid 30 days).
4. Access tokens last 4 hours; the connector refreshes them itself. **Refresh tokens rotate** on every refresh, so the newest one is stored in
   `SHOPEE_TOKEN_FILE` (default `.shopee_tokens.json`, git-ignored; keep it private).

## 2. Run

```bash
export SHOPEE_PARTNER_ID=...   SHOPEE_PARTNER_KEY=...   SHOPEE_SHOP_ID=...
export SHOPEE_REFRESH_TOKEN=...                       # only needed the first time
export SHOPEE_API_HOST=https://partner.test-stable.shopeemobile.com    # sandbox (default). Production: https://partner.shopeemobile.com
python scripts/run_profile.py --profile shopee --backend bigquery --project YOUR_PROJECT_ID --location US
```
The command to open the dashboard is printed at the end.

## 3. What it does

| Entity | Shopee API | Mapping |
|---|---|---|
| orders | `order/get_order_list` (update_time windows of <= 15 days, cursor paging) + `order/get_order_detail` (<= 50 orders per call) | `SPE-<order_sn>`; status mapped to the platform vocabulary; epoch seconds -> UTC |
| payments | same order detail (`pay_time`, `total_amount`) | one buyer payment per paid order |
| settlements | `payment/get_escrow_detail` per COMPLETED order | gross = buyer total, fees = commission + service + seller transaction fee, adjustment = the other signed components, net = `escrow_amount` |

Every request is signed (HMAC-SHA256 over partner id + path + timestamp [+ access token + shop id]); Shopee reports errors inside HTTP 200
bodies - the client classifies them (auth -> refresh/fail, server/rate-limit -> retry, others -> fail with a clear message). Secrets never reach logs,
the audit table or alerts.

## 4. Limits

* Refunds / returns are not ingested yet (the refund rules are disabled in this profile).
* Escrow is requested per completed order (one call each); fine for a shop, use batch endpoints for very large volumes.
* `read` access to a live shop needs Shopee to approve your app; until then the platform runs on the simulator, Olist, or the mock.

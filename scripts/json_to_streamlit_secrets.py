#!/usr/bin/env python
"""Turn a Google service-account JSON key into the TOML block to paste into Streamlit Cloud -> Settings -> Secrets.

    python scripts/json_to_streamlit_secrets.py key.json [--dataset platform_reporting_omni] > secrets.txt
Never commit the key or the output (both are git-ignored by name patterns: key*.json, secrets*.txt).
"""
from __future__ import annotations

import argparse
import json

PRESETS = {
    "platform_reporting_omni": ("1", "Omnichannel demo (simulated)", "₫"),
    "platform_reporting_olist": ("0", "Olist (real marketplace data)", "R$"),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("key_file")
    ap.add_argument("--dataset", default="platform_reporting_omni", choices=sorted(PRESETS))
    a = ap.parse_args()
    k = json.load(open(a.key_file))
    sim, label, cur = PRESETS[a.dataset]
    lines = [f'GCP_PROJECT_ID = "{k["project_id"]}"', 'BQ_LOCATION = "US"', f'DASH_DATASET = "{a.dataset}"', f'DASH_SIMULATED = "{sim}"',
             f'DASH_SOURCE_LABEL = "{label}"', f'DASH_CURRENCY = "{cur}"', "", "[gcp_service_account]"]
    for f in ("type", "project_id", "private_key_id", "private_key", "client_email", "client_id", "token_uri"):
        lines.append(f"{f} = {json.dumps(k[f], ensure_ascii=False)}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

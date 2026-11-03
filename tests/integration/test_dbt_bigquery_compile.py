"""Compiles the platform dbt models for the BigQuery target (no connection, throw-away key) and validates the
generated SQL with the BigQuery dialect parser; also checks datasets, partitioning and clustering config."""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("sqlglot")
pytest.importorskip("dbt.adapters.bigquery")
rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")
import sqlglot  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.dbt, pytest.mark.skipif(shutil.which("dbt") is None, reason="dbt not installed")]


def test_platform_models_compile_to_valid_bigquery_sql(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    kf = tmp_path / "key.json"
    kf.write_text(json.dumps({"type": "service_account", "project_id": "demo-project", "private_key_id": "x", "private_key": pem,
                              "client_email": "sa@demo-project.iam.gserviceaccount.com", "client_id": "1",
                              "token_uri": "https://oauth2.googleapis.com/token"}))
    target = tmp_path / "target"
    res = subprocess.run(
        ["dbt", "--no-populate-cache", "compile", "--select", "path:models/platform", "--target", "prod",
         "--project-dir", str(ROOT / "dbt/olist"), "--profiles-dir", str(ROOT / "dbt/olist"),
         "--target-path", str(target), "--log-path", str(tmp_path / "logs"), "--vars", '{as_of: "2026-11-03 10:00:00"}'],
        env={**os.environ, "GCP_KEY_PATH": str(kf)}, capture_output=True, text=True, timeout=240)
    assert res.returncode == 0, res.stdout[-2000:] + res.stderr[-500:]
    files = glob.glob(str(target / "compiled/olist/models/platform/**/*.sql"), recursive=True)
    assert len(files) >= 18
    for f in files:
        sqlglot.parse_one(Path(f).read_text(), read="bigquery")
    manifest = json.loads((target / "manifest.json").read_text())
    models = {n["name"]: n for n in manifest["nodes"].values() if n["resource_type"] == "model" and "models/platform" in n["original_file_path"]}
    assert {n["schema"] for n in models.values()} == {"platform_staging", "platform_intermediate", "platform_marts"}
    # no partitioning: the BigQuery sandbox drops partitions older than 60 days, which would empty historical (Olist) data
    assert not models["fct_platform_orders"]["config"].get("partition_by")
    assert models["fct_platform_orders"]["config"]["cluster_by"] == ["channel", "order_status"]
    assert "generate_date_array" in Path(next(f for f in files if f.endswith("dim_date.sql"))).read_text()
    # Olist datasets keep their original names (platform schema override must not leak)
    assert {n["schema"] for n in manifest["nodes"].values() if n["resource_type"] == "model" and "models/platform" not in n["original_file_path"]} == {
        "olist_staging", "olist_intermediate", "olist_marts"}

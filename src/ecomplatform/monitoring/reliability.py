"""Historical reliability analytics over ``pipeline_run_audit`` (success metrics from the spec)."""
from __future__ import annotations

import pandas as pd
from sqlalchemy.engine import Engine

from ecomplatform import db


def reliability_summary(engine: Engine) -> pd.DataFrame:
    """Per pipeline: runs, success rate, retries, rejected records, mean/max failed-run recovery time.

    Recovery time = time from the first FAILED run of an outage until the next successful completion.
    """
    with engine.connect() as c:
        df = pd.read_sql(db.pipeline_run_audit.select(), c)
    if df.empty:
        return df
    df = df.sort_values(["pipeline_name", "started_at"])
    rows = []
    for name, g in df.groupby("pipeline_name"):
        done = g[g.status != "RUNNING"]
        ok = done[done.status.isin(["SUCCESS", "PARTIAL_SUCCESS"])]
        rec, outage_start = [], None
        for r in done.itertuples():
            if r.status == "FAILED" and outage_start is None:
                outage_start = r.started_at
            elif r.status in ("SUCCESS", "PARTIAL_SUCCESS") and outage_start is not None:
                rec.append((r.completed_at - outage_start).total_seconds() / 60)
                outage_start = None
        rows.append({
            "pipeline_name": name, "runs": len(done), "success_rate_pct": round(100 * len(ok) / len(done), 1) if len(done) else None,
            "failed_runs": int((done.status == "FAILED").sum()), "total_retries": int(done.retry_count.sum()),
            "records_loaded": int(done.records_loaded.sum()), "records_rejected": int(done.records_rejected.sum()),
            "mean_recovery_minutes": round(sum(rec) / len(rec), 1) if rec else None,
            "max_recovery_minutes": round(max(rec), 1) if rec else None,
            "open_outage": outage_start is not None,
        })
    return pd.DataFrame(rows)

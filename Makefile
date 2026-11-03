.PHONY: demo-bq install test test-unit test-integration lint demo simulator dbt-local up down

install:            ## dev install (simulator + DuckDB/dbt + test deps)
	pip install -e ".[sim,local,bigquery,dev,postgres]"

test:               ## everything that runs without cloud access
	python -m pytest tests -q

test-unit:
	python -m pytest tests/unit -q

test-integration:
	python -m pytest tests/integration -q

lint:
	ruff check src tests scripts

demo:               ## simulator -> ingest -> dbt(DuckDB) -> reconcile -> monitor; prints a report
	python scripts/run_local_demo.py

simulator:          ## SIMULATED APIs on :8000 (token from SIM_API_TOKEN, default sim-token)
	python -m ecomplatform.simulators.server

demo-bq:            ## same demo on your own BigQuery project (free sandbox OK): make demo-bq PROJECT=my-project
	python scripts/run_local_demo.py --backend bigquery --project $(PROJECT) --workdir .demo_bq --reset

dbt-local:          ## build the platform dbt models against .demo/warehouse.duckdb
	DUCKDB_PATH=.demo/warehouse.duckdb dbt build --select path:models/platform --target local \
	  --project-dir dbt/olist --profiles-dir dbt/olist

up:                 ## Airflow + platform DB + simulator (needs GCP creds, see README)
	cd airflow && docker compose up -d --build

down:
	cd airflow && docker compose down

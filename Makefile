.PHONY: db schema backfill refresh build test fresh docs clean

db:            ## start Postgres
	docker compose up -d db

schema: db     ## create the landing schema
	psql "postgresql://$${PGUSER:-air}:$${PGPASSWORD:-air}@$${PGHOST:-127.0.0.1}:$${PGPORT:-55432}/$${PGDATABASE:-airquality}" -f sql/001_schema.sql

backfill:      ## load history, e.g. make backfill FROM=2019-01-01 TO=2026-10-01
	python ingest/fetch.py --mode backfill --start $(FROM) --end $(TO)

refresh:       ## load only what is missing since the last run
	python ingest/fetch.py --mode incremental

build:         ## run the dbt models
	cd dbt_air && dbt build

test:          ## run only the data quality tests
	cd dbt_air && dbt test

fresh: refresh build  ## the full daily cycle

docs:
	cd dbt_air && dbt docs generate && dbt docs serve

clean:
	docker compose down -v

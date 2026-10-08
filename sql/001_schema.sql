-- Landing schema. Nothing here is transformed: the API's numbers arrive as they are,
-- and every correction happens downstream in dbt, so a bug in a model never destroys
-- the only copy of the source data.

CREATE SCHEMA IF NOT EXISTS raw;

-- One row per city, hour and pollutant. The primary key IS the grain, which is what
-- makes re-running a backfill safe: a second run of the same window updates in place
-- instead of doubling the table.
CREATE TABLE IF NOT EXISTS raw.measurements (
    city_id      text        NOT NULL,
    observed_at  timestamptz NOT NULL,
    pollutant    text        NOT NULL,
    value        double precision,
    unit         text        NOT NULL,
    run_id       bigint      NOT NULL,
    ingested_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT measurements_pk PRIMARY KEY (city_id, observed_at, pollutant)
);

CREATE INDEX IF NOT EXISTS measurements_observed_at_idx
    ON raw.measurements (observed_at);
CREATE INDEX IF NOT EXISTS measurements_city_pollutant_idx
    ON raw.measurements (city_id, pollutant);

-- Every run writes a row here, successful or not. This is what turns "the pipeline
-- runs daily" from a claim into something a reader can audit.
CREATE TABLE IF NOT EXISTS raw.load_runs (
    run_id         bigserial PRIMARY KEY,
    mode           text        NOT NULL,
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    status         text        NOT NULL DEFAULT 'running',
    cities         integer,
    window_start   date,
    window_end     date,
    rows_fetched   bigint      DEFAULT 0,
    rows_written   bigint      DEFAULT 0,
    api_calls      integer     DEFAULT 0,
    error          text
);

CREATE INDEX IF NOT EXISTS load_runs_started_at_idx ON raw.load_runs (started_at DESC);

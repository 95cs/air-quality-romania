"""
Pull hourly air quality from the Open-Meteo Air Quality API into raw.measurements.

Two modes:
    backfill     load an explicit date window, chunked by year
    incremental  look at what is already in the table and load only what is missing

The script is safe to re-run. Every write is an upsert on (city_id, observed_at,
pollutant), so running the same window twice updates rows instead of duplicating
them, and a run that dies halfway can simply be run again.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import logging
import os
import pathlib
import sys
import time

import psycopg2
import psycopg2.extras
import requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is a convenience, not a requirement
    pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
CITIES_CSV = ROOT / "dbt_air" / "seeds" / "cities.csv"

API_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

# The API's variable names mapped to the names used in the warehouse, with the unit
# the API documents for each. Units are stored per row rather than assumed later,
# because CO is reported in a different unit from the rest and a single hard-coded
# "ug/m3" downstream would silently inflate it by a factor of a thousand.
POLLUTANTS = {
    "pm10": ("pm10", "ug/m3"),
    "pm2_5": ("pm2_5", "ug/m3"),
    "nitrogen_dioxide": ("no2", "ug/m3"),
    "sulphur_dioxide": ("so2", "ug/m3"),
    "ozone": ("o3", "ug/m3"),
    "carbon_monoxide": ("co", "ug/m3"),
}

# European reanalysis in this API starts here.
EARLIEST = dt.date(2013, 1, 1)

log = logging.getLogger("ingest")


# --------------------------------------------------------------------------- db


def connect():
    return psycopg2.connect(
        host=os.environ.get("PGHOST", "127.0.0.1"),
        port=int(os.environ.get("PGPORT", 55432)),
        user=os.environ.get("PGUSER", "air"),
        password=os.environ.get("PGPASSWORD", "air"),
        dbname=os.environ.get("PGDATABASE", "airquality"),
    )


def load_cities() -> list[dict]:
    with CITIES_CSV.open(encoding="utf-8") as fh:
        return [
            {
                "city_id": r["city_id"],
                "city": r["city"],
                "latitude": float(r["latitude"]),
                "longitude": float(r["longitude"]),
            }
            for r in csv.DictReader(fh)
        ]


def start_run(conn, mode: str, cities: int, start: dt.date | None, end: dt.date | None) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw.load_runs (mode, cities, window_start, window_end)
            VALUES (%s, %s, %s, %s) RETURNING run_id
            """,
            (mode, cities, start, end),
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    return run_id


def finish_run(conn, run_id: int, status: str, fetched: int, written: int, calls: int, error: str | None = None):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE raw.load_runs
               SET finished_at = now(), status = %s, rows_fetched = %s,
                   rows_written = %s, api_calls = %s, error = %s
             WHERE run_id = %s
            """,
            (status, fetched, written, calls, error, run_id),
        )
    conn.commit()


def last_observed(conn) -> dict[str, dt.date]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT city_id, max(observed_at)::date FROM raw.measurements GROUP BY city_id"
        )
        return {row[0]: row[1] for row in cur.fetchall()}


UPSERT_SQL = """
INSERT INTO raw.measurements
    (city_id, observed_at, pollutant, value, unit, run_id)
VALUES %s
ON CONFLICT (city_id, observed_at, pollutant) DO UPDATE
    SET value = EXCLUDED.value,
        unit = EXCLUDED.unit,
        run_id = EXCLUDED.run_id,
        ingested_at = now()
"""


def upsert(conn, rows: list[tuple], page_size: int = 5000) -> int:
    """Insert or update rows, returning how many were actually written.

    The batching is done here rather than through execute_values' own page_size,
    because psycopg2 issues one statement per page and cur.rowcount then reports
    only the last one. Left to itself it under-counts a 57,000-row load as 2,000,
    which would make raw.load_runs - the table this project points at to prove the
    pipeline runs - quietly dishonest.
    """
    if not rows:
        return 0
    written = 0
    with conn.cursor() as cur:
        for start in range(0, len(rows), page_size):
            chunk = rows[start:start + page_size]
            psycopg2.extras.execute_values(cur, UPSERT_SQL, chunk, page_size=len(chunk))
            written += cur.rowcount
    conn.commit()
    return written


# -------------------------------------------------------------------------- api


def call_api(city: dict, start: dt.date, end: dt.date, attempts: int = 4) -> dict:
    params = {
        "latitude": city["latitude"],
        "longitude": city["longitude"],
        "hourly": ",".join(POLLUTANTS),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "timezone": "UTC",
    }
    delay = 2.0
    for attempt in range(1, attempts + 1):
        try:
            resp = requests.get(API_URL, params=params, timeout=90)
            if resp.status_code == 429:
                raise requests.HTTPError("rate limited")
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001 - deliberately broad, then re-raised
            if attempt == attempts:
                raise
            log.warning(
                "%s %s..%s failed (%s), retrying in %.0fs",
                city["city_id"], start, end, exc, delay,
            )
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def to_rows(city_id: str, payload: dict, run_id: int) -> list[tuple]:
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    rows: list[tuple] = []
    for api_name, (name, unit) in POLLUTANTS.items():
        series = hourly.get(api_name)
        if not series:
            continue
        for ts, value in zip(times, series):
            if value is None:
                # A missing hour is left out rather than stored as zero. Zero is a
                # real reading for a pollutant; absence is not, and conflating them
                # is how a gap turns into clean air.
                continue
            rows.append(
                (
                    city_id,
                    dt.datetime.fromisoformat(ts).replace(tzinfo=dt.timezone.utc),
                    name,
                    float(value),
                    unit,
                    run_id,
                )
            )
    return rows


def year_chunks(start: dt.date, end: dt.date):
    cursor = start
    while cursor <= end:
        stop = min(dt.date(cursor.year, 12, 31), end)
        yield cursor, stop
        cursor = stop + dt.timedelta(days=1)


# ------------------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=["backfill", "incremental"], default="incremental")
    ap.add_argument("--start", type=dt.date.fromisoformat)
    ap.add_argument("--end", type=dt.date.fromisoformat)
    ap.add_argument("--cities", help="comma-separated city_id list; default is all")
    ap.add_argument("--sleep", type=float, default=1.0, help="pause between API calls")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    cities = load_cities()
    if args.cities:
        wanted = {c.strip() for c in args.cities.split(",")}
        cities = [c for c in cities if c["city_id"] in wanted]
        missing = wanted - {c["city_id"] for c in cities}
        if missing:
            log.error("unknown city_id: %s", ", ".join(sorted(missing)))
            return 2
    if not cities:
        log.error("no cities selected")
        return 2

    today = dt.date.today()
    conn = connect()

    if args.mode == "backfill":
        if not args.start:
            log.error("--start is required for a backfill")
            return 2
        window_start = max(args.start, EARLIEST)
        window_end = args.end or today
    else:
        window_start, window_end = None, today

    run_id = start_run(conn, args.mode, len(cities), window_start, window_end)
    log.info("run %s started (%s, %d cities)", run_id, args.mode, len(cities))

    fetched = written = calls = 0
    try:
        seen = last_observed(conn) if args.mode == "incremental" else {}
        for city in cities:
            if args.mode == "incremental":
                # Re-fetch the last stored day as well: the most recent hours of a
                # forecast-backed series get revised, and the upsert makes
                # re-reading them free.
                city_start = seen.get(city["city_id"])
                city_start = city_start if city_start else today - dt.timedelta(days=7)
                city_end = today
                if city_start > city_end:
                    continue
            else:
                city_start, city_end = window_start, window_end

            for chunk_start, chunk_end in year_chunks(city_start, city_end):
                payload = call_api(city, chunk_start, chunk_end)
                calls += 1
                rows = to_rows(city["city_id"], payload, run_id)
                fetched += len(rows)
                written += upsert(conn, rows)
                log.info(
                    "%-14s %s..%s  %7d rows", city["city_id"], chunk_start, chunk_end, len(rows)
                )
                time.sleep(args.sleep)

        finish_run(conn, run_id, "success", fetched, written, calls)
        log.info("run %s finished: %d rows fetched, %d written, %d API calls",
                 run_id, fetched, written, calls)
        return 0
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        finish_run(conn, run_id, "failed", fetched, written, calls, repr(exc)[:2000])
        log.exception("run %s failed", run_id)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

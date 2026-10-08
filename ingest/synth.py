"""
Development fixture. Generates measurements with the same shape as the API so the
models and tests can be exercised without network access or a long backfill.

This is NOT a data source. It exists so that `make build` has something to chew on
in a fresh checkout and in CI, and so the quality tests can be shown to fail when
they should: the generator deliberately leaves gaps and plants a handful of
physically impossible values.

    python ingest/synth.py --days 400 --seed 7
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import math
import random
import sys

from fetch import POLLUTANTS, connect, load_cities, start_run, finish_run, upsert

log = logging.getLogger("synth")

# Rough central levels per pollutant, ug/m3, before seasonal and daily shaping.
BASE = {"pm10": 28.0, "pm2_5": 17.0, "no2": 24.0, "so2": 7.0, "o3": 55.0, "co": 240.0}


def shape(pollutant: str, when: dt.datetime, city_factor: float, rng: random.Random) -> float:
    """A value with a plausible seasonal and diurnal signature.

    Particulates and NO2 peak in winter and at commuting hours; ozone does the
    opposite, peaking in summer afternoons. The point is not realism for its own
    sake but that the marts have a signal to find, so a broken GROUP BY shows up as
    a flat line instead of passing unnoticed.
    """
    doy = when.timetuple().tm_yday
    hour = when.hour
    winter = math.cos(2 * math.pi * (doy - 15) / 365.25)      # +1 in January
    morning = math.exp(-((hour - 8) ** 2) / 6)
    evening = math.exp(-((hour - 19) ** 2) / 8)
    afternoon = math.exp(-((hour - 15) ** 2) / 10)

    base = BASE[pollutant] * city_factor
    if pollutant in ("pm10", "pm2_5", "so2"):
        value = base * (1 + 0.55 * winter) * (1 + 0.35 * (morning + evening))
    elif pollutant == "no2":
        value = base * (1 + 0.30 * winter) * (1 + 0.60 * (morning + evening))
    elif pollutant == "co":
        value = base * (1 + 0.40 * winter) * (1 + 0.45 * (morning + evening))
    else:  # o3
        value = base * (1 - 0.40 * winter) * (1 + 0.50 * afternoon)

    value *= rng.lognormvariate(0, 0.28)
    return round(max(value, 0.0), 2)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=400)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--gap-rate", type=float, default=0.012,
                    help="fraction of hours dropped, to mimic real sensor outages")
    ap.add_argument("--plant-bad", type=int, default=0,
                    help="insert N physically impossible values, to prove the tests bite")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s",
                        datefmt="%H:%M:%S")
    rng = random.Random(args.seed)

    cities = load_cities()
    end = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - dt.timedelta(days=args.days)

    conn = connect()
    run_id = start_run(conn, "synthetic", len(cities), start.date(), end.date())
    names = {name: unit for name, unit in POLLUTANTS.values()}

    total = 0
    try:
        for city in cities:
            # A stable per-city multiplier so cities rank consistently run to run.
            city_factor = 0.72 + (hash(city["city_id"]) % 100) / 100 * 0.75
            rows: list[tuple] = []
            when = start
            while when <= end:
                if rng.random() >= args.gap_rate:
                    for name, unit in names.items():
                        rows.append(
                            (city["city_id"], when, name,
                             shape(name, when, city_factor, rng), unit, run_id)
                        )
                when += dt.timedelta(hours=1)
            total += upsert(conn, rows)
            log.info("%-14s %7d rows", city["city_id"], len(rows))

        if args.plant_bad:
            bad = []
            for i in range(args.plant_bad):
                city = rng.choice(cities)
                when = start + dt.timedelta(hours=rng.randrange(args.days * 24))
                bad.append((city["city_id"], when, "pm2_5", -5.0 - i, "ug/m3", run_id))
            upsert(conn, bad)
            log.warning("planted %d impossible values - dbt test should now fail", args.plant_bad)

        finish_run(conn, run_id, "success", total, total, 0)
        log.info("generated %d rows across %d cities", total, len(cities))
        return 0
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        finish_run(conn, run_id, "failed", total, total, 0, repr(exc)[:2000])
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

# Air Quality Romania

An hourly data warehouse for air quality in 22 Romanian cities, built to answer one
question properly: **which cities breach the WHO 2021 air quality guidelines, how
often, and does the shape of the day say traffic or heating?**

The annual average that usually gets quoted cannot answer the second half of that,
and the second half is the one that decides what a city should actually do.

Hourly readings for six pollutants are pulled from the Open-Meteo Air Quality API,
landed in Postgres, and transformed entirely in SQL with dbt. Every refresh runs a
suite of data quality tests before the results are considered usable.

---

## Why it is built this way

**All transformation is SQL.** The ingestion script does one job — move bytes from
an API into a table — and makes no decisions about the data. Everything that shapes
a number into an answer happens in dbt models, where it is version-controlled,
tested, and legible to someone who did not write it.

**The grain is the primary key.** `raw.measurements` is keyed on
`(city_id, observed_at, pollutant)`, and every write is an upsert. That one choice
is what makes a backfill re-runnable: a run that dies at city 14 of 22 is fixed by
running it again, not by working out what to delete first.

**Incremental loading is on `ingested_at`, not `observed_at`.** The API revises its
most recent hours. A model that selected new rows by observation time would skip
exactly those corrections and leave the warehouse quietly stale while appearing to
work.

**Implausible readings are flagged, not dropped.** A negative concentration stays in
staging with `is_plausible = false`, so the quality marts can count it. It is
excluded from every aggregate. Deleting bad rows at ingestion is how a data quality
problem becomes invisible.

**A day needs 18 of 24 hours before it counts.** That is the EU's 75% data-capture
threshold. Without it, a city with four hours of data on a clean afternoon ranks
better than a city measured properly, and the ranking inverts for reasons that have
nothing to do with air.

**Completeness is published next to the results.** `mart_data_completeness` reports
what fraction of expected hours actually exist, per city and month. This comes
directly from a lesson on an earlier project, where a national statistics source
exported missing values inconsistently between counties — 91% empty rows in one, 2%
in another — so any indicator built on row counts ranked the worst-served county as
the best. Nothing in the headline numbers revealed it. Here the reader can see which
cities are thin before trusting a ranking.

---

## The WHO thresholds, and a note on checking them

`dbt_air/seeds/who_guidelines.csv` holds the WHO 2021 global air quality guideline
levels. They were cross-checked against two sources because the first disagreed:
it gave PM2.5 annual as 10 µg/m³ and SO₂ 24-hour as 50 µg/m³. Those are the **2005**
values. The 2021 revision halved PM2.5 annual to 5 and raised SO₂ 24-hour to 40.
Taking the first source would have made every city on this list look roughly twice
as compliant as it is.

| Pollutant | Annual | 24-hour |
|---|---|---|
| PM2.5 | 5 µg/m³ | 15 µg/m³ |
| PM10 | 15 µg/m³ | 45 µg/m³ |
| NO₂ | 10 µg/m³ | 25 µg/m³ |
| SO₂ | — | 40 µg/m³ |
| O₃ | 60 µg/m³ (peak season) | 100 µg/m³ (8-hour) |
| CO | — | 4 mg/m³ |

The 24-hour guidelines are 99th-percentile rules: 3–4 exceedance days a year are
tolerated. `mart_who_exceedance` therefore compares the **count** of exceedance days
against that allowance rather than flagging any day over the line.

CO is stored as 4000 µg/m³ rather than 4 mg/m³, because that is the unit the API
reports CO in. Comparing a reading of 240 against a threshold of 4 would flag every
hour of every day in every city.

---

## What is in the warehouse

```
raw.measurements          one row per city, hour and pollutant; the landing table
raw.load_runs             one row per pipeline run, successful or not

staging.stg_air__*        typed, time-zoned views; nothing filtered out

marts.dim_city                     the 22 cities, with population and size band
marts.fct_measurement_hourly       the grain; incremental
marts.mart_daily_city_pollutant    daily aggregates with the 75% capture flag
marts.mart_who_exceedance          days over guideline, per city, pollutant, year
marts.mart_diurnal_profile         mean and median by hour of day, split by season
marts.mart_data_completeness       observed hours against expected hours
```

Timestamps are stored in UTC and read in `Europe/Bucharest`. Every question a person
actually asks about air quality is a local-time question, and Romania changes offset
twice a year.

---

## Data quality tests

`dbt build` runs 40 tests. The ones that matter:

- **Grain uniqueness** on the fact table. If this fails, the primary key or the
  incremental strategy has broken and every average downstream is wrong.
- **Range tests** on concentrations, hours and percentages, via a custom generic
  test (`macros/test_within_range.sql`) rather than an external package, so the
  project has no dependency to install before it works.
- **Referential integrity** from the fact table to `dim_city`.
- **The capture rule**, asserted: no day marked valid may have fewer than 18 hours.
- **An arithmetic impossibility**: more exceedance days than measured days, which is
  what a fan-out join against the guideline seed would produce.
- **Far-future timestamps**, which would mean a date-arithmetic bug in the ingestion.

CI goes one step further and checks that the tests still *fail* when they should: it
plants impossible values, runs the suite, and fails the build if nothing is caught.
A test suite that has never failed is not evidence of anything.

---

## Running it

```bash
cp .env.example .env
make schema                                   # start Postgres, create the landing tables
make backfill FROM=2019-01-01 TO=2026-10-01   # ~45 min for 22 cities
make build                                    # run the models and the tests
```

Then, daily:

```bash
make fresh      # load what is missing, rebuild, test
```

To work on the models without waiting for a backfill, or without network access:

```bash
cd ingest && python synth.py --days 400
```

`synth.py` generates measurements with the same shape as the API — winter
particulate peaks, twin NO₂ commuting peaks, summer ozone afternoons — plus
deliberate gaps. It is a fixture for exercising the models, **not** a data source,
and nothing it produces should ever be reported as a finding.

### Scheduling

`.github/workflows/ci.yml` rebuilds the whole warehouse and runs the tests on every
push, against a throwaway Postgres, using the fixture.

`.github/workflows/refresh.yml` runs the real daily load at 04:17 UTC. It needs a
reachable Postgres; set `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD` and `PGDATABASE`
as repository secrets. Without them it exits cleanly instead of failing every
morning.

---

## Limitations

**Modelled, not measured.** Open-Meteo's historical air quality is reanalysis output
from the Copernicus atmospheric model, interpolated to a point. It is not a reading
from a monitoring station at that address. It is consistent across cities, which is
what makes comparison fair, but a specific hour in a specific street may differ from
a physical sensor. Any claim about one location on one day needs the national
monitoring network behind it.

**A city is a coordinate.** Each city is represented by a single point near its
centre. Real exposure varies enormously within a city — a ring road is not a park —
and nothing here captures that.

**Population is 2021 census.** Used only for size bands and for weighting
population-exposure figures, so it ages slowly, but it ages.

**The diurnal profile suggests a cause, it does not establish one.** Twin commuting
peaks are consistent with traffic and a flat winter elevation with domestic heating,
but neither is proof. Attribution needs source apportionment, which this project
does not do.

---

## Sources

- [Open-Meteo Air Quality API](https://open-meteo.com/en/docs/air-quality-api) — hourly data, European reanalysis from 2013
- WHO global air quality guidelines 2021 — thresholds in `dbt_air/seeds/who_guidelines.csv`

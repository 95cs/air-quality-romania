-- What fraction of the hours that should exist actually do, per city, month and
-- pollutant.
--
-- This exists because of a lesson from an earlier project: a source that exports
-- missing values inconsistently between regions will rank the worst-covered place
-- as the best, and nothing in the headline numbers reveals it. Completeness is
-- published next to the results so a reader can see which cities are thin before
-- they trust a ranking.

with bounds as (

    select
        min(observed_at_local) as first_hour,
        max(observed_at_local) as last_hour
    from {{ ref('fct_measurement_hourly') }}

),

months as (

    select generate_series(
        date_trunc('month', (select first_hour from bounds)),
        date_trunc('month', (select last_hour  from bounds)),
        interval '1 month'
    ) as month_start

),

expected as (

    -- Elapsed hours, not wall-clock arithmetic.
    --
    -- A month is not 24 x days. Romania moves the clock twice a year, so October
    -- genuinely has 745 hours and March 743. Subtracting two local timestamps gives
    -- the wall-clock difference and misses that, which made every October look
    -- 100.1% complete - more data than could exist. Converting each boundary to an
    -- absolute instant first (`at time zone` turns a local timestamp into a
    -- timestamptz) makes the subtraction real elapsed time, DST included.
    --
    -- Found by the range test on pct_complete, on the first run against real data:
    -- the synthetic fixture drops ~1% of hours at random, and that noise was just
    -- large enough to hide a one-hour surplus.

    select
        m.month_start::date as month_start,
        (
            extract(epoch from (
                least(
                    (m.month_start + interval '1 month') at time zone 'Europe/Bucharest',
                    ((select last_hour from bounds) + interval '1 hour') at time zone 'Europe/Bucharest'
                )
                - greatest(
                    m.month_start at time zone 'Europe/Bucharest',
                    (select first_hour from bounds) at time zone 'Europe/Bucharest'
                )
            )) / 3600
        )::int as expected_hours
    from months m

),

observed as (

    select
        city_id,
        pollutant,
        date_trunc('month', observed_at_local)::date as month_start,
        count(*)                                     as observed_hours
    from {{ ref('fct_measurement_hourly') }}
    group by 1, 2, 3

)

select
    o.city_id,
    o.pollutant,
    o.month_start,
    e.expected_hours,
    o.observed_hours,
    greatest(e.expected_hours - o.observed_hours, 0) as missing_hours,
    round(
        (o.observed_hours::numeric / nullif(e.expected_hours, 0)) * 100, 1
    ) as pct_complete
from observed o
join expected e on e.month_start = o.month_start

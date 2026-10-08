-- Daily aggregates, with the data-capture rule made explicit.
--
-- A day built from four hours is not a daily mean, and treating it as one is the
-- standard way a sparse city ends up looking cleaner than a well-monitored one.
-- The EU reporting threshold is 75% capture, so a day needs 18 of 24 hours before
-- it counts. Days below that are kept, flagged, and excluded downstream.

with hourly as (

    select * from {{ ref('fct_measurement_hourly') }}
    where is_plausible

),

daily as (

    select
        city_id,
        pollutant,
        date_local,
        count(*)                                                     as hours_observed,
        avg(value)                                                   as mean_value,
        min(value)                                                   as min_value,
        max(value)                                                   as max_value,
        percentile_cont(0.95) within group (order by value)          as p95_value
    from hourly
    group by 1, 2, 3

)

select
    *,
    hours_observed >= 18 as is_valid_day
from daily

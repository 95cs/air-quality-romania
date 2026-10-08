-- The shape of a day, by season.
--
-- This is the model that answers whether a city's particulate problem is traffic or
-- heating. Traffic shows two peaks, around the morning and evening commute, and
-- behaves the same all year. Domestic heating shows one long winter elevation that
-- barely moves with the clock. The policy response to each is completely different,
-- which is why the annual average alone is not a useful answer.

select
    city_id,
    pollutant,
    season,
    hour_local,
    count(*)                                             as observations,
    round(avg(value)::numeric, 2)                        as mean_value,
    round(
        (percentile_cont(0.5) within group (order by value))::numeric, 2
    )                                                    as median_value
from {{ ref('fct_measurement_hourly') }}
where is_plausible
group by 1, 2, 3, 4

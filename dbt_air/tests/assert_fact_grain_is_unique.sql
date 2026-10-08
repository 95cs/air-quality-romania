-- The grain claim, tested. If this ever returns a row, either the landing primary
-- key was dropped or the incremental strategy is inserting instead of replacing,
-- and every average in the marts is quietly wrong.

select
    city_id,
    observed_at,
    pollutant,
    count(*) as rows_at_grain
from {{ ref('fct_measurement_hourly') }}
group by 1, 2, 3
having count(*) > 1

-- The API serves a few days of forecast alongside history, which is fine, but a
-- timestamp weeks ahead means a date-arithmetic bug in the ingestion.

select *
from {{ ref('fct_measurement_hourly') }}
where observed_at > now() + interval '8 days'

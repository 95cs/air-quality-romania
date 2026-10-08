-- The 75% data-capture rule, tested rather than trusted.

select *
from {{ ref('mart_daily_city_pollutant') }}
where is_valid_day
  and hours_observed < 18

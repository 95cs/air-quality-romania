-- Implausible readings are kept in staging on purpose, but they must never reach a
-- daily average. This catches the case where the is_plausible filter is dropped
-- from mart_daily_city_pollutant during a refactor.

select d.*
from {{ ref('mart_daily_city_pollutant') }} d
where d.min_value < 0
   or d.max_value > 100000

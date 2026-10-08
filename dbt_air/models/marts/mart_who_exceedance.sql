-- How often each city breaks the WHO 2021 guidelines, by year.
--
-- Two separate comparisons, because WHO sets two kinds of limit. The 24-hour
-- guideline is a 99th-percentile rule: 3-4 exceedance days a year are tolerated,
-- so the count matters, not the fact that it is above zero. The annual guideline
-- is absolute.

with daily as (

    select * from {{ ref('mart_daily_city_pollutant') }}
    where is_valid_day

),

guideline_24h as (

    select pollutant, guideline_value, allows_exceedance_days
    from {{ ref('who_guidelines') }}
    where averaging_period = '24h'

),

guideline_annual as (

    select pollutant, guideline_value
    from {{ ref('who_guidelines') }}
    where averaging_period = 'annual'

),

yearly as (

    select
        d.city_id,
        d.pollutant,
        extract(year from d.date_local)::int                              as year_local,
        count(*)                                                         as valid_days,
        count(*) filter (where d.mean_value > g.guideline_value)         as exceedance_days,
        avg(d.mean_value)                                                as annual_mean,
        max(g.guideline_value)                                           as guideline_24h,
        max(g.allows_exceedance_days)                                    as tolerated_days
    from daily d
    join guideline_24h g on g.pollutant = d.pollutant
    group by 1, 2, 3

)

select
    y.city_id,
    y.pollutant,
    y.year_local,
    y.valid_days,
    y.exceedance_days,
    y.tolerated_days,
    y.guideline_24h,
    round((y.exceedance_days::numeric / nullif(y.valid_days, 0)) * 100, 1) as pct_days_over,
    y.exceedance_days > y.tolerated_days                                   as breaches_24h_guideline,
    round(y.annual_mean::numeric, 1)                                       as annual_mean,
    a.guideline_value                                                      as guideline_annual,
    case
        when a.guideline_value is null then null
        else y.annual_mean > a.guideline_value
    end                                                                    as breaches_annual_guideline,
    -- A year assembled from 40 valid days is not a year. Reported rather than
    -- filtered, so a reader can decide what to trust.
    y.valid_days >= 300                                                    as year_is_complete
from yearly y
left join guideline_annual a on a.pollutant = y.pollutant

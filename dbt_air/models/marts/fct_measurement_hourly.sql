{{ config(
    materialized = 'incremental',
    unique_key = ['city_id', 'observed_at', 'pollutant'],
    incremental_strategy = 'delete+insert',
    indexes = [
        {'columns': ['city_id', 'observed_at', 'pollutant'], 'unique': True},
        {'columns': ['date_local']},
        {'columns': ['pollutant']}
    ]
) }}

-- The grain of the warehouse: one row per city, hour and pollutant.
--
-- Incremental selection is on ingested_at, not on observed_at. The API revises its
-- most recent hours, so a run that corrects yesterday's figures writes new
-- ingested_at values against old timestamps; filtering on observed_at would skip
-- exactly those corrections and leave the warehouse quietly stale.

with measurements as (

    select * from {{ ref('stg_air__measurements') }}

    {% if is_incremental() %}
    where ingested_at > (
        select coalesce(max(loaded_at), '1900-01-01'::timestamptz) from {{ this }}
    )
    {% endif %}

)

select
    city_id,
    pollutant,
    observed_at,
    observed_at_local,
    observed_at_local::date                          as date_local,
    extract(hour  from observed_at_local)::int       as hour_local,
    extract(month from observed_at_local)::int       as month_local,
    extract(year  from observed_at_local)::int       as year_local,
    case extract(month from observed_at_local)
        when 12 then 'winter' when 1 then 'winter' when 2 then 'winter'
        when 3  then 'spring' when 4 then 'spring' when 5 then 'spring'
        when 6  then 'summer' when 7 then 'summer' when 8 then 'summer'
        else 'autumn'
    end                                              as season,
    value,
    unit,
    is_plausible,
    ingested_at                                      as loaded_at
from measurements

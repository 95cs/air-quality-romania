-- Typed, time-zoned view over the landing table. No filtering happens here: a row
-- that looks wrong still reaches the fact table, flagged, so the quality marts can
-- count it. Silently dropping bad rows upstream is how a data quality problem
-- becomes invisible.

with source as (

    select * from {{ source('raw', 'measurements') }}

),

shaped as (

    select
        city_id,
        pollutant,
        observed_at,

        -- Stored in UTC, read in local time. Every question a person actually asks
        -- about air quality ("is it worse at rush hour?") is a local-time question,
        -- and Romania changes offset twice a year.
        observed_at at time zone 'Europe/Bucharest' as observed_at_local,

        value,
        unit,
        run_id,
        ingested_at,

        -- Physically impossible rather than merely unusual. Concentrations cannot be
        -- negative, and the upper bounds are far above any reading ever recorded in
        -- Europe, so a trip here means a unit or parsing problem, not bad air.
        case
            when value < 0 then false
            when pollutant in ('pm10', 'pm2_5') and value > 2000 then false
            when pollutant in ('no2', 'so2', 'o3') and value > 1000 then false
            when pollutant = 'co' and value > 100000 then false
            else true
        end as is_plausible

    from source

)

select * from shaped

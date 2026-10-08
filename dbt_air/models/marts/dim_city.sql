with cities as (

    select * from {{ ref('cities') }}

)

select
    city_id,
    city,
    county,
    latitude,
    longitude,
    population_2021,
    case
        when population_2021 >= 300000 then 'large'
        when population_2021 >= 150000 then 'medium'
        else 'small'
    end as size_band
from cities

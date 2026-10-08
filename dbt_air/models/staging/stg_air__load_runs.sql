with source as (

    select * from {{ source('raw', 'load_runs') }}

)

select
    run_id,
    mode,
    status,
    started_at,
    finished_at,
    extract(epoch from (finished_at - started_at)) as duration_seconds,
    cities,
    window_start,
    window_end,
    rows_fetched,
    rows_written,
    api_calls,
    error
from source

{% test within_range(model, column_name, min_value, max_value) %}

-- Fails on any value outside physically possible bounds. Written as a custom
-- generic test rather than pulled from a package so the project has no external
-- dependency to install before `dbt build` works.

select *
from {{ model }}
where {{ column_name }} is not null
  and ({{ column_name }} < {{ min_value }} or {{ column_name }} > {{ max_value }})

{% endtest %}

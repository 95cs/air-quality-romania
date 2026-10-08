-- An arithmetic impossibility that a bad join would produce: more days over the
-- guideline than days measured. This is the test that would have caught a fan-out
-- join against the guideline seed.

select *
from {{ ref('mart_who_exceedance') }}
where exceedance_days > valid_days

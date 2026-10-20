with

-- One version of each day's report: the latest to arrive.
latest as (
    select * from {{ latest_version_rows(ref('brz_amfi__navall'), 'amfi', 'navall') }}
),

-- Plan and option are published as free text in many spellings. Only the unambiguous ones are
-- mapped; anything else stays null rather than being guessed from the scheme name.
conformed as (
    select
        scheme_code,
        nav_date,
        nav_inr,
        scheme_name,
        case
            when lower(plan_name) like 'direct%' then 'direct'
            when lower(plan_name) like 'regular%' then 'regular'
        end as plan,
        case
            when lower(option_name) like '%growth%' or lower(option_name) like '%cumulative%'
                then 'growth'
            when lower(option_name) like '%idcw%'
                or lower(option_name) like '%dividend%'
                or lower(option_name) like '%income distribution%'
                or lower(option_name) like '%payout%'
                or lower(option_name) like '%reinvestment%'
                then 'idcw'
            when lower(option_name) like '%bonus%' then 'bonus'
        end as option_type,
        isin_div_payout_or_growth,
        isin_div_reinvestment,
        _file_version,
        _source_file,
    from latest
)

select * from conformed

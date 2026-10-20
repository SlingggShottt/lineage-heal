with

-- One version of each ex-date's file: the latest to arrive. NSE reorders the same records
-- between requests, so nothing here may depend on their order.
latest as (
    select * from {{ latest_version_rows(ref('brz_nse__corporate_actions'), 'nse', 'corporate_actions') }}
),

-- The same wording is spelled many ways ("Rs 5", "Re. 5", "Rs5", "Per Sh"), sometimes with
-- stray double spaces. Matching uses a lower-cased, whitespace-collapsed copy.
described as (
    select
        *,
        lower(regexp_replace(trim(subject), '\s+', ' ', 'g')) as _text
    from latest
),

classified as (
    select
        *,
        case
            when _text like 'distribution%' then 'distribution'
            when _text like '%dividend%' and _text not like 'scheme of arrangement%' then 'dividend'
            when _text like 'face value split%' or _text like '%sub-division%' then 'split'
            when regexp_matches(_text, '^bonus \d+:\d+$') then 'bonus'
            when regexp_matches(_text, '^rights \d+:\d+') then 'rights'
            when _text like 'buy back%' or _text like 'buyback%' then 'buyback'
            when _text like 'demerger%' then 'demerger'
            when _text like 'interest payment%' then 'interest'
            else 'other'
        end as action_type
    from described
),

conformed as (
    select
        ex_date,
        symbol,
        series,
        isin,
        company_name,
        subject,
        action_type,
        case action_type
            -- One announcement can hold several payments (a regular and a special dividend): add them.
            when 'dividend' then list_sum(list_transform(
                regexp_extract_all(_text, '\br[se]\.?\s*(\d+(?:\.\d+)?)', 1),
                x -> try_cast(x as decimal(18, 4))
            ))
            -- A distribution states its total first and then the parts that make it up.
            when 'distribution' then try_cast(
                regexp_extract(_text, '\br[se]\.?\s*(\d+(?:\.\d+)?)', 1) as decimal(18, 4)
            )
        end as cash_amount_inr,
        case when action_type = 'split' then try_cast(
            regexp_extract(_text, 'from r[se]\.? ?(\d+(?:\.\d+)?)', 1) as decimal(18, 4)
        ) end as split_old_face_value_inr,
        case when action_type = 'split' then try_cast(
            regexp_extract(_text, 'to r[se]\.? ?(\d+(?:\.\d+)?)', 1) as decimal(18, 4)
        ) end as split_new_face_value_inr,
        case when action_type in ('bonus', 'rights') then try_cast(
            regexp_extract(_text, '^(?:bonus|rights) (\d+):\d+', 1) as bigint
        ) end as ratio_new,
        case when action_type in ('bonus', 'rights') then try_cast(
            regexp_extract(_text, '^(?:bonus|rights) \d+:(\d+)', 1) as bigint
        ) end as ratio_held,
        -- The currency is sometimes missing ("@ Premium 91"), so it is optional.
        case when action_type = 'rights' then try_cast(
            regexp_extract(_text, 'premium (?:r[se]\.? ?)?(\d+(?:\.\d+)?)', 1) as decimal(18, 4)
        ) end as rights_premium_inr,
        face_value_inr,
        record_date,
        book_closure_start_date,
        book_closure_end_date,
        no_delivery_start_date,
        no_delivery_end_date,
        _file_version,
        _source_file,
    from classified
)

select * from conformed

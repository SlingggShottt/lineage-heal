-- Within a day the high is never below the low, and the open, the close and the last traded
-- price all lie between them, because each is a price at which the instrument traded. A file
-- where this breaks for many rows has shifted or damaged price columns.
--
-- It is judged on the share of a file, not row by row. Over 13 weeks of real data (224,156 rows)
-- exactly one row broke the rule: IIFL in the T0 series on 16 September 2026, whose close was
-- above that series' own high, on 5,000 shares. NSE derives some closing prices from outside a
-- single series, so an isolated oddity is real data, and a per-row rule would fail the build for
-- ever over it. A damaged file breaks thousands of rows, far above half a percent of the file.
-- Nulls are left to the not_null tests.
with checked as (
    select
        trade_date,
        _source_file,
        count(*) as rows_in_file,
        count(*) filter (
            where
                high_price_inr < low_price_inr
                or open_price_inr < low_price_inr
                or open_price_inr > high_price_inr
                or close_price_inr < low_price_inr
                or close_price_inr > high_price_inr
                or last_price_inr < low_price_inr
                or last_price_inr > high_price_inr
        ) as rows_out_of_range
    from {{ ref('brz_nse__cm_bhavcopy') }}
    group by trade_date, _source_file
)

select
    trade_date,
    _source_file,
    rows_in_file,
    rows_out_of_range,
    rows_out_of_range * 1.0 / rows_in_file as share_out_of_range
from checked
where rows_out_of_range > 0.005 * rows_in_file

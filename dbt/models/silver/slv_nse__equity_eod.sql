with

-- One version of each day's file: the latest to arrive.
latest as (
    select * from {{ latest_version_rows(ref('brz_nse__cm_bhavcopy'), 'nse', 'cm_bhavcopy') }}
),

conformed as (
    select
        trade_date as price_date,
        symbol,
        series,
        isin,
        instrument_id,
        instrument_name,
        open_price_inr,
        high_price_inr,
        low_price_inr,
        close_price_inr,
        last_price_inr,
        prev_close_price_inr,
        settlement_price_inr,
        volume,
        turnover_inr,
        trades,
        -- Close against the previous close, in percent. NSE's previous close is NOT adjusted for
        -- splits, so a split day shows a large move; telling that from a fault needs corporate
        -- actions (see the price-jump check, LH-028).
        ((close_price_inr / nullif(prev_close_price_inr, 0) - 1) * 100)::decimal(18, 4) as change_pct,
        _file_version,
        _source_file,
    from latest
)

select * from conformed

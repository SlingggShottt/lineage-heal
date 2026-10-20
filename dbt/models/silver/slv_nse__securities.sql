with

-- One snapshot per day, from the latest stored version of that day's security master.
latest as (
    select * from {{ latest_version_rows(ref('brz_nse__security_master'), 'nse', 'security_master') }}
),

conformed as (
    select
        _trade_date as as_of_date,
        instrument_id,
        symbol,
        series,
        instrument_name,
        isin,
        board_lot_qty,
        -- NSE publishes par value in paise (1000 means Rs 10). Rupees here, like every other
        -- amount; the cross-feed test against corporate actions guards the scale.
        (par_value_paise / 100)::decimal(18, 2) as par_value_inr,
        issued_capital_shares,
        issue_date,
        listing_date,
        security_type_flag,
        _file_version,
        _source_file,
    from latest
)

select * from conformed

with

prices as (
    select * from {{ ref('slv_nse__equity_eod') }}
),

-- Only splits and bonuses change how many shares there are, so only they move a price. A split
-- from Rs 5 to Re 1 turns one share into five; a bonus of n for every h held turns h shares
-- into h + n. Dividends take cash out but leave the share count alone, so they do not adjust.
-- A symbol listed in several series is announced once per series, so the same announcement is
-- counted once per symbol.
share_count_changes as (
    select distinct
        symbol,
        ex_date,
        subject,
        (
            case action_type
                when 'split' then split_old_face_value_inr / nullif(split_new_face_value_inr, 0)
                when 'bonus' then (ratio_held + ratio_new) / nullif(ratio_held, 0)
            end
        )::decimal(24, 10) as share_multiplier
    from {{ ref('slv_nse__corporate_actions') }}
    where action_type in ('split', 'bonus')
),

-- Two changes on one ex-date (a split and a bonus) compound. A change whose numbers could not
-- be read stays null and is reported by the column tests, instead of passing as "no change".
changes_by_date as (
    select
        symbol,
        ex_date,
        list_reduce(list(share_multiplier), (a, b) -> (a * b)::decimal(24, 10)) as share_multiplier
    from share_count_changes
    group by symbol, ex_date
),

-- A published price is on the share count of its day, so it is divided by every change after
-- it to sit on the same footing as today's.
later_changes as (
    select
        prices.price_date,
        prices.symbol,
        prices.series,
        case
            when count(changes_by_date.symbol) = 0 then 1::decimal(24, 10)
            else list_reduce(
                list(changes_by_date.share_multiplier), (a, b) -> (a * b)::decimal(24, 10)
            )
        end as later_share_multiplier
    from prices
    left join changes_by_date
        on changes_by_date.symbol = prices.symbol
        and changes_by_date.ex_date > prices.price_date
    group by prices.price_date, prices.symbol, prices.series
),

-- Every kind of action, not only the ones that adjust prices: a reader looking at a move
-- wants to know that something was announced.
action_days as (
    select distinct symbol, ex_date from {{ ref('slv_nse__corporate_actions') }}
),

combined as (
    select
        prices.price_date,
        prices.symbol,
        prices.series,
        prices.isin,
        prices.instrument_name,
        prices.open_price_inr,
        prices.high_price_inr,
        prices.low_price_inr,
        prices.close_price_inr,
        prices.prev_close_price_inr,
        prices.volume,
        prices.turnover_inr,
        prices.trades,
        action_days.symbol is not null as has_corporate_action,
        case
            when changes_today.symbol is null then 1::decimal(24, 10)
            else changes_today.share_multiplier
        end as action_factor,
        (1 / later_changes.later_share_multiplier)::decimal(24, 10) as adjustment_factor
    from prices
    inner join later_changes
        on later_changes.price_date = prices.price_date
        and later_changes.symbol = prices.symbol
        and later_changes.series = prices.series
    left join changes_by_date as changes_today
        on changes_today.symbol = prices.symbol
        and changes_today.ex_date = prices.price_date
    left join action_days
        on action_days.symbol = prices.symbol
        and action_days.ex_date = prices.price_date
),

final as (
    select
        price_date,
        symbol,
        series,
        isin,
        instrument_name,
        open_price_inr,
        high_price_inr,
        low_price_inr,
        close_price_inr,
        prev_close_price_inr,
        volume,
        turnover_inr,
        trades,
        has_corporate_action,
        action_factor,
        adjustment_factor,
        (close_price_inr * adjustment_factor)::decimal(18, 4) as adj_close_price_inr,
        -- NSE's previous close is not restated for a split or bonus, so on an ex-date it is
        -- put on the new share count before the two are compared.
        ((close_price_inr * action_factor / nullif(prev_close_price_inr, 0) - 1) * 100)::decimal(18, 4)
            as daily_return_pct
    from combined
)

select * from final

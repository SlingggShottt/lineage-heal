-- The security master publishes par value in paise and corporate actions publish face value in
-- rupees. For an action on shares that does not change par value (a dividend, bonus, rights
-- issue or buy-back), the two feeds must agree once the master is converted to rupees. On real
-- data they matched for 1,812 of 1,816 such actions, and a master switched to rupees would
-- match none, so this catches a silent unit change that no single-feed check can see.
-- Splits are left out because par value moves, and distributions because their "face value"
-- is a unit issue price.
select
    actions.ex_date,
    actions.symbol,
    actions.series,
    actions.action_type,
    actions.face_value_inr as corporate_action_face_value_inr,
    securities.par_value_inr as master_par_value_inr
from {{ ref('slv_nse__corporate_actions') }} as actions
inner join {{ ref('slv_nse__securities') }} as securities
    on securities.as_of_date = actions.ex_date
    and securities.symbol = actions.symbol
    and securities.series = actions.series
where
    actions.action_type in ('dividend', 'bonus', 'rights', 'buyback')
    and actions.face_value_inr is not null
    and securities.par_value_inr <> actions.face_value_inr

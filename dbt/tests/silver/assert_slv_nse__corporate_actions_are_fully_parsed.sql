-- Every action of a recognised type must have the numbers that type needs. A gap means NSE
-- changed the wording of its descriptions, which is the kind of drift the parser should surface.
select
    ex_date,
    symbol,
    series,
    action_type,
    subject
from {{ ref('slv_nse__corporate_actions') }}
where
    (action_type in ('dividend', 'distribution') and (cash_amount_inr is null or cash_amount_inr <= 0))
    or (
        action_type = 'split'
        and (
            split_old_face_value_inr is null
            or split_new_face_value_inr is null
            or split_old_face_value_inr <= split_new_face_value_inr
        )
    )
    or (
        action_type in ('bonus', 'rights')
        and (ratio_new is null or ratio_held is null or ratio_new <= 0 or ratio_held <= 0)
    )

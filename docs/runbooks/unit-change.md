# Values arrive in a different unit or scale

**Fault class:** unit_change · **Feeds:** NSE bhavcopy, NSE corporate actions, AMFI NAVAll

## Symptoms
- `unexplained_price_move` or `implausible_nav_move` fails: a price or NAV moved by a scale-sized factor (tenfold, hundredfold) with no corporate action behind it. A smaller but unusual move only warns (D-30).
- `distribution_anomaly` fails on a silver table: the typical price or NAV of the day is far from that of comparable days.
- The cross-feed check `assert_par_value_matches_corporate_action_face_value` fails. NSE publishes par value in paise (RELIANCE shows 1000 for a Rs 10 face value) while the corporate actions feed uses rupees, so every instrument should differ by exactly 100. A silent switch to rupees leaves valid numbers that pass every single-feed check.
- Bronze checks are green, because the values are still valid numbers.

## Diagnosis
1. Rule out a genuine event first. A split or bonus makes a price fall by the split ratio without any fault: the 5-to-1 split of BANSALWIRE reads as an 80% fall in silver, and gold adjusts it. Check the corporate actions for the instrument and date.
2. Find the ratio. A factor of 100 means paise against rupees; 1,000 points to thousands against units; a factor equal to a published ratio is a corporate action.
3. Check whether the factor applies to the whole day or to some rows. The whole day suggests the source changed unit; a few rows suggest bad records.
4. Compare the same column on the previous day's file version, and find the first date it changed.

## Fix
- **A unit the publisher changed, inside dbt:** convert in the silver model, which already turns NSE par value from paise into rupees, and keep the published column beside it so the change can be audited. Add the new case to the model's tests.
- **A loader or parsing error, outside dbt:** if the raw file is correct and the loader mangled it, the fix is in the loader.
- **A genuine corporate action the model does not know:** the data is right and the model is incomplete; add the action handling in dbt rather than treating it as a fault.

## Prevention
- Keep the cross-feed par-value check, which catches a unit switch no single-feed check can see.
- Keep the price and NAV jump checks tied to the corporate actions feed, so a split is not a false alarm and a hundredfold move is never ignored.

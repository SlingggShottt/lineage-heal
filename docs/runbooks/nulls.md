# A required value arrives blank

**Fault class:** nulls · **Feeds:** AMFI NAVAll, NSE bhavcopy

## Symptoms
- A `not_null` test on a bronze column fails for one row or a few. In the blank-NAV drill, `not_null_brz_amfi__navall_nav_inr` fails with `Got 1 result` among about nine thousand rows.
- The AMFI silver table, the gold tables that read it and the cross-feed market summary are skipped, while the NSE side still builds.
- No other monitor fires: the row count and the typical value are unchanged.

## Diagnosis
1. Read the failing rows from the bronze model: scheme code, trade date and `_source_file`. The file hash prefix names the exact version, and an earlier version of the same day may hold the value.
2. Check whether the value is blank in the raw file itself. If it is, the publisher sent it blank. If the raw file has a value and the model reads null, the cast or the column selection is wrong.
3. Look for a later version of the same day. AMFI republishes and corrects past reports, so a blank NAV is sometimes fixed within hours.
4. Tell a blank from a zero. About 100 schemes publish a NAV of exactly 0 on an ordinary day, so a zero is not a missing value and the check must not be tightened to reject it.
5. If every row is blank, this is schema drift, not nulls.

## Fix
- **Blank in the raw file, outside dbt:** the publisher's data is incomplete. Re-download the day with the loader's refresh option to pick up a corrected report, or wait for the republication. Do not patch the value in dbt, because that invents data.
- **Raw value present, null in bronze, inside dbt:** fix the cast or the column mapping in the bronze model, for example a placeholder text the cast turns into null.

## Prevention
- Keep `not_null` on every column a downstream model depends on.
- Keep silver choosing the latest version of each day, so a corrected republication replaces a blank one without a manual step.

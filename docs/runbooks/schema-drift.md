# A publisher renames, drops or retypes a column

**Fault class:** schema_drift · **Feeds:** NSE bhavcopy, NSE corporate actions, AMFI NAVAll

## Symptoms
- A `not_null` or type test on a bronze column fails for nearly every row of one file version at once, not for a few rows. In the AMFI rename-column drill, `not_null_brz_amfi__navall_nav_inr` failed on 8,748 rows, the whole file.
- Silver and gold models downstream are skipped because of the bronze failure.
- Date columns suddenly parse as null, or a whole file loads with its columns shifted. NSE's July 2024 change to the UDiFF format is the model case: 13 columns became 34, every name changed, and dates went from `05-JUL-2024` to `2024-07-05`.
- The `volume_anomaly` monitor stays quiet: the row count is normal, only the values are empty.

## Diagnosis
1. Compare the failing file version's header with the previous day's. The bronze models select columns by header name (D-19), so a renamed column loads as nulls, not as an error. A column present yesterday and missing today, or a new one beside it, confirms the cause.
2. Check the failure count against the file's row count. Every row failing points to the schema; a handful of rows points to nulls (see the nulls runbook).
3. Find the first date the header changed. The `_source_file` hash prefix on bronze rows identifies the version, and the ingestion log shows when it arrived.
4. Decide whether the change is a rename (same meaning, new name), a removal (the data is gone) or a type change (same name, different format). A removal cannot be fixed in dbt.

## Fix
- **Rename or type change, inside dbt:** point the bronze model and its source declaration at the new column name, or cast the new format, and keep reading the old name for older files so history still loads. The change touches the bronze model, the source YAML and its tests only.
- **Removed column, or a changed file name or URL, outside dbt:** the loader or the source has changed. Draft a suggested change for the loader and file it as an issue instead of a pull request.

## Prevention
- Declare every published source column in the source YAML, so the lineage graph and a header comparison can see a change.
- Keep `not_null` on the key and value columns of each bronze model; it is what catches this fault.
- Keep a sample of the previous format as a test fixture, as the NSE July 2024 files are, so a model's handling of both can be tested.

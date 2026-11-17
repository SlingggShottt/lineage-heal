# The same record appears more than once

**Fault class:** duplicates · **Feeds:** AMFI NAVAll, NSE bhavcopy, NSE corporate actions

## Symptoms
- A `unique_combination` test fails: for AMFI the key is scheme code, NAV date and source file; for NSE it is symbol, series and date. In the duplicate-row drill, one scheme appears twice.
- `volume_anomaly` fails when a whole file is duplicated, because the day's row count is about double that of comparable days. Every row in it is valid, so value-level checks stay green.
- Aggregates in gold (advancers, decliners, turnover) are inflated, and the market summary's reconciliation checks against silver fail.

## Diagnosis
1. Separate duplicates within one file version from rows that repeat across versions. Bronze keeps every stored version of a republished day on purpose (D-21), so the same scheme appearing once per version is expected. Only a repeat inside one `_source_file` is a fault.
2. Group the failing rows by key and `_source_file` to see whether the source file holds the repeat or a join creates it.
3. If the raw file has the repeat, check whether the two rows are identical or differ (a correction published beside the original).
4. If silver has the repeat but bronze does not, the version selection or a join is at fault. Silver picks the latest version by file arrival time (D-26); a reset file time can break that.

## Fix
- **Repeat in the raw file, outside dbt:** the publisher sent duplicates. If the rows are identical, a documented deduplication rule in the silver model is acceptable (keep one row per key, say so in the model's YAML). If they differ, a human must decide which is right, so file a suggestion, not a patch.
- **Repeat created by a join or the version selection, inside dbt:** fix the join keys or the selection in the silver model.

## Prevention
- Keep `unique_combination` on the key of every bronze and silver model.
- Keep `volume_anomaly` on each feed's daily row count, so a duplicated or half-empty file is caught even when every row is valid.

# A file is missing, late or holds the wrong date

**Fault class:** late_data · **Feeds:** NSE bhavcopy, AMFI NAVAll

## Symptoms
- `file_present_when_expected` warns that a trading day has no bhavcopy, a weekend session has no file, or an AMFI day has no report (D-37). It warns instead of failing, so other layers still build.
- An `ingestion_error` signal names a feed and trade date whose download failed and was never answered by a later attempt.
- `fresh_within` fails: the newest date in a table is older than expected.
- A file arrives but holds an old date: in the wrong-date drill the AMFI report carries NAVs for an earlier day, and the bronze test that checks dates match their report fails.
- `volume_anomaly` fails for a report far smaller than comparable days.

## Diagnosis
1. Ask whether a file was expected at all. The NSE calendar seed separates a holiday from a missing file (D-36), and a weekday holiday gets a weekend-sized AMFI report that is not a fault (D-38).
2. Read the ingestion log for the feed and date. A failed attempt with an HTTP status says why (a blocked client, a server error); a no-data answer means the source had nothing yet. NSE answers a missing file with 404, while AMFI answers 200 with an HTML page, so a 200 is not proof of a report.
3. Check whether a later attempt answered the failure. If a file is now stored, the signal is stale.
4. For a wrong date, compare the dates inside the file with the requested trade date and find the version the wrong data came from.

## Fix
- **A download that failed or came early, outside dbt:** run the ingest again for that date, or the backfill. If the source is blocking the client, the loader's headers or pacing need changing, which is a suggested change, not a dbt patch.
- **A calendar that is wrong, inside dbt:** a closure or special session missing from the calendar seed makes a normal gap look like a fault, or the reverse. Correct the seed row, with its source, and the checks follow.
- **A wrong-date file, outside dbt:** the publisher served the wrong report. Re-download after it republishes; the bad version stays stored, since raw files are never overwritten, and silver takes the latest.

## Prevention
- Keep the expected-file check on every feed from its first stored file to the run date, so a gap is a signal and not silence.
- Keep the calendar seed current each year (`lineageheal calendar fetch-nse`).

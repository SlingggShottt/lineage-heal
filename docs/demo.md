# Demo script: mid-project evaluation

A rehearsed run-through of what works today, in about 10 minutes. Every command below was run for real against the live AMFI and NSE sites, and the outputs shown are what you will see (sizes and counts can differ slightly, see "Things that vary").

**What this demo proves:** real public data is downloaded politely and stored write-once, a warehouse layer checks it, and when a bad file arrives a quality check fails and points to the exact row and file version. That failure signal is what the agents will diagnose in Weeks 4–5.

**What it does not show:** the lineage graph, the agents, the pull requests and the dashboard are not built yet. Say so up front (see "Built and not built").

## Before the day

Do this 15 minutes before, on the machine you will present from.

```bash
cd lineage-heal
git checkout main && git pull
uv sync --all-extras
```

Open these tabs so nothing loads live: the repository **Actions** tab (a green check on `main`), the README architecture diagram, and, if you have set it up, the Cloudflare R2 bucket.

Use two terminals side by side, with a large font. In both, paste this block once. It uses a separate demo folder, so your development data and the injected faults never mix.

```bash
export RAW_ZONE_DIR=.data/demo LOG_LEVEL=WARNING
lh()   { uv run lineageheal "$@"; }
dbtb() { uv run dbt build --project-dir dbt --profiles-dir dbt --target dev --no-version-check "$@"; }
dbts() { uv run dbt show  --project-dir dbt --profiles-dir dbt --target dev --no-version-check "$@"; }
```

**Dry run once**, then reset, so any problem shows up now and not on stage:

```bash
lh ingest amfi --date 2026-10-16 && lh ingest nse --date 2026-10-16 && dbtb | tail -3
rm -rf .data/demo .data/dev.duckdb      # reset
```

**Pre-load the second day** (used for the schema-drift bonus), after the reset:

```bash
lh ingest amfi --date 2026-10-15
```

Leave `2026-10-16` un-ingested, so step 3 is live.

## The run-through

| Time | Step |
|---|---|
| 0:00 | 1. The problem |
| 1:00 | 2. The idea and the architecture |
| 2:00 | 3. Live ingestion |
| 4:00 | 4. What the real data taught us |
| 5:00 | 5. The warehouse, healthy |
| 6:00 | 6. A bad file arrives |
| 8:00 | 7. Bonus: schema drift |
| 9:00 | 8. Roadmap and questions |

Short on time: skip steps 4 and 7. Extra time: do the lineage page in step 5.

### 1. The problem (1 min)

Say:

- Brokers, fintech apps and research platforms run on daily public feeds from NSE, AMFI and FBIL.
- Those pipelines break often. One survey of 200 data professionals reported about 67 data incidents a month and about 15 hours to resolve each (Monte Carlo, 2023).
- In July 2024 NSE switched its end-of-day file to a new format, UDiFF. File names, column names and date formats changed overnight.
- Tools can tell you *that* something broke. Working out *why* is still manual.

### 2. The idea and the architecture (1 min)

Show the README diagram. Say:

- When a data-quality check fails, three agents take over: a Detector, an Investigator that walks a column-level lineage graph back to the root cause, and a Fixer that writes a dbt patch and verifies it in a sandbox.
- It opens a pull request, and **a human approves every fix**. There is no auto-merge.
- Today I will show the foundation those agents stand on: the data pipeline and the failure signal.

### 3. Live ingestion (2 min)

```bash
lh ingest amfi --date 2026-10-16
```

You see one line: `2026-10-16 amfi/navall stored 1190057 bytes raw/source=amfi/dataset=navall/trade_date=2026-10-16/a91aec7a24e75305__NAVHistoryReport161026.txt`. It takes about two seconds.

```bash
lh ingest nse --date 2026-10-16
lh ingest amfi --date 2026-10-16        # the same file again
lh ingest nse --date 2026-10-17         # a Saturday
```

You see `stored` for NSE, then `unchanged` for the repeat, then `no_data` for the weekend. Say:

- Files are stored exactly as published. The key contains the file's SHA-256, so an identical file is never stored twice and a changed file becomes a new object. Nothing is overwritten.
- Requests are spaced three seconds apart, retried with backoff, and a missing file on a weekend is not an error.

```bash
find .data/demo -type f
```

Point at the SHA-256 prefix in each file name. If you have R2 set up, switch the storage with one setting (`RAW_ZONE_BACKEND=r2`) and show the same file in the Cloudflare dashboard.

### 4. What the real data taught us (1 min)

Say any two of these, they are real findings from this project:

- **AMFI corrects past reports.** The 16 October report grew by about 2 KB between two downloads hours apart, and on another day AMFI changed one scheme's plan from blank to "Direct Plan". Past data is not immutable, which is why storage is write-once.
- **NSE publishes two formats side by side.** The old format was served until 5 July 2024 and UDiFF is available back to January 2024. For 5 July the two files match row for row, so we have a ground-truth pair for the July 2024 change. Show it:

  ```bash
  head -1 tests/fixtures/nse/cm_bhavcopy_old_2024-07-05_sample.csv
  head -1 tests/fixtures/nse/cm_bhavcopy_udiff_2024-07-05_sample.csv
  ```

  Thirteen columns against thirty-four, every name changed, dates from `05-JUL-2024` to `2024-07-05`.
- **A rule that looks obvious was wrong.** I expected every NAV to be positive. About 100 schemes publish exactly 0 on an ordinary day, so that check would have raised a false alarm every day.
- **NSE rejects an honest client.** It resets connections from a client that identifies itself as this project, so loaders send browser-like headers.

### 5. The warehouse, healthy (1 min)

```bash
dbtb
```

You see `Done. PASS=10 WARN=0 ERROR=0`. Say: dbt reads the stored files, drops the blank lines and headings the report mixes in, matches columns by name and types them, then runs ten checks.

```bash
dbts --limit 5 --inline "select scheme_code, left(scheme_name, 44) as scheme_name, plan_name, nav_inr, nav_date from {{ ref('brz_amfi__navall') }} where _trade_date = '2026-10-16' order by scheme_code"
```

You see a table of schemes with typed NAVs. (Do not put your own `limit` inside the query. `dbt show` adds one and it would fail.)

*Optional, if you have time:* in the second terminal run `uv run dbt docs generate --project-dir dbt --profiles-dir dbt --target dev && uv run dbt docs serve --project-dir dbt --profiles-dir dbt --target dev --port 8080`, open http://localhost:8080, open the `brz_amfi__navall` model and its lineage graph. It shows the raw source feeding the model. Say: this is table-level; the agents will use column-level lineage.

### 6. A bad file arrives (2 min)

Say: AMFI republishes a report, and one NAV is missing.

```bash
lh dev inject-fault amfi --fault blank-nav --date 2026-10-16
```

You see `injected blank-nav (nulls) into amfi/navall 2026-10-16: raw/source=...`. Say: this adds a second, damaged version of the file. The good one is untouched, because storage is write-once. The command works only on a local folder and refuses to run against R2, so test faults can never reach real data.

```bash
dbtb
```

You see `FAIL 1 not_null_brz_amfi__navall_nav_inr`, `Got 1 result, configured to fail if != 0` and `Done. PASS=9 WARN=0 ERROR=1`. Say: one missing value in about nine thousand rows, caught by a check.

```bash
dbts --limit 5 --inline "select scheme_code, left(scheme_name, 40) as scheme_name, nav_inr, _trade_date, regexp_extract(_source_file, '([0-9a-f]{16})__', 1) as file_version from {{ ref('brz_amfi__navall') }} where nav_inr is null"
```

You see one row: scheme 148921, an empty NAV, and the file version. Say: this is the evidence trail. It names the scheme, the day and the exact file. The agents will start from this failure signal, follow lineage back to the raw column, and propose a fix.

### 7. Bonus: schema drift (1 min)

Say: this is the July 2024 story. The publisher renames a column.

```bash
lh dev inject-fault amfi --fault rename-column --date 2026-10-15
dbtb
dbts --limit 10 --inline "select _trade_date, regexp_extract(_source_file, '([0-9a-f]{16})__', 1) as file_version, count(*) as rows_missing_nav from {{ ref('brz_amfi__navall') }} where nav_inr is null group by 1, 2 order by 3"
```

You see `FAIL 8748` and a table with two rows: 1 row missing a NAV on 16 October, and 8,747 on 15 October. Say: the same check fires, but the numbers tell the story. One bad value is a data problem. Every row failing at once is a changed schema, and the Investigator should trace it to the renamed column in the raw file.

### 8. Roadmap and questions (1 min)

Say: Week 3 finishes the warehouse layers and the daily schedule. Week 4 builds the lineage graph and the Detector and Investigator. Week 5 builds the Fixer, pull requests, API and dashboard. Week 6 is a benchmark of about 100 injected faults against two baselines, plus the July 2024 NSE change as a held-out natural case, then deployment.

## Built and not built

| Built and working | Not built yet |
|---|---|
| Polite loaders for AMFI and the NSE bhavcopy, with retries | NSE security master, corporate actions and FBIL loaders |
| Write-once, content-addressed raw storage (local, R2 ready) | Reading raw files from R2 in the warehouse |
| dbt project, AMFI bronze model and ten quality checks | NSE bronze, silver and gold layers |
| Five fault injectors, each proven to trip its check | Unit-change faults and the full corpus of about 100 |
| CI on every pull request, over 150 automated tests | Dagster schedule, lineage graph, agents, dashboard |

Be direct about the gaps. FBIL's site sits behind a reCAPTCHA with no plain download, so we are looking for a documented route or agreeing a replacement feed (LH-080).

## Likely questions

**Where are the agents?** Weeks 4 and 5. What exists is what they depend on: trustworthy raw data, quality checks that produce a failure signal, and fault injection for the benchmark.

**How is this different from data-observability tools?** They detect and alert. This project aims to diagnose through lineage and to propose a verified, reviewable fix.

**What if the model proposes a wrong fix?** Every patch must pass `dbt build` in a sandbox before a pull request opens, the Fixer may only change files under `dbt/`, and a human approves or rejects. There is no auto-merge.

**How will you measure success?** A benchmark of about 100 injected faults over five classes (schema drift, nulls, duplicates, unit changes, late data), compared with a rule-based baseline and a plain-LLM baseline, plus the held-out July 2024 NSE change.

**Is the data real?** Yes, public end-of-day files from NSE and AMFI, downloaded live in step 3.

**Does it cost anything?** No. Everything runs on free tiers.

**Why did the unit-change fault not appear?** Bronze checks cannot see it, because the values are still valid numbers. It needs the price and NAV jump checks planned for the next phase.

## If something goes wrong

- **No network, or a source is slow:** skip step 3 and say the files were pre-loaded. Run `lh ingest amfi --date 2026-10-16` during your dry run instead, and everything from step 5 works offline.
- **`no_data` for a date:** the source has nothing for that day. Use `2026-10-16` and `2026-10-15`, which are verified trading days.
- **A command is slow the first time:** dbt takes a few seconds to start. Fill the time with the talking points.
- **To redo the faults:** `rm -rf .data/demo .data/dev.duckdb`, then re-run the setup ingests.

## Things that vary

- AMFI republishes reports, so byte sizes and row counts (8,7xx) can differ by a few between runs. The shape of the output does not change.
- The file-version hashes in the diagnosis tables are different every time.
- The first `dbt` command in a session is slower than the rest.

# Demo script

A rehearsed run-through of what works today, in about 15 minutes. The data steps (3 to 6) were run for real against the live AMFI, NSE and RBI sites on 26 November 2026, on the files of Wednesday 25 November, the latest complete trading day, and the outputs shown are what you will see for those dates (sizes and counts differ on other dates, see "Picking the dates" and "Things that vary"). The dashboard step (7) shows sample incidents that a command stores for the purpose, and says so on screen.

**What this demo proves:** real public data from three feeds is downloaded politely and stored write-once, a warehouse of three layers checks it, and when a bad file arrives a quality check fails and points to the exact row and file version. The half that reacts to that failure exists too: a lineage graph, agents that diagnose a fault and draft a verified fix, and a dashboard where a person reviews it.

**What it does not show live, and you should say so up front:**

- **A real approval.** GitHub sign-in works on this machine and approvers get Approve and Reject, but approving merges a pull request, and the incidents are samples whose pull request numbers (9001 and 9002) deliberately do not exist. Do not click Approve on stage: it shows an error. A real approval needs a published fix, which waits on a live diagnosis (LH-114).
- **A live diagnosis by the agents.** Two rehearsals on 26 November reached the models and could not finish: Gemini answered with errors and long delays, and the time budget ran out (LH-114). A storage bug found in the first one is fixed (LH-113). The incidents in step 7 are stored samples. Do not run `lh heal` on stage until LH-114 is settled.
- **Pull requests, the benchmark results and a deployed copy.** Nothing is published from this machine, the full benchmark has not been run, and the demo runs locally (see "Built and not built").

## Before the day

Do this 20 minutes before, on the machine you will present from. You need Docker, `uv`, Node and `pnpm` (see the README for installing `pnpm`).

```bash
cd lineage-heal
git checkout main && git pull
uv sync --all-extras
pnpm --dir web install
make up                                  # Postgres, about ten seconds
```

### Picking the dates

The commands use four dates, set once in the block below. The outputs in this script are for the dates shown. To rehearse on another day, change that one line:

| Variable | Meaning | Value used here |
|---|---|---|
| `D` | The latest trading day for which all three feeds have a file. NSE posts at about 16:30 India time and AMFI in the evening (its report was still missing at 18:47 on one day we checked); FBIL's time was not measured. If a feed answers `no_data` for the day, use the day before | `2026-11-25`, a Wednesday |
| `P` | The trading day before `D` | `2026-11-23`, a Monday |
| `H` | A weekday between them when NSE was closed (see `dbt/seeds/seed_calendar_nse.csv`). If `P` and `D` are consecutive trading days, leave out every line that uses `H` | `2026-11-24`, Guru Nanak Jayanti |
| `W` | Any Saturday | `2026-11-21` |

AMFI publishes **every** day, so the calendar check warns if a day between two stored ones has no AMFI file. That is why the setup stores AMFI's report for `H`.

### The pasted block

Use two terminals side by side, with a large font. In **both**, paste this block once. It gives the demo its own raw folder, its own DuckDB file and its own Postgres database, so your development data and the faults you inject never mix.

```bash
export D=2026-11-25 P=2026-11-23 H=2026-11-24 W=2026-11-21
export RAW_ZONE_DIR=.data/demo DBT_DATA_DIR=.data/demo WAREHOUSE_PATH=.data/demo/dev.duckdb LOG_LEVEL=WARNING
export DATABASE_URL=postgresql+psycopg://lineageheal:lineageheal@localhost:5433/lineageheal_demo
lh()   { uv run lineageheal "$@"; }
dbtb() { uv run dbt build --project-dir dbt --profiles-dir dbt --target dev --no-version-check "$@"; }
dbts() { uv run dbt show  --project-dir dbt --profiles-dir dbt --target dev --no-version-check "$@"; }
```

Create the demo database, once:

```bash
docker compose exec -T postgres psql -U lineageheal -d postgres -qc "create database lineageheal_demo"
lh db upgrade
```

**Dry run once**, then reset, so any problem shows up now and not on stage:

```bash
lh ingest amfi --date $D && lh ingest nse --date $D \
  && lh ingest nse --dataset security_master --date $D \
  && lh ingest nse --dataset corporate_actions --date $D \
  && lh ingest fbil --date $D && dbtb | tail -3
```

You should see `Done. PASS=262 WARN=0 ERROR=0`. Reset:

```bash
rm -rf .data/demo
docker compose exec -T postgres psql -U lineageheal -d postgres -qc "drop database lineageheal_demo"
docker compose exec -T postgres psql -U lineageheal -d postgres -qc "create database lineageheal_demo"
lh db upgrade
```

**Pre-load the previous days** (the trading day before, and AMFI's report for the holiday), then store the dashboard's sample incidents:

```bash
lh ingest amfi --date $P && lh ingest nse --date $P && lh ingest fbil --date $P
lh ingest amfi --date $H
uv run dbt compile --project-dir dbt --profiles-dir dbt --target dev --no-version-check | tail -1
lh dev seed-demo --repository SlingggShottt/lineage-heal --first-number 9001
```

The last command prints `seeded amfi-schema-drift as incident 1`, `seeded nse-unit-change as incident 2` and `seeded amfi-late-data as incident 3`. It needs the compile step, and it refuses a database that is not on this machine. The `--first-number 9001` matters: the samples link to pull request 9001 and issue 9002 on GitHub, numbers that cannot exist, so no action on a sample can touch a real pull request (the default, 101, soon will). Leave `$D` un-ingested, so step 3 is live.

**Start the app.** In two more terminals, each with the pasted block:

```bash
make api                                 # http://localhost:8000/docs
make web                                 # http://localhost:5173
```

**GitHub sign-in (optional).** It needs the GitHub App set up as in `docs/repository_settings.md` §5, with its settings in `.env`. Set `API_PUBLIC_URL=http://localhost:8000` there, not the `.../5173/api` that the notes suggest: through the dashboard's proxy the sign-in cookie never comes back and GitHub's return fails with `invalid-state` (LH-115). Start the API after changing `.env`. The session lasts eight hours, and resetting the demo database signs everyone out.

In a fifth terminal **without** the pasted block, start Dagster for the pipeline tour. Use port 3030 if something else holds 3000, as it does on the development machine:

```bash
DAGSTER_HOME=$PWD/dagster_home uv run dagster dev -m lineageheal.orchestration.definitions -p 3030
```

Open these tabs so nothing loads live: the Dagster asset graph (`http://localhost:3030`, Lineage), the dashboard (`http://localhost:5173`), the repository **Actions** tab (a green check on `main`), and the README architecture diagram.

**Do not click Materialize in Dagster.** Dagster loads `.env` and always writes `.data/dev.duckdb`, so a run started during the demo would read your real raw zone and rebuild your real warehouse (LH-112). The tour is read-only.

## The run-through

| Time | Step |
|---|---|
| 0:00 | 1. The problem |
| 1:00 | 2. The idea and the architecture |
| 2:00 | 3. Live ingestion |
| 4:00 | 4. What the real data taught us |
| 5:00 | 5. The warehouse, healthy, and the pipeline graph |
| 7:00 | 6. A bad file arrives (and the schema-drift bonus) |
| 9:30 | 7. The dashboard: from failure to review |
| 13:30 | 8. What exists, what is next, and questions |

Short on time: skip step 4 and the schema-drift bonus in step 6. Extra time: open a runbook in step 7.

### 1. The problem (1 min)

Say:

- Brokers, fintech apps and research platforms run on daily public feeds from NSE, AMFI and FBIL.
- Those pipelines break often. One survey of 200 data professionals reported about 67 data incidents a month and about 15 hours to resolve each (Monte Carlo, 2023).
- In July 2024 NSE switched its end-of-day file to a new format, UDiFF. File names, column names and date formats changed overnight.
- Tools can tell you *that* something broke. Working out *why* is still manual.

### 2. The idea and the architecture (1 min)

Show the README diagram. Say:

- Public feeds are stored as published, then built into three layers by dbt: bronze (typed), silver (cleaned) and gold (business-ready). Dagster runs and monitors it.
- When a data-quality check fails, three agents take over: a Detector that opens an incident, an Investigator that walks a column-level lineage graph back to the root cause, and a Fixer that writes a dbt patch and verifies it in a sandbox before anything is proposed.
- It opens a pull request, and **a human approves every fix**. There is no auto-merge.
- Today I will show the pipeline, a failure, and the dashboard where that failure ends up.

### 3. Live ingestion (2 min)

```bash
lh ingest amfi --date $D
```

You see one line: `2026-11-25 amfi/navall stored 1124457 bytes raw/source=amfi/dataset=navall/trade_date=2026-11-25/aacb7251edca790a__NAVHistoryReport251126.txt`. It takes about two seconds.

```bash
lh ingest nse --date $D
lh ingest nse --dataset security_master --date $D
lh ingest nse --dataset corporate_actions --date $D
lh ingest fbil --date $D
lh ingest amfi --date $D                # the same file again
lh ingest nse --date $H                 # a market holiday
lh ingest fbil --date $H                # the same day, for the rates
lh ingest nse --date $W                 # a Saturday
```

You see `stored` for each of the three NSE datasets (the price file, the instrument list and the day's corporate actions) and for FBIL's rupee reference rates, then `unchanged` for the repeat, then `no_data` three times: NSE on the holiday, FBIL on the same day, and NSE on the Saturday. Say:

- Files are stored exactly as published. The key contains the file's SHA-256, so an identical file is never stored twice and a changed file becomes a new object. Nothing is overwritten.
- Requests are spaced three seconds apart, retried with backoff, and a missing file on a weekend or a market holiday is not an error.
- FBIL has no download of its own, so the rates come from the RBI archive, which republishes them. One date was checked against FBIL's own site and matched.

```bash
find .data/demo -type f | sort
```

You see the raw files, and under `staged/` a CSV copy of the NSE zip and of the FBIL table. Say: the warehouse tool cannot read a zip or an HTML table, so the pipeline derives a readable copy beside the original, which stays exactly as published.

```bash
lh log
```

You see one line per attempt, newest first: `stored`, `unchanged`, `no_data`, with size, hash and time. Say: every download attempt is recorded in the application database, including failures and empty days. This is how the pipeline tells a late file from a missing one, and it is what the dashboard's feeds card reads.

### 4. What the real data taught us (1 min)

Say any two of these, they are real findings from this project:

- **A market holiday still gets an AMFI report, a tiny one.** Tuesday 24 November was an NSE holiday: NSE and FBIL had no file, but AMFI published 714 schemes against 8,834 the day before and 8,273 the day after. A row-count check that does not know the calendar would call that a 92% fault, so the monitors read the NSE calendar. Show it in step 5.
- **AMFI corrects past reports.** The 16 October report was 1,190,057 bytes in one download and 1,190,150 in another, and on another day AMFI changed one scheme's plan from blank to "Direct Plan". Past data is not immutable, which is why storage is write-once, and why silver keeps the version that arrived last.
- **NSE publishes two formats side by side.** The old format was served until 5 July 2024 and UDiFF is available back to January 2024. For 5 July the two files match row for row, so we have a ground-truth pair for the July 2024 change. Show it:

  ```bash
  head -1 tests/fixtures/nse/cm_bhavcopy_old_2024-07-05_sample.csv
  head -1 tests/fixtures/nse/cm_bhavcopy_udiff_2024-07-05_sample.csv
  ```

  Thirteen columns against thirty-four, every name changed, dates from `05-JUL-2024` to `2024-07-05`.
- **NSE publishes par value in paise.** RELIANCE shows 1000 for a Rs 10 face value. Corporate actions use rupees, and every instrument checked was exactly 100 times larger. A silent switch to rupees would still pass every basic check, because the values stay valid numbers. That is a real unit-change fault, and a cross-feed check catches it.
- **FBIL quotes the yen per 100 yen.** Every other currency is per unit. Silver converts it and keeps the published figure beside the result, and a check proves the two agree, because a wrong conversion would otherwise pass every other check.
- **A rule that looks obvious was wrong.** I expected every NAV to be positive. About 100 schemes publish exactly 0 on an ordinary day, so that check would have raised a false alarm every day.
- **NSE rejects an honest client.** It resets connections from a client that identifies itself as this project, so loaders send browser-like headers.

### 5. The warehouse, healthy, and the pipeline graph (2 min)

```bash
dbtb
```

You see `Done. PASS=262 WARN=0 ERROR=0`: sixteen models, a seed and 245 checks, in about five seconds. Say: dbt reads the stored files, drops the blank lines and headings the AMFI report mixes in, matches columns by name, types them, then runs the checks. Prices, NAVs and rates are exact decimals, never floating point.

```bash
dbts --limit 5 --inline "select nav_date, dayname(nav_date) as day, count(*) as schemes from {{ ref('slv_amfi__nav') }} group by 1, 2 order by 1"
```

You see three rows: Monday 8,834 schemes, **Tuesday 714**, Wednesday 8,273. Say: Tuesday was the NSE holiday. The build still passes, because the monitors know from the NSE calendar that a holiday report is small. A half-empty report on a trading day would not match either pattern and would fail.

```bash
dbts --limit 5 --inline "select scheme_code, left(scheme_name, 44) as scheme_name, plan_name, nav_inr, nav_date from {{ ref('brz_amfi__navall') }} where _trade_date = '$D' order by scheme_code"
```

You see a table of schemes with typed NAVs. (Do not put your own `limit` inside the query. `dbt show` adds one and it would fail.) Then the NSE side:

```bash
dbts --limit 5 --inline "select symbol, series, close_price_inr, volume, turnover_inr, trade_date from {{ ref('brz_nse__cm_bhavcopy') }} where symbol in ('RELIANCE','TCS','INFY','HDFCBANK') and series = 'EQ' and trade_date = '$D' order by symbol"
```

You see four large caps for 25 November: HDFCBANK 846.20, INFY 1,130.00, RELIANCE 1,373.90 and TCS 2,517.30, with their volumes and turnover.

Then the corporate actions, which arrive as free text and become numbers in silver:

```bash
dbts --limit 5 --inline "select symbol, action_type, cash_amount_inr, ratio_new, ratio_held, rights_premium_inr from {{ ref('slv_nse__corporate_actions') }} where ex_date = '$D' order by symbol"
```

You see three actions: BONLON, a rights issue of 1 for 1 at a premium of Rs 20; VRAJ, a dividend of Re 0.60; and ZEEL, a buy-back. Say: NSE publishes these as sentences such as "Rights 1:1 @ Premium Rs 20/-". Silver parses each into a type and its numbers, and a check fails the build if a recognised type loses its numbers. Gold uses them to adjust prices for splits and bonuses only, since dividends do not change what a price means. There is no split in today's data. In an earlier rehearsal, on 16 October, BANSALWIRE split each Rs 5 share into five Re 1 shares: NSE does not restate its previous close, so the feed alone read as an 80% fall, while gold, reading the corporate actions, showed -2.3%.

And the third feed, FBIL's rates in silver:

```bash
dbts --limit 5 --inline "select rate_date, currency, rate_inr::varchar as rate_inr, quoted_rate_inr::varchar as quoted_rate_inr, quoted_units from {{ ref('slv_fbil__reference_rates') }} where rate_date = '$D' order by currency"
```

You see four rows for 25 November: EUR 101.128000, GBP 120.967000, USD 92.489500 and JPY **0.580000**, with the published JPY figure 58.0000 and `quoted_units` 100 beside it. Say: the yen was published per 100 yen, silver gives every currency per one unit, and the published figure and its units stay next to the converted rate so anyone can check it.

```bash
dbts --limit 5 --inline "select rate_date, currency, rate_inr::varchar as rate_inr, prev_rate_date, days_since_prev_rate, rate_change_pct::varchar as change_pct from {{ ref('gld_fx_reference_rates') }} where rate_date = '$D' order by currency"
```

You see each rate beside the previous one: `prev_rate_date` is **23 November** and `days_since_prev_rate` is **2**, because FBIL published nothing on the 24th. USD is down 0.3312%, EUR 0.8941%, GBP 0.6762% and JPY 0.6679%. Say: "the previous rate" means the last day that had one, not yesterday, so a holiday shows up as a wider gap and not as a missing value.

**The pipeline graph.** Switch to the Dagster tab (Lineage). You see every table as a box in four groups, 22 in all: 5 raw files, 7 bronze, 6 silver and 4 gold, with arrows for what reads what and the result of each dbt check on its box. Say: this graph is the pipeline the agents reason about, and each box shows when it was last built and whether its checks passed. Boxes that have not been run in this Dagster instance say so. Do not click Materialize.

### 6. A bad file arrives (2.5 min)

Say: AMFI republishes a report, and one NAV is missing.

```bash
lh dev inject-fault amfi --fault blank-nav --date $D
```

You see `injected blank-nav (nulls) into amfi/navall 2026-11-25: raw/source=...`. Say: this adds a second, damaged version of the file, and `lh log --limit 1` shows it as a new stored version. The good one is untouched, because storage is write-once. The command works only on a local folder and refuses to run against cloud storage.

```bash
dbtb
```

You see `FAIL 1 not_null_brz_amfi__navall_nav_inr`, `Got 1 result, configured to fail if != 0` and `Done. PASS=221 WARN=0 ERROR=1 SKIP=40`. Say: one missing value in about eight thousand rows, caught by a check. The 40 skipped items are the AMFI silver table, the gold tables that read it, and their checks: the failure stopped everything downstream of it, so the bad value never reaches a number a consumer reads.

```bash
dbts --limit 5 --inline "select scheme_code, left(scheme_name, 40) as scheme_name, nav_inr, _trade_date, regexp_extract(_source_file, '([0-9a-f]{16})__', 1) as file_version from {{ ref('brz_amfi__navall') }} where nav_inr is null"
```

You see one row: scheme 148921, an empty NAV, and the file version. Say: this is the evidence trail. It names the scheme, the day and the exact file. The agents start from this failure signal, follow lineage back to the raw column, and propose a fix.

**Bonus: schema drift (1 min).** Say: this is the July 2024 story. The publisher renames a column.

```bash
lh dev inject-fault amfi --fault rename-column --date $P
dbtb
dbts --limit 10 --inline "select _trade_date, regexp_extract(_source_file, '([0-9a-f]{16})__', 1) as file_version, count(*) as rows_missing_nav from {{ ref('brz_amfi__navall') }} where nav_inr is null group by 1, 2 order by 3"
```

You see `FAIL 8835` and a table with two rows: 1 row missing a NAV on 25 November, and 8,834 on 23 November. Say: the same check fires, but the numbers tell the story. One bad value is a data problem. Every row failing at once is a changed schema, and the Investigator should trace it to the renamed column.

### 7. The dashboard: from failure to review (4 min)

Say: when a check fails, the failure becomes an incident that a person reviews. These three are stored samples, built from the same tables the agents write to, and the pull request and issue numbers on them are placeholders. Switch to the dashboard tab, `http://localhost:5173`.

**Overview.** Three cards. *Feeds* lists the newest stored file for each dataset, five rows including FBIL, all `ok` for 25 November (this reads the ingestion log you saw in step 3). *Latest run* shows a failed run, a sample: one error and three skipped models. *Open incidents* lists the three samples. Say: this is the screen an on-call engineer opens first.

**Incidents.** The list has filters for status, fault class and feed. You see three incidents: an AMFI schema change and an NSE unit change, each with a fix proposed, and a late-data incident with a suggestion filed.

**Open the first incident, the AMFI schema change.** Walk down the page:

- **Root cause.** The AMFI report publishes the NAV under the header `NAV` instead of `Net Asset Value`. The bronze model picks columns by header name, so every row loads with a null NAV. Confidence 93%.
- **Lineage.** An interactive graph of seven nodes, from the raw column through bronze and silver to the gold tables, with the path from the root cause to the failing check highlighted. Say: the agent found this by walking the graph backwards from the failing column, not by guessing.
- **Fix.** A dbt patch shown as a side-by-side diff, with the sandbox verdict: it fixed one failing node and broke nothing. The patch only touches files under `dbt/`, and it was built and tested in a sandbox copy of the warehouse before anything was proposed.
- **Decision.** Signed out, the page is read-only. Signed in as an approver, you also get Approve and Reject. Say: only GitHub accounts on an allowlist can approve or reject, rejection needs a reason, and the server checks that on every request, so hiding a button is not the protection. Do not approve a sample: its pull request does not exist, so it would only show an error.

**Open the second incident, the NSE unit change.** From 24 November the prices arrive in paise: the mean close is 100 times the previous day's, while the row count and the column names are unchanged. Say: this is the fault no basic check sees, because every value is still a valid number. The fix is verified in the sandbox but has no pull request yet.

**Open the third incident, the late file.** AMFI publishes every day, 23 November was a Monday, and both download attempts ended in HTTP 503, so no file was ever stored. Say: the cause is outside the dbt project, so the system does not open a pull request. It files an issue with a suggested command, `uv run lineageheal ingest amfi --date 2026-11-23`, because the fixer may only change dbt files.

**Runbooks.** Five short procedures, one per fault class: nulls, duplicates, schema drift, unit change and late data. Say: the Investigator retrieves these, and similar past incidents, while it works.

### 8. What exists, what is next, and questions (1.5 min)

Say: the pipeline, the lakehouse, the agents, the API and the dashboard are built and tested. What is left is proving it: a benchmark of 100 injected faults against a rule-based baseline and a plain language model, including the real July 2024 NSE change as a held-out case, then deployment and the write-up. Be direct about the two things found while preparing this demo: a live diagnosis does not finish yet because the model providers answer with errors and long delays (logged), and Dagster runs ignore a demo setup (also logged). A storage bug found on the way has been fixed.

## Built and not built

| Built and working | Not built yet |
|---|---|
| Polite loaders for three feeds: AMFI NAVs, three NSE datasets and FBIL's rates (via the RBI archive), with retries and a block stopping a backfill | Cloud storage in use: the R2 code is unit-tested against a fake client but has not run on a real bucket |
| Write-once, content-addressed raw storage on local disk, and an ingestion log in Postgres | Scheduled runs on GitHub Actions and a deployed copy (the API on Render and the dashboard on Cloudflare Pages) |
| dbt project: bronze models for five datasets, six silver models (latest version of each day, conformed units and vocabulary, parsed corporate actions, FBIL's rates one row per currency with the yen per unit, a cross-feed par-value check, a calendar), four gold tables (split- and bonus-adjusted prices, NAVs and FBIL's rates each beside their previous value, and a daily summary across equity and funds), and 245 quality checks | A plausibility band on FX rates (LH-087), FBIL's holiday calendar (LH-108), and FX in the daily summary (LH-091) |
| Dagster assets for every dataset and model, with every dbt test as a check, and a record of each run's failure signals | Dagster runs that respect a demo setup (LH-112) |
| Column-level lineage graph, the Detector, the Investigator with its tools, runbooks and similar-incident retrieval, the Fixer with a path allowlist and a sandbox verifier, drafted issues for faults outside dbt, and the GitHub App client | A live diagnosis that completes on real data (LH-114), pause and resume at review (LH-097), OpenLineage events (LH-037, LH-038) and LangSmith tracing (LH-043) |
| FastAPI backend with GitHub sign-in, an approver allowlist, sessions and approve and reject endpoints; the GitHub App is set up and signing in works on this machine | A real approval, which needs a published pull request (LH-114), and sign-in through the dashboard's proxy (LH-115) |
| Dashboard: overview, incident list with filters, incident page with root cause, lineage graph and side-by-side diff, approve and reject controls, and runbooks | The benchmark screen (LH-075) and a browser end-to-end test (LH-077) |
| Benchmark foundations: a corpus of 100 faults with seed and test splits, injectors for AMFI and NSE, a resumable runner, both baselines, and LineageHeal as a benchmark system | Benchmark results, the July 2024 replay, the metrics report and the full run (LH-071 to LH-074) |
| CI on every pull request, and about 1,800 automated tests: 1,395 Python, 222 dbt scenarios, 166 for the dashboard and 21 against a real Postgres | The dashboard's tests in CI (LH-099) and secret scanning over a diff in CI (LH-081) |

## Likely questions

**Where are the agents?** Built and tested with recorded and fake model responses, so the tests never call a real model. A live run with real keys reached the models twice on 26 November and did not finish: Gemini answered with errors and long delays and the time budget ran out (LH-114). A storage bug found in the first run is fixed (LH-113). The incidents in the dashboard are stored samples.

**How is this different from data-observability tools?** They detect and alert. This project aims to diagnose through lineage and to propose a verified, reviewable fix.

**What if the model proposes a wrong fix?** Every patch must pass `dbt build` in a sandbox before a pull request opens, the Fixer may only change files under `dbt/`, and a human approves or rejects. There is no auto-merge. A cause outside dbt gets an issue, never a pull request.

**How will you measure success?** A benchmark of about 100 injected faults over five classes (schema drift, nulls, duplicates, unit changes, late data), compared with a rule-based baseline and a plain-LLM baseline, plus the held-out July 2024 NSE change. The corpus, injectors, runner and baselines exist; the full run does not yet.

**Is the data real?** Yes, public end-of-day files: AMFI's NAVs, NSE's prices, instrument list and corporate actions, and FBIL's reference rates, downloaded live in step 3.

**Why do FBIL's rates come from the RBI?** FBIL's own site is a JavaScript page behind a captcha with no file to download. The RBI's reference-rate archive republishes FBIL's rates and is a plain form. One date was compared with FBIL's site (USD, 20 November 2026) and matched. RBI's crawler rules could not be read, so the loader is polite and a hand-downloaded file is the fallback.

**What happens when a source publishes a corrected file?** Bronze keeps every version. Silver keeps the one that arrived last, decided by when each file was stored. A correction replaces the old values; a bad republication that fails a bronze check stops the build before silver.

**Does it cost anything?** No. Everything runs on free tiers or locally, including the Gemini and Groq model tiers.

**Is it deployed, and does it use cloud storage?** No, on purpose. The demo runs locally. Cloud storage (Cloudflare R2) is supported in code and unit-tested against a fake client, but it is not switched on, because even its free tier needs a payment method on file. The same code path writes to either.

**What happens if NSE ships the old file format again?** The model rejects it, because the column names do not match. This is tested with NSE's real pre-July-2024 file: alone it fails the model, and next to a healthy file it shows up as missing values. That is the July 2024 change in miniature, and it is the held-out case for the benchmark.

**Why is the NSE unit change only a sample?** Bronze checks cannot see one, because the values are still valid numbers. Silver catches the one we know about (par value in paise against face value in rupees), and that is proved in the tests; a command to inject it live is part of the benchmark work.

## If something goes wrong

- **No network, or a source is slow:** skip step 3 and say the files were pre-loaded. Run the dry-run ingests during setup instead and keep the data, and everything from step 5 works offline.
- **`no_data` for a date that should have a file:** the source has nothing for that day yet, or it was a holiday. Check "Picking the dates", and use the day before if today's file is not out.
- **The healthy build warns `file_present_when_expected_slv_meta__calendar_`:** a day between two stored ones has no file where the calendar expects one. AMFI publishes every day, so store its report for `$H`; NSE and FBIL files are only expected on their own trading days.
- **`error: Could not write to the ingestion log`:** Postgres is not running or the demo database is missing. Run `make up`, then the create-database and `lh db upgrade` steps.
- **`error: The manifest has no compiled SQL`:** run the `dbt compile` line from the setup, then `lh dev seed-demo` again.
- **Signing in ends with `invalid-state`:** `API_PUBLIC_URL` in `.env` is set to the dashboard's proxy. Set it to `http://localhost:8000`, restart the API and try again from the dashboard (LH-115).
- **GitHub says the App must be configured with a callback URL:** add `http://localhost:8000/auth/callback` under "Add redirect URI" in the App's settings, and save.
- **The overview shows no feeds, or "late":** the demo database starts empty. Feeds appear as step 3 stores files.
- **The dashboard shows no incidents:** `lh dev seed-demo --repository SlingggShottt/lineage-heal --first-number 9001` stores the three samples, and `--reset` removes exactly those.
- **Port 3000 is taken:** Dagster here runs on 3030, as in the setup. Port 5173 in use means an earlier dashboard server is still running; stop it before `make web`.
- **A command is slow the first time:** dbt takes a few seconds to start. Fill the time with the talking points.
- **To redo the faults:** stop `make api` first, because a database that something is connected to cannot be dropped. Then run the reset block in the setup, and the setup ingests, the `dbt compile` line and `lh dev seed-demo`.

## Things that vary

- AMFI republishes reports, so byte sizes and row counts (8,8xx on a trading day) can differ by a few between runs. The shape of the output does not change.
- The file-version hashes in the diagnosis tables are different every time.
- On another date the prices, rates and corporate actions all differ, and there may be a split to show instead of the rights issue.
- The first `dbt` command in a session is slower than the rest, and the first model call of a session can take most of a minute.
- The dashboard's sample incidents and run carry dates relative to the day they were stored, and say nothing on screen that they are samples.

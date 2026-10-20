# Design and Architecture — LineageHeal

| | |
|---|---|
| **Status** | Baseline for build |
| **Last updated** | Week 2 |
| **Related** | [SRS](SRS.md) · [Tech stack](techstack.md) · [Style guide](style_guide.md) · [Context](context.md) |

## 1. Purpose

This document explains how LineageHeal is put together: its components, data flows, agent design, data models, deployment and the reasoning behind them. Requirements live in the SRS; this document describes how they are met. Significant decisions are summarised in [context.md](context.md#key-decisions) and recorded in detail as ADRs in `docs/adr/`.

## 2. System context

```mermaid
flowchart LR
    subgraph Sources["Public data publishers"]
        NSE["NSE"]
        AMFI["AMFI"]
        FBIL["FBIL"]
    end

    LH["LineageHeal"]

    subgraph Services["External services"]
        LLM["Gemini / Groq"]
        LS["LangSmith"]
        GH["GitHub<br/>repo, App, Actions"]
    end

    ENG["Approver"]
    VIS["Viewer"]

    Sources -- daily files --> LH
    LH -- prompts --> LLM
    LH -- traces --> LS
    LH -- PRs, issues --> GH
    GH -- schedules, runs --> LH
    ENG -- review, approve --> LH
    VIS -- browse read-only --> LH
```

## 3. Runtime topology

```mermaid
flowchart TB
    subgraph GA["GitHub Actions"]
        PIPE["pipeline.yml<br/>ingest + dbt"]
        HEAL["heal.yml<br/>agents"]
        BENCH["benchmark.yml"]
        CI["ci.yml"]
    end

    subgraph Data["Data plane"]
        R2[("Cloudflare R2<br/>raw files")]
        MD[("MotherDuck<br/>prod warehouse")]
        DDB[("Local DuckDB<br/>sandbox, bench")]
    end

    PG[("Neon Postgres + pgvector<br/>app state, lineage, knowledge,<br/>Dagster runs, agent checkpoints")]

    subgraph Serve["Serving"]
        API["FastAPI on Render"]
        WEB["React on Cloudflare Pages"]
    end

    PIPE --> R2
    PIPE --> MD
    PIPE --> PG
    PIPE -- failure --> HEAL
    HEAL --> PG
    HEAL --> DDB
    HEAL --> MD
    HEAL -- PRs, issues --> GHR["GitHub repository"]
    BENCH --> DDB
    BENCH --> PG
    WEB --> API
    API --> PG
    API -- merge, close, dispatch --> GHR
```

Design choices worth noting:

- **Heavy work runs in GitHub Actions, not on the API host.** Render's free instance has 512 MB of memory and sleeps when idle, so the API only serves reads and records decisions. Healing, benchmark runs and post-decision indexing all execute as workflows.
- **Two warehouses, one dbt project.** `prod` targets MotherDuck. `dev`, `ci` and `bench` target local DuckDB files. The sandbox and the benchmark never touch MotherDuck, which protects its free compute allowance.

## 4. Repository structure

See the repository map in [CLAUDE.md](../CLAUDE.md#repository-map). Each Python subpackage under `src/lineageheal/` owns one concern and depends only on `core` and on packages below it in this order:

```
core  ←  store  ←  ingestion, lineage, llm, vcs, rag  ←  agents  ←  orchestration, api, benchmark
```

`api` and `orchestration` never import each other. A command-line entry point, `lineageheal` (built with Typer), exposes `ingest`, `backfill`, `detect`, `heal`, `index` and `bench` commands so that GitHub Actions and developers use the same code path.

## 5. Data architecture

### 5.1 Raw zone (R2)

```
raw/source=<source>/dataset=<dataset>/trade_date=<YYYY-MM-DD>/<sha256-prefix>__<original_filename>
```

- Files are stored exactly as published (compressed where the source compresses them).
- The SHA-256 prefix (first 16 hex characters; the full digest is stored as object metadata) makes a changed re-publication a new object instead of an overwrite (FR-ING-05). An identical file resolves to the existing key and is skipped. Reading a file re-checks its hash against the key.
- File names are reduced to `A-Za-z0-9._-` before they enter a key.
- `RAW_ZONE_BACKEND` selects `r2` (the bucket) or `local` (a directory, `.data/raw` by default). Tests, offline development and benchmark sandboxes use `local`, so they cannot touch the production bucket.
- Every attempt is recorded in the `ingestion_log` table (FR-ING-06).
- Benchmark and sandbox copies live outside the raw zone (`bench/` prefix or a local directory), so injected faults can never contaminate production data.

### 5.2 Source datasets

| Source | Dataset | Bronze model |
|---|---|---|
| NSE | `cm_bhavcopy` (CM-UDiFF) | `brz_nse__cm_bhavcopy` |
| NSE | `security_master` | `brz_nse__security_master` |
| NSE | `corporate_actions` | `brz_nse__corporate_actions` |
| AMFI | `navall` | `brz_amfi__navall` |
| FBIL | `reference_rates` | `brz_fbil__reference_rates` |

#### Source access details

Filled in by the Week 2 data spike (LH-012, LH-013).

| Dataset | URL pattern | Format | Headers needed | Publication time (IST) | History available | Reachable from Actions |
|---|---|---|---|---|---|---|
| NSE `cm_bhavcopy` | `https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip` | Zip holding one CSV: 34 columns, ISO dates, LF endings, two-decimal prices, about 3,700 rows | Browser-like User-Agent and `Accept` headers; see [NSE findings](#nse-findings) | About 16:30 IST on the trade date | UDiFF back to at least 2 Jan 2024; old format until 5 Jul 2024 | Not tested yet (check in LH-033) |
| NSE `security_master` | `https://nsearchives.nseindia.com/content/cm/NSE_CM_security_DDMMYYYY.csv.gz` | Gzipped CSV with 120 named columns, about 38,100 rows, regular (no ragged or quoted lines) | As above | About 20:20 IST | Not available for 2 Jan 2024 (404); earliest date unknown | Not tested yet |
| NSE `corporate_actions` | `https://www.nseindia.com/api/corporates-corporateActions?index=equities&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY` | JSON array of objects | As above; answered without cookies in the spike | Not measured | Date ranges back to at least Jan 2024 | Not tested yet |
| AMFI `navall` | `https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx?frmdt=DD-Mon-YYYY&todt=DD-Mon-YYYY` (set both to the same date). Live snapshot: `https://portal.amfiindia.com/spages/NAVAll.txt` | Semicolon-delimited text, UTF-8, CRLF; see [AMFI findings](#amfi-findings) | None; the default curl User-Agent worked | Evening; not yet published at 18:47 IST on the same day, and the live file was last modified 15:27 IST on 19 Oct 2026 | From at least 2 Jan 2024 (tested), one request per date | Not tested yet (check in LH-033) |
| FBIL `reference_rates` | Not found. `fbil.org.in` is a JavaScript application with reCAPTCHA and exposes no plain file URL (LH-080) | Unknown | Unknown | Unknown | Unknown | Not tested |

#### NSE findings

Measured on 19 October 2026 (LH-012) with about 16 requests, spaced at least four seconds apart, from a home network (not yet from a GitHub Actions runner).

- **The project User-Agent is rejected.** NSE sits behind Akamai. A request identifying itself as `lineageheal/0.1` was reset (curl error 92, an HTTP/2 stream reset), with or without `Accept` headers. A Chrome-style User-Agent with `Accept` and `Accept-Language` headers was served over HTTP/2 and HTTP/1.1. NSE loaders therefore send a browser-like User-Agent (as the data rules in CLAUDE.md anticipate), keep the three-second spacing, and never retry in a tight loop.
- **The loader** (`ingestion/nse.py`) stores the zip exactly as published after checking it is an intact zip (a damaged, truncated or HTML response is rejected, never stored), and reports a 404 as `no_data`. A 404 is normal for weekends and holidays but is also what NSE returns before publication or when a file is genuinely missing, so telling those apart needs the exchange calendar (LH-016). Verified live: the stored zip for 16 Oct 2026 was byte-identical (same SHA-256) to a manual download.
- **The reference loaders** (`NseSecurityMasterLoader`, `NseCorporateActionsLoader`) share the bhavcopy loader's flow: the file is validated (a complete gzip, or a JSON list), stored as published, and a 404 is `no_data`. The corporate actions API filters by **ex-date**: a one-day query returns that day's actions (4 on 16 Oct 2026) or `[]`, which is reported as `no_data` and not stored. Two requests seconds apart returned identical bytes, but hours later the same four records came back in a different order, so a later ingest stores a new version with the same content. Bronze reads every version, and silver must deduplicate corporate actions by content (D-21), never by position. A security master for 2 Jan 2024 is `no_data`. Both are loaded with `lineageheal ingest nse --dataset security_master|corporate_actions`.
- **A missing file is HTTP 404.** Saturday 17 Oct 2026 and the old-format URL for 8 Jul 2024 both returned a 3.4 KB HTML error page with status 404. This differs from AMFI, which answers 200 with an HTML page.
- **Two formats exist side by side.** The old files live at `.../content/historical/EQUITIES/YYYY/MON/cmDDMONYYYYbhav.csv.zip` and were served up to 5 Jul 2024 (404 from 8 Jul). UDiFF files were served for every date tried back to 2 Jan 2024. The July 2024 change was therefore a change of publication, and both formats overlap from January to July 2024. Backfills use UDiFF only (D-20); old-format files are the held-out schema-drift case.
- **The two formats hold the same data.** For 5 Jul 2024 both have 2,775 rows (1,906 `EQ`), and every row matches on close, volume, value and trade count. The pair is ground truth for a replay (fixtures in `tests/fixtures/nse/`).
- **What changed between the formats:**

  | Old | UDiFF | Note |
  |---|---|---|
  | `SYMBOL` | `TckrSymb` | |
  | `SERIES` | `SctySrs` | |
  | `OPEN`, `HIGH`, `LOW`, `CLOSE` | `OpnPric`, `HghPric`, `LwPric`, `ClsPric` | |
  | `LAST`, `PREVCLOSE` | `LastPric`, `PrvsClsgPric` | |
  | `TOTTRDQTY`, `TOTTRDVAL` | `TtlTradgVol`, `TtlTrfVal` | |
  | `TIMESTAMP` (`05-JUL-2024`) | `TradDt` (`2024-07-05`) | date format changes |
  | `TOTALTRADES` | `TtlNbOfTxsExctd` | |
  | `ISIN` | `ISIN` | unchanged name; column moves from last to seventh |
  | trailing empty column | none | the old header and rows end with a comma |
  | not present | `FinInstrmId`, `FinInstrmNm`, `SttlmPric`, `BizDt`, and about 17 constant or empty columns (segment, expiry, strike, option type, open interest, reserved fields) | new in UDiFF |

  Numbers also change shape: prices always have two decimals in UDiFF (`990.00`) but trailing zeros are trimmed in the old format (`990`).
- **Security master.** A regular CSV: 120 named columns, 38,107 rows on 16 Oct 2026, every line with exactly 120 fields and no quoting, so DuckDB reads it without special options. (An earlier version of this note called the rows ragged; that was a misreading of a truncated header, corrected in LH-018.) It includes dummy instruments such as `011NSETEST`. No file was served for 2 Jan 2024, so the master is effectively a snapshot captured from now on, and silver conformance for older dates must cope with symbols that are missing from it.
- **`EQUITY_L.csv`** (`.../content/equities/EQUITY_L.csv`) is a latest-only list of about 2,620 equities, not addressable by date, with leading spaces in some header names.
- **Corporate actions are an API, not an archive file.** It returned 27 records for 1 to 16 Oct 2026 and 15 for 1 to 15 Jan 2024, with fields `symbol`, `series`, `isin`, `subject`, `exDate`, `recDate`, `bcStartDate`, `bcEndDate`, `ndStartDate`, `ndEndDate`, `faceVal`, `comp`, `ind` and `caBroadcastDate`. Completeness against NSE's website was not checked. Because it lives on the main site rather than the archive host, it is the most likely endpoint to be rate limited or blocked; fetch it with wide date ranges and few requests.
- **Publication times.** The bhavcopy zip entry is stamped 16:30 IST and the file was last modified at 16:33 IST on the trade date. The security master was last modified at 20:22 IST.

#### AMFI findings

Measured on 19 October 2026 (LH-013) with a handful of requests, spaced a few seconds apart.

- **Two endpoints, two layouts.** The live snapshot has the columns `Scheme Code; ISIN Div Payout/ ISIN Growth; ISIN Div Reinvestment; Scheme Name; Plan; Option; Net Asset Value; Date`. The history report has `Scheme Code; NAV Name; Plan; Option; ISIN Div Payout/ISIN Growth; ISIN Div Reinvestment; Net Asset Value; Date`. Names and order differ, so bronze must select columns by header name, never by position.
- **The live file is not date-addressable.** It is a snapshot of the latest NAV per scheme and held 829 distinct NAV dates (8,035 rows dated Friday 16 Oct, 709 dated Sunday 18 Oct, the rest older). The history report returns exactly one NAV date per file, so the loader uses it, which keeps ingestion idempotent per trade date (D-19).
- **Row types.** Data rows have eight fields. Blank lines (a single space in the live file), category lines such as `Open Ended Schemes(...)` and fund-house names are interleaved with them. Bronze must keep only lines with eight fields. Category lines can contain non-ASCII characters (a curly apostrophe), so read the file as UTF-8.
- **Weekends are partial, not missing.** Saturday 17 Oct 2026 returned 603 rows (83 KB) against 8,747 on Thursday 15 Oct. Row-count monitors, not file existence, will catch a truncated weekday file. Behaviour on a full market holiday has not been observed.
- **No data is an HTML page, not an error.** For a date with no NAVs (tested with a future date) AMFI answers HTTP 200 with a `text/html` page containing "No data found on the basis of selected parameters for this report". Real reports are `text/plain`. The loader treats the marked page as "no data", and any other HTML as a failure.
- **Reports for past dates are not immutable.** Two downloads of the 15 Oct 2026 report 40 minutes apart held the same 8,747 rows, but AMFI had corrected one: the Plan for scheme 154764 changed from blank to "Direct Plan", and the section order shifted. The raw zone keeps the second version as a new object and leaves the first untouched (FR-ING-05). Row order must never be relied on.
- **Placeholders differ.** Plan and Option are blank on about 40% of live-file rows. A missing ISIN is always `-` in the live file (799 rows in the first ISIN column, 9,795 in the second) but an empty string in the history report (about 640 rows in the first column), with a stray `-` in one or two rows. Bronze should treat both as null.
- **Size and cost.** A single date is about 1 MB and takes under a second. Backfilling from 1 January 2024 is roughly 700 requests, about 35 minutes at one request every three seconds.
- **Date format** is `DD-Mon-YYYY` in both the query and the data.
- **Precision.** History NAVs have at most four decimal places. 476 live-file NAVs have more, up to eight, so a `DECIMAL(18, 4)` column fed from the live file would silently round them, which is another reason to use the history report.

### 5.3 Lakehouse layers (dbt)

| Layer | Responsibility | Example models |
|---|---|---|
| Bronze | Read raw files, apply types, rename to project conventions, add `_source_file`, `_ingested_at` | `brz_nse__cm_bhavcopy`, `brz_amfi__navall` |
| Silver | Clean, deduplicate on keys, conform units and identifiers, join reference data | `slv_nse__equity_eod`, `slv_nse__securities`, `slv_nse__corporate_actions`, `slv_amfi__nav`, `slv_fbil__reference_rates` |
| Gold | Business-ready tables for consumers | `gld_equity_daily_prices`, `gld_mf_nav_daily`, `gld_fx_reference_rates`, `gld_market_daily_summary` |

**How bronze reads the raw zone.** Each raw dataset is a dbt source whose location is a DuckDB `read_csv(...)` over the raw-zone folder, so raw files are the first node of every lineage path. The folder comes from `RAW_ZONE_DIR`, the setting the loaders write to; this local-path mode serves `dev`, `ci` and `bench`, and reading from R2 on `prod` is LH-082. `brz_amfi__navall` shows the pattern:

- Columns are matched by header name, not position. A renamed column makes the model fail (the schema drift surfaces in dbt, where lineage can trace it), while an added column is ignored.
- Blank lines, category headings and fund-house names are dropped, but any line with a scheme code or another value is kept, so a damaged data row reaches the tests instead of vanishing.
- Values that do not convert become null and are reported by `not_null` tests. A singular test checks that every row's NAV date equals the trade date of the folder it was stored in.
- Bronze reads every stored version of a report, so a day AMFI republished appears once per version, told apart by `_source_file`. Silver chooses which version to keep (D-21).

**NSE zips are staged (D-22).** The NSE bhavcopy is a zip archive. DuckDB cannot read zip natively (it does read gzip, which the security master uses), and the raw zone keeps the zip as published (D-02). So the NSE loader also unzips each stored file into a sibling `staged/` area of the same store, and dbt reads from there:

```
raw/source=nse/dataset=cm_bhavcopy/trade_date=D/<hash>__BhavCopy_..._0000.csv.zip     as published
staged/source=nse/dataset=cm_bhavcopy/trade_date=D/<hash>__BhavCopy_..._0000.csv      derived
```

The staged key is derived from the raw key, so the hash prefix still says which raw version a row came from, and staging is idempotent: re-running an ingest repairs a missing staged copy, and the whole area can be rebuilt from the raw zone. Raw bytes are never touched. Staging checks the raw object's hash, accepts only single-file zips, and refuses one that would expand beyond 256 MB. Alternatives considered: a DuckDB community extension that reads zip (it works, but loads third-party binary code at run time, in every CI run, and its behaviour on MotherDuck is unknown) and a dbt-duckdb Python model (it would hide the SQL from column-level lineage).

`brz_nse__cm_bhavcopy` follows the same pattern over the staged files. Its checks are `not_null` on the key and price columns, "not negative" on prices, volume and turnover, uniqueness per day, symbol, series and file, a date-match test, and a range test (the high is never below the low, and the close lies between them). A file in the pre-July-2024 format is rejected, because its column names do not match: alone it fails the model, and next to a healthy file it shows up as missing values.

`brz_nse__security_master` reads the gzipped CSV directly (DuckDB handles gzip) and `brz_nse__corporate_actions` reads the JSON arrays with `read_json`. Both match columns or keys by name, so a renamed one fails the model (matching ignores letter case, so `exDate` and `exdate` are the same). Details worth knowing:

- NSE counts dates in **seconds since 1980-01-01**, and uses 0 for "not set"; 616 of 38,106 instruments have no listing date. Verified by decoding RELIANCE to its real 29 November 1995 listing.
- The security master's par value is in **paise**: 1000 means Rs 10. Corporate actions publish face value in rupees, and every instrument present in both was exactly 100 times larger in the master. Bronze keeps the published unit (`par_value_paise`); a silent switch to rupees would pass every bronze check, so catching it is a cross-feed check for silver (LH-028) and a unit-change fault for the benchmark.
- NSE writes `-` where a corporate action field does not apply, which bronze turns into null. The action's meaning stays as free text in `subject`; classifying it is silver's job.
- A day with no corporate actions has no file, and a model whose source has no files at all fails (for example a fresh environment where nothing was ingested); that is deliberate, since a missing source is a real failure.

The dbt project lives in `dbt/`. Its profile has four targets: `dev` (local DuckDB file), `ci` (in-memory DuckDB), `bench` (a separate local file) and `prod` (MotherDuck, with the token read from `MOTHERDUCK_TOKEN`). Only `prod` is remote, and a test guards that the benchmark target can never be. Each layer builds into a schema of the same name (`bronze`, `silver`, `gold`) on every target, through a `generate_schema_name` override.

`gld_market_daily_summary` deliberately joins all three feeds (for example equity turnover, NAV coverage and USD/INR per day), so lineage paths cross sources and the agents must reason about which feed a failure came from.

Seeds hold per-source calendars (`seed_calendar_nse`, `seed_calendar_amfi`, `seed_calendar_fbil`), used for expected-file checks (FR-ING-07) and late-data detection.

### 5.4 Data quality

- **Generic tests:** `unique`, `not_null`, `accepted_values`, `relationships`, plus range and row-count tests from `dbt_utils` and `dbt_expectations`.
- **Anomaly monitors:** Elementary volume, freshness and column-distribution monitors on silver and gold (fallback: custom z-score tests if the Week 3 spike shows incompatibility).
- **Domain checks:** close price within high–low range; day-over-day price change beyond a threshold without a matching corporate action (catches unit changes); NAV not negative (zero is valid: about 100 segregated-portfolio schemes publish exactly 0 on an ordinary day); FX rate within a plausible band.

## 6. Orchestration

- **Raw assets** are daily-partitioned Dagster assets, one per dataset, backed by the loaders.
- **dbt assets** come from the dbt manifest via `dagster-dbt`, so Dagster's graph matches dbt's.
- **Run storage** uses `dagster-postgres` on Neon, so the local Dagster UI can browse runs executed in Actions.
- **Scheduling:** `pipeline.yml` runs on a cron (default early morning IST) for the previous trade date, after all three feeds have published. It materialises the day's partition, uploads dbt artifacts, and records signals.
- **Hand-off to healing:** if the run records any failure signal, a dependent `heal` job in the same workflow starts with the run's artifacts. `heal.yml` can also be triggered manually or dispatched by the API (resume after review).

## 7. Lineage

### 7.1 Collection

- **Loaders** emit OpenLineage `RunEvent`s with a schema facet listing the raw file's column names (FR-ING-09). This lets the system compare today's raw schema with the expected one.
- **dbt** runs are wrapped with `openlineage-dbt` to emit events for each model.
- Events are stored as JSONB in `lineage_events`.

### 7.2 Graph construction

1. Parse the dbt manifest for model dependencies and compiled SQL.
2. For each model, use `sqlglot.lineage` on compiled SQL to map each output column to its input columns. Edge types: `direct` (pass-through or rename), `transform` (expression), `aggregate`, `filter` (column used only in `WHERE`/`JOIN`).
3. Link raw file columns to bronze columns using the bronze model's column mapping and the latest schema facet.
4. If parsing fails, add table-level edges marked `inferred` (FR-LIN-05).
5. Persist nodes and edges to `lineage_nodes` and `lineage_edges`, tagged with the manifest hash (FR-LIN-04).

**Node identity:** `<dataset>.<column>`, where dataset is either `raw.<source>.<dataset>` or a dbt model name, e.g. `raw.nse.cm_bhavcopy.ClsPric` or `slv_nse__equity_eod.close_price_inr`.

### 7.3 Traversal

At runtime the graph is loaded into NetworkX. The lineage service offers `upstream(node, depth)`, `downstream(node, depth)`, `path(a, b)` and `common_ancestors(nodes)`, the last of which the Detector uses for correlation.

## 8. Agent design

### 8.1 Workflow

```mermaid
flowchart TD
    S([Start]) --> D["Detect and correlate"]
    D --> I["Investigate"]
    I -->|more evidence needed, budget left| I
    I -->|root cause in dbt| P["Write patch"]
    I -->|root cause outside dbt| G["Draft suggestion"]
    I -->|budget exhausted| U["Mark unresolved"]
    P --> V["Verify in sandbox"]
    V -->|failed, attempts left| P
    V -->|failed, no attempts left| X["Mark fix unverified"]
    V -->|passed| PR["Open pull request"]
    G --> IS["Open issue"]
    PR --> R{{"Interrupt: await human review"}}
    R --> L["Record outcome and index"]
    IS --> L
    X --> L
    U --> L
    L --> E([End])
```

The graph is built with LangGraph and checkpointed to Postgres (`langgraph-checkpoint-postgres`). The run pauses at the review interrupt and the workflow job ends. When an approver decides, the API dispatches `heal.yml` in resume mode, which continues from the checkpoint into the learning step.

### 8.2 State

The graph state is a typed model holding: incident ID, signals, affected nodes, retrieved knowledge, an evidence list, ranked hypotheses with confidence, the chosen root cause, fix location (`dbt` or `external`), patch attempts with sandbox results, GitHub links, and counters for the budget (LLM calls, elapsed time).

### 8.3 Detector

Mostly deterministic, so its behaviour is predictable and testable:

1. Parse `run_results.json`, Elementary results, the ingestion log and expected-file calendars into signals.
2. Map each signal to lineage nodes.
3. Group signals whose nodes share a common upstream ancestor within a depth limit (FR-DET-02).
4. Match against open incidents by root ancestor and fault fingerprint to avoid duplicates (FR-DET-05).
5. One LLM call classifies the fault and writes a short summary (FR-DET-04).

### 8.4 Investigator

A **lineage-guided search**. Starting from the failing columns, it visits upstream nodes in order of a cheap heuristic score (schema changed today, profile shifted, nearest to the source), and at each step uses tools to gather evidence for or against "this node is the root cause". It stops when one hypothesis reaches the confidence threshold, the frontier is exhausted, or the budget runs out (FR-INV-05).

| Tool | Returns |
|---|---|
| `get_lineage` | Upstream or downstream nodes and edges |
| `get_raw_schema` | Column names and inferred types of a raw file |
| `compare_schemas` | Added, removed, renamed-candidate and retyped columns between two dates |
| `profile_column` | Null rate, distinct count, min, max, mean for a column on a date |
| `compare_profiles` | A column's profile today versus a trailing baseline |
| `sample_rows` | At most 20 rows matching a filter |
| `get_model_sql` | Raw and compiled SQL for a model |
| `get_test_failure` | Test definition and sample failing rows |
| `get_ingestion_log` | Ingestion attempts for a source, dataset and date |
| `get_corporate_actions` | Corporate actions for instruments in a date range |
| `get_calendar` | Whether a file was expected on a date |
| `search_knowledge` | Top similar incidents and runbook sections |

All tools are read-only and return bounded, delimited output (NFR-SEC-04).

### 8.5 Fixer

1. Read the relevant dbt files and the Investigator's root cause.
2. Produce a unified diff. Reject it immediately if it touches anything outside `dbt/models/`, `dbt/macros/` or schema YAML files, or exceeds a size limit (FR-FIX-01).
3. **Sandbox verification** (FR-FIX-02): create a temporary git worktree, apply the patch, build the affected subgraph on a DuckDB copy containing the incident's data, and compare test results before and after. Pass means: originally failing tests now pass, and no previously passing test fails.
4. On failure, feed the error back and retry, up to the attempt limit (FR-FIX-03).
5. On success, the GitHub App pushes branch `heal/<incident-id>` and opens a pull request using the PR template in section 10.3. For external root causes, it opens an issue instead (FR-FIX-05).

### 8.6 LLM layer

`lineageheal.llm` exposes one interface for chat and structured output. It applies a token-bucket rate limiter per provider, retries with backoff, and falls back from Gemini to Groq on rate-limit or server errors (NFR-REL-03). Prompts are loaded from versioned files, and every call records model ID, prompt version, tokens and latency. A fake provider replays recorded responses for tests.

## 9. Application data model

```mermaid
erDiagram
    PIPELINE_RUN ||--o{ SIGNAL : produces
    INCIDENT ||--|{ SIGNAL : groups
    INCIDENT ||--o{ AGENT_RUN : "investigated by"
    AGENT_RUN ||--o{ EVIDENCE : collects
    INCIDENT ||--o{ FIX : proposes
    FIX ||--o{ REVIEW : "decided by"
    KNOWLEDGE_DOCUMENT ||--|{ KNOWLEDGE_CHUNK : "split into"
    LINEAGE_NODE ||--o{ LINEAGE_EDGE : "source of"
    BENCHMARK_RUN ||--|{ BENCHMARK_RESULT : contains
```

Plus standalone tables: `ingestion_log`, `lineage_events`, LangGraph checkpoint tables, and Dagster's own schema.

### Ingestion log

`ingestion_log` (FR-ING-06) holds one row per ingestion attempt, successful or not:

| Column | Meaning |
|---|---|
| `source`, `dataset`, `trade_date` | What was asked for |
| `outcome` | `stored` (a new object), `unchanged` (an identical file was already there), `no_data` (the source had nothing) or `failed` |
| `object_key`, `size_bytes`, `sha256` | The raw object, for `stored` and `unchanged` |
| `http_status` | The source's answer, when there was one |
| `error` | Why a `failed` attempt failed (truncated, never a secret) |
| `started_at`, `finished_at` | Timezone-aware UTC timestamps |

The database enforces the rules, not just the code: an unknown outcome, a stored file without its key, hash and size, and a failure without a reason are all rejected. Migrations are Alembic revisions written by hand, and a test fails if the models and the migrations ever describe different schemas.

What the log is for:

- **Choosing a version.** A day's file can be republished (AMFI did, repeatedly). `stored_versions(source, dataset, trade_date)` returns each stored object with the time it first arrived, oldest first, so silver can pick the latest version by arrival time (D-21). An `unchanged` re-ingest does not move a version, and failed or empty attempts add none.
- **Late and missing data.** Together with the exchange calendars it distinguishes "no file expected" from "file missing" (FR-ING-07).

How it is used:

- It is optional. With no `DATABASE_URL`, ingestion works exactly as before and keeps no log. With one, `ingest` and `backfill` record every attempt, and a failure to write the log is an error, because a silent gap would hide exactly what the log exists to show (D-24).
- Files written by a fault injector are logged as ordinary stored versions with no HTTP status, so the log matches what is actually in the raw zone.
- `lineageheal db upgrade` and `db status` manage the schema; `lineageheal log` reads recent attempts.

### Incident lifecycle

```mermaid
stateDiagram-v2
    [*] --> detected
    detected --> investigating
    investigating --> fix_proposed: verified dbt patch
    investigating --> suggestion_filed: root cause outside dbt
    investigating --> fix_unverified: no patch passed sandbox
    investigating --> unresolved: budget exhausted
    fix_proposed --> approved: approver approves
    fix_proposed --> rejected: approver rejects
    approved --> resolved: next pipeline run passes
    rejected --> resolved: fixed manually
    suggestion_filed --> resolved: fixed manually
    fix_unverified --> resolved: fixed manually
    unresolved --> resolved: fixed manually
    resolved --> [*]
```

## 10. API and review flow

### 10.1 Endpoints

| Method | Path | Access |
|---|---|---|
| GET | `/health` | Public |
| GET | `/auth/login`, `/auth/callback`; POST `/auth/logout`; GET `/me` | Public |
| GET | `/feeds/health` | Public |
| GET | `/incidents`, `/incidents/{id}` | Public |
| GET | `/incidents/{id}/lineage`, `/incidents/{id}/fix` | Public |
| POST | `/incidents/{id}/approve` | Approver |
| POST | `/incidents/{id}/reject` (reason required) | Approver |
| GET | `/runbooks`, `/runbooks/{slug}` | Public |
| GET | `/benchmark/runs`, `/benchmark/runs/{id}` | Public |

### 10.2 Review sequence

```mermaid
sequenceDiagram
    participant GA as GitHub Actions
    participant AG as Agents
    participant SB as Sandbox (DuckDB)
    participant APP as GitHub App
    participant PG as Postgres
    participant API as FastAPI
    actor R as Approver

    GA->>AG: heal job with run artifacts
    AG->>PG: create incident, store evidence
    AG->>SB: apply patch, build affected subgraph
    SB-->>AG: tests pass
    AG->>APP: push branch, open pull request
    AG->>PG: status fix_proposed, checkpoint
    R->>API: approve (signed in with GitHub)
    API->>API: check allowlist and CSRF token
    API->>APP: approving review + squash merge with approver's token
    API->>PG: record decision
    API->>GA: dispatch heal.yml resume
    GA->>AG: resume from checkpoint
    AG->>PG: index outcome for retrieval
```

### 10.3 Pull request template (agent-generated)

```markdown
## Incident <id>: <one-line summary>

**Fault class:** <class> · **Confidence:** <0.00–1.00>

### Root cause
<explanation>

### Lineage path
<root node> → … → <failing column>

### Evidence
- <evidence item with tool and value>

### Change
<what the patch does and why>

### Verification
<tests that failed before and pass now; count of tests still passing>

[View incident in dashboard](<url>) · [Agent trace](<url>)
```

## 11. Retrieval design

- **Corpus:** runbooks (`docs/runbooks/*.md`, chunked by heading) and decided incidents (one document each: summary, root cause, fix, decision, reviewer reason).
- **Embedding:** `BAAI/bge-small-en-v1.5` via fastembed, 384 dimensions, stored in pgvector with an HNSW index.
- **Keyword search:** Postgres `tsvector` with a GIN index.
- **Fusion:** reciprocal rank fusion (k = 60) over both result lists, then optional filters by fault class and source.
- **Benchmark isolation:** benchmark runs use a separate namespace containing runbooks plus seed-split incidents only (FR-KB-03, FR-BEN-07).

## 12. Benchmark design

### 12.1 Fault specification

```yaml
id: nse-schema-drift-007
class: schema_drift
source: nse
dataset: cm_bhavcopy
trade_date: 2024-09-12
split: test
seed: 7
injection:
  type: rename_column
  column: ClsPric
  new_name: ClosePrice
expected:
  root_cause: raw.nse.cm_bhavcopy.ClsPric
  fix_location: dbt
```

### 12.2 Run procedure

For each fault and each system (LineageHeal, rule-based, plain LLM):

1. Build a sandbox DuckDB with clean data up to the fault's date.
2. Copy the day's raw files and apply the injector.
3. Run the pipeline for that date and collect signals.
4. Run the system under test and record diagnosis, patch, verification result, timings, LLM calls and tokens.
5. Score against the expected root cause and fix location.

Results are written per fault, so an interrupted run resumes where it stopped (FR-BEN-06).

**Injectors so far.** `lineageheal.benchmark.faults` holds pure bytes-to-bytes injectors for AMFI reports: `blank-nav` (nulls), `duplicate-row` (duplicates), `rename-column` (schema drift), `wrong-date` (late data: a stale report), and `negative-nav` (invalid values, which is not one of the five classes). A test asserts which bronze check catches each one. `lineageheal dev inject-fault` stores an injected copy as a new version in a local raw zone, for rehearsals and demos, and refuses to run when `RAW_ZONE_BACKEND` is `r2`, so faults cannot reach production data. Unit-change faults (for example paise instead of rupees) are not injected yet because bronze tests cannot see them; they need the price and NAV jump checks (LH-028).

### 12.3 Baselines

- **Rule-based:** maps each failing test to the nearest upstream source of the failing model and applies templated fixes for known patterns. No LLM.
- **Plain LLM:** the same model as LineageHeal, given only the failing test output and the failing model's SQL. No lineage, tools or retrieval.

### 12.4 Held-out case

The July 2024 replay configures the pipeline as it would have looked before NSE's change, then feeds the real post-change files. Both the file-naming change (outside dbt) and the column changes (inside dbt) appear, testing both fix paths.

## 13. Security

- **Secrets:** environment variables locally, GitHub Actions secrets in CI, Render and Cloudflare environment settings in deployment. gitleaks runs in pre-commit and CI.
- **GitHub App permissions:** Contents (read and write), Pull requests (read and write), Issues (read and write), Metadata (read), Actions (write, for workflow dispatch). Nothing else.
- **Authentication:** GitHub sign-in through the App's user authorisation flow. Sessions are signed, HTTP-only, `SameSite=Lax` cookies, and state-changing requests require a CSRF token.
- **Authorisation:** the approver allowlist is checked server-side on every state-changing endpoint.
- **Branch protection:** `main` requires pull requests and passing CI.
- **Prompt injection:** feed data (such as scheme names) is untrusted. Tool outputs are delimited and truncated, the model never chooses which tools exist or what paths are writable, and patches are validated against the path allowlist outside the model.

## 14. Configuration and observability

- **Configuration:** `LH_ENV` selects `dev`, `ci`, `bench` or `prod`. All settings come from environment variables through `lineageheal.core.settings`.
- **Logging:** structlog JSON with `run_id`, `incident_id` and `component` fields.
- **Tracing:** LangSmith, tagged with incident ID, environment and prompt versions.
- **Metrics:** agent run metrics in Postgres, shown on the dashboard and used by the benchmark report.

## 15. Local development

`docker compose up` starts:

| Service | Purpose | Port |
|---|---|---|
| `postgres` | Postgres 17 with pgvector (replaces Neon locally) | 5432 |
| `dagster-webserver` | Dagster UI | 3000 |
| `dagster-daemon` | Schedules and sensors | — |
| `api` | FastAPI with reload | 8000 |
| `web` | Vite dev server | 5173 |

Local DuckDB files live in `./.data/` (git-ignored).

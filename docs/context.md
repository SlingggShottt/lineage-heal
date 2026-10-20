# Project Context

> The first page anyone (human or Claude Code) should read. It is a living document, updated whenever a decision, constraint or status changes.
>
> **Last updated:** Week 3

## Summary

LineageHeal is a self-healing data pipeline for Indian financial market data. It ingests daily public feeds into a lakehouse and, when data quality fails, uses LLM agents that reason over column-level lineage to locate the root cause and propose a verified fix as a pull request for human approval.

## Problem

India's capital markets serve over 13.6 crore investors holding more than 21 crore demat accounts, and mutual fund assets exceed ₹80 lakh crore (SEBI, 2025). Brokers, fintech apps and research platforms depend on daily public feeds from NSE, AMFI and FBIL, and these pipelines break often. A survey of 200 data professionals reported around 67 data incidents per month and about 15 hours on average to resolve each (Monte Carlo, 2023).

A concrete example: in July 2024, NSE switched to the UDiFF bhavcopy format, changing file names, column names and date formats overnight. Existing data-observability tools detect breaks like this, but diagnosis (tracing the failure back to its upstream cause) still falls to engineers.

## Goals

1. A working pipeline that ingests three public feeds daily into a bronze → silver → gold lakehouse.
2. An agent system that detects failures, localises the root cause using lineage, and proposes a verified dbt patch as a GitHub pull request.
3. A dashboard where a human reviews the incident, lineage path, patch and agent reasoning, then approves or rejects.
4. A reproducible fault-injection benchmark comparing LineageHeal against rule-based and plain-LLM baselines.
5. A research paper based on the benchmark results, written after the system is complete.

## Non-goals

- **Not an investment or analytics product.** Gold tables exist as realistic downstream consumers, not to give financial advice.
- **No autonomous changes.** There is no auto-merge path, ever.
- **No automatic patching outside dbt.** Faults rooted in loaders or sources get a diagnosis and a suggested change, not a pull request.
- **No real-time or intraday data.** End-of-day feeds only.
- **No paid infrastructure.** Everything runs on free tiers.

## Users

| Persona | Need |
|---|---|
| On-call data engineer (primary) | Understand why a pipeline broke and get a correct, reviewable fix quickly |
| Reviewer / approver | Enough evidence (lineage path, diff, test results, reasoning) to trust or reject a fix |
| Data consumer | Gold tables that are correct, or clearly flagged when they are not |
| Researcher | A reproducible benchmark with clear metrics and baselines |
| Demo visitor | Read-only access to explore incidents and fixes without being able to change anything |

## Data sources

| Feed | Publisher | Role | Notes |
|---|---|---|---|
| CM-UDiFF bhavcopy | NSE | Primary equity end-of-day prices | Format changed in July 2024 (UDiFF). Pre-change files are the held-out natural test case. |
| Security master | NSE | Instrument reference data | Used to conform symbols and ISINs in silver. |
| Corporate actions | NSE | Splits, bonuses, dividends | Needed to tell genuine price jumps from unit or scaling faults. |
| NAVAll | AMFI | Mutual fund NAVs | Text file covering all schemes. |
| Reference rates | FBIL | Rupee exchange rates | Chosen instead of RBI reference rates, which FBIL now publishes. |

Access details (URLs, file naming, headers, history availability, behaviour from GitHub Actions runners) are verified in the Week 2 data spike and recorded in `docs/design_architecture.md`.

## Glossary

| Term | Meaning |
|---|---|
| Bhavcopy | NSE's daily end-of-day market data file |
| UDiFF | Unified Distilled File Formats, NSE's standardised file format adopted in July 2024 |
| NAV | Net asset value per unit of a mutual fund scheme |
| ISIN | International Securities Identification Number |
| Corporate action | An event such as a split, bonus or dividend that changes how prices should be interpreted |
| Bronze / silver / gold | Lakehouse layers: typed raw data / cleaned and conformed data / business-ready tables |
| Lineage | The record of which upstream columns and transformations produce each downstream column |
| Root cause | The upstream source, column or transformation where a failure originates |
| Incident | One detected failure, from detection through investigation, fix and human decision |
| Runbook | A short written procedure for diagnosing or fixing a known class of failure |
| Suggested fix | A drafted change for a root cause outside dbt, filed as a GitHub issue for a human to apply |
| Sandbox | An isolated DuckDB copy where a patch is built and tested before any pull request is opened |

## Fault classes

The benchmark and the agents are designed around five fault classes:

| Class | Example |
|---|---|
| Schema drift | A column is renamed, dropped, reordered or changes type |
| Nulls | Required values arrive blank, e.g. a missing NAV |
| Duplicates | Repeated rows for the same key, e.g. symbol + series + date |
| Unit changes | Values arrive in different units or scale, e.g. paise instead of rupees |
| Late data | A file is missing or arrives after its expected time |

## How the system works

1. **Ingest.** Python loaders download each feed and store the raw file, unchanged, in Cloudflare R2.
2. **Transform and test.** dbt builds bronze, silver and gold models on MotherDuck. Schema tests and Elementary anomaly monitors run on every layer.
3. **Orchestrate.** Dagster models every table as an asset and runs on a schedule in GitHub Actions.
4. **Trace lineage.** OpenLineage events, plus sqlglot parsing of dbt SQL, build a column-level lineage graph.
5. **Heal.** On failure, the Detector opens an incident, the Investigator walks the lineage graph and retrieves similar past incidents and runbooks, and the Fixer writes a dbt patch, verifies it in a sandbox and opens a pull request. If the root cause is outside the dbt project (for example a changed source URL), the Fixer instead drafts a suggested change, shown in the dashboard and filed as a GitHub issue.
6. **Decide.** A human signs in with GitHub and approves or rejects in the dashboard. The outcome is stored for future retrieval.

Full design: `docs/design_architecture.md`.

## Key decisions

| ID | Decision | Reason |
|---|---|---|
| D-01 | FBIL reference rates instead of RBI | FBIL is the current publisher of rupee reference rates |
| D-02 | Raw files stored as published (schema-on-read) | Makes schema drift surface in dbt, where lineage can trace it |
| D-03 | MotherDuck as primary warehouse, local DuckDB for development and tests | GitHub Actions runners are ephemeral, so persistent state needs cloud storage |
| D-04 | Dagster open source, run in GitHub Actions | Free, and its asset model mirrors the lakehouse layers |
| D-05 | Postgres + pgvector on Neon for application state and retrieval | One database for incidents, approvals and hybrid search |
| D-06 | Local open-source embeddings via fastembed | No rate limits, no cost, reproducible benchmark results |
| D-07 | Gemini as primary LLM, Groq as fallback, behind one interface | Free tiers with different rate limits; fallback keeps runs going |
| D-08 | Fixer pull requests are limited to the dbt project | Keeps fixes reviewable and testable with `dbt build` |
| D-09 | Mandatory human approval, no auto-merge | Financial data correctness requires accountability |
| D-10 | React + TypeScript dashboard with a FastAPI backend | Clean separation of UI and API, industry-standard stack |
| D-11 | Benchmark verifies patches in a sandbox on local DuckDB without opening real pull requests; live mode uses MotherDuck and opens real ones | Avoids around 100 throwaway pull requests and keeps the benchmark within MotherDuck's free compute allowance |
| D-12 | MIT licence | Permissive and widely understood for portfolio projects |
| D-13 | Claude Code never commits or pushes; the team commits all changes | Keeps authorship and review with the team |
| D-14 | When the root cause is outside the dbt project, the Investigator still diagnoses it and the Fixer drafts a suggested change as a dashboard entry and GitHub issue, with no pull request | Every pull request stays verifiable with `dbt build`, while source-side faults such as the July 2024 NSE change still get a useful outcome |
| D-15 | dbt Core 1.x (Python) with dbt-duckdb rather than dbt 2.0 | dbt 2.0 shipped in September 2026; Dagster, OpenLineage and Elementary integrations target the Python engine. Revisit after the build. |
| D-16 | API on Render's free tier, dashboard on Cloudflare Pages | Free, Git-based deploys; a cold start of about a minute is acceptable for a demo |
| D-17 | Dashboard login via GitHub OAuth; only allowlisted accounts can approve, everyone else is read-only | Approvals are attributable to a person, and a public demo link cannot merge anything |
| D-18 | Agents open pull requests and issues through a dedicated GitHub App | Bot identity distinct from team members, with narrowly scoped permissions |
| D-19 | The AMFI loader fetches the per-date history report, not the live `NAVAll.txt` snapshot, and bronze selects columns by header name | The live file holds 829 different NAV dates and cannot be fetched for a past date; the two endpoints also name and order columns differently (LH-013) |
| D-20 | NSE history is backfilled in UDiFF only; old-format bhavcopy files (served up to 5 Jul 2024) are the held-out schema-drift case | UDiFF is served back to at least 2 Jan 2024 and matches the old format row for row, so one bronze schema covers the backfill while the old files give ground truth for the replay (LH-012) |
| D-21 | Bronze reads every stored version of a raw file and silver chooses which version to keep | Raw files are write-once and a republished day is a new object, so bronze stays a faithful, append-only view; the version choice needs ingestion times from the ingestion log (LH-014) |
| D-22 | NSE zip files are unzipped into a derived `staged/` area next to the raw zone, and dbt reads from there | DuckDB cannot read zip and raw files must stay exactly as published; staging is deterministic, idempotent, offline and keeps the SQL visible to lineage, unlike a community extension or a Python model (LH-024) |
| D-23 | Development and the mid-project demo run on local storage only; the Cloudflare R2 account (LH-006) is deferred | R2 needs a payment method on file even for its free tier, and the demo does not need deployment. The storage client already supports R2 behind `RAW_ZONE_BACKEND`, so nothing changes in the code when it is switched on |
| D-24 | The ingestion log is optional (no `DATABASE_URL`, no log), but when it is configured a failed write is an error, and files written by fault injectors are logged as stored versions | A silent gap in the log would hide late data and make version selection wrong; keeping it optional means a laptop run needs no database |
| D-25 | The store uses synchronous SQLAlchemy for now; an async session factory over the same models is added with the API (LH-058) | The CLI and loaders are synchronous, and the models and migrations are the same either way |
| D-26 | Silver chooses the latest version of a republished file by the file's modification time, read in SQL from the raw zone, not by querying the Postgres ingestion log | `dbt build`, CI and the benchmark sandbox stay independent of a database, and a metadata-only read is fast; the log stays the authoritative record, to be reconciled with file times (LH-086) |
| D-27 | Gold prices are adjusted only for splits and bonuses, and the published prices stay beside the adjusted close | Splits and bonuses change how many shares a price refers to; dividends do not, so adjusting for them would change what the price means. Rights, demergers and buy-backs are flagged but not adjusted, a stated limitation. Keeping the published columns lets a reader audit the adjustment, and a check proves gold never changes a silver price (LH-026) |

## Constraints

- **Cost:** free tiers only, across all services.
- **Team:** two developers, both on Ubuntu with Docker.
- **Time:** the full build fits in diary Weeks 2–6. The research paper starts after the build is complete.
- **Rate limits:** free LLM tiers cap requests per minute and per day. The benchmark must be designed to run within them, batched and resumable.
- **Warehouse compute:** MotherDuck's free plan includes limited monthly compute, so heavy workloads (benchmark, experiments) run on local DuckDB.
- **Source access:** public sites may throttle or block automated requests. Loaders must be polite and cache aggressively in development.

## Timeline

| Week | Focus |
|---|---|
| 1 ✅ | Topic finalised with guide, background research, feeds and stack chosen, synopsis prepared |
| 2 | Project documentation, repository and tooling, CI, Docker, data spike, loaders writing to R2 |
| 3 | dbt bronze/silver/gold on MotherDuck, tests and anomaly monitors, Dagster assets, scheduled runs, historical backfill |
| 4 | Lineage graph, Postgres store, LLM layer, Detector and Investigator agents, retrieval |
| 5 | Fixer with sandbox verification, pull requests and issues, FastAPI backend, React dashboard with GitHub login |
| 6 | Fault injectors, full benchmark with baselines and the held-out case, deployment, demo and documentation polish |

Task-level detail lives in `backlog.md`.

## Current status
**Current diary week:** 3
- **Week 1 (complete):** topic finalised with the guide; background research on data incidents and the NSE UDiFF change; feeds and tech stack chosen; synopsis prepared.
- **Week 2 (complete) and Week 3 (in progress):** documentation, tooling and CI are in place (LH-005, LH-007, LH-009, LH-011). AMFI and all three NSE datasets (bhavcopy, security master, corporate actions) have polite loaders, write-once raw storage and the `lineageheal ingest` and `backfill` commands (LH-012 to LH-021, LH-018). The dbt project has bronze models for AMFI and the three NSE datasets, each with fault-catching tests (LH-022 to LH-024), and a local-only fault injector (LH-083) and a rehearsed demo script (LH-084) make failures reproducible. Everything runs on local storage (D-23). A local Postgres with pgvector and an application image run under Docker Compose (LH-010), and every ingestion attempt can be recorded in an `ingestion_log` table that also orders a republished day's files by arrival (LH-014). The silver layer has started: the latest version of each republished day is chosen by arrival time (D-26), and four silver models conform AMFI NAVs and the three NSE datasets (LH-025). A cross-feed check catches NSE's par value switching between paise and rupees, which no single-feed check can see. Open: FBIL access (LH-080), exchange calendars (LH-016) and the deferred cloud accounts (LH-006). The test suite runs in about 50 seconds (LH-085). The gold layer has started: `gld_equity_daily_prices` puts prices on a common share count by adjusting for splits and bonuses (D-27). Next: the NAV and market-summary gold models, then the domain checks such as unexplained price jumps (LH-028).

## Open questions

1. Can NSE archives be reached reliably from GitHub Actions runners, or do cloud IP ranges get blocked? From a home network they are reachable with a browser-like User-Agent (LH-012); runners are still untested, so this is checked when `pipeline.yml` is built (LH-033).
2. How can FBIL reference rates be retrieved at all, and how much history is there? The site is a JavaScript application behind reCAPTCHA with no plain file URL (LH-013). To be resolved by hand in a browser, then recorded (LH-080).
3. Does Elementary run correctly on DuckDB and MotherDuck? (Week 3 spike; fallback is custom statistical tests in dbt)
4. Which venue to target for the research paper? (Decided after the build)

Resolved: fix scope outside dbt (D-14), hosting (D-16).

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Source sites block automated or cloud traffic | Ingestion fails in CI | Polite headers and rates, caching, fallback to running ingestion locally and syncing to R2 |
| NSE security master has no history before an unknown date | Backfilled dates may include instruments missing from the master, so silver conformance leaves gaps | Capture the master daily from now on; conform with the latest master and flag unmatched symbols instead of dropping rows |
| FBIL offers no scriptable download | The third feed cannot be ingested as planned | Look for a documented download or API route (LH-080); otherwise agree a replacement rate feed with the guide, keeping the NSE and AMFI feeds as the core of the demo |
| Free LLM tiers change or tighten | Agents or benchmark stall | Provider abstraction with fallback; benchmark runs batched and resumable |
| MotherDuck compute allowance runs out | Live pipeline paused until the monthly reset | Incremental models; benchmark and experiments on local DuckDB; usage checked weekly |
| Free-tier services pause when idle | Slow first request in demos | Warm-up step before demos; documented in runbooks |
| LangSmith's 14-day trace retention | Older traces unavailable for analysis | Run metrics persisted in Postgres |
| Scope versus five-week timeline | Unfinished features | Weekly milestones in `backlog.md`; core healing loop prioritised over polish |

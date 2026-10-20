# Backlog

Planned and completed work for LineageHeal, organised by diary week. Requirement IDs refer to [docs/SRS.md](docs/SRS.md).

**Status:** `Todo` · `In progress` · `Done` · `Blocked`

**Rules**
- One item per pull request. Branch names use the item ID (see [style guide](docs/style_guide.md#21-branches)).
- When an item is finished, set its status to `Done`, fill in the week, and add a line to the [done log](#done-log).
- New work gets the next free ID and goes under the week it is planned for.

## Milestones

| Week | Milestone | Exit criteria |
|---|---|---|
| 1 | Topic and synopsis | Topic finalised, synopsis prepared |
| 2 | Foundations | Repo, tooling, CI and Docker in place; data spike done; loaders writing to R2 |
| 3 | Lakehouse | All layers built and tested on MotherDuck; daily schedule running; backfill complete |
| 4 | Diagnosis | Lineage graph built; injected faults detected and diagnosed end to end |
| 5 | Healing and UI | Verified pull requests and issues created; dashboard supports full review |
| 6 | Evaluation and launch | Benchmark complete; deployed demo; documentation finished |

## Week 1 — Topic and synopsis

| ID | Task | Refs | Status | Week done |
|---|---|---|---|---|
| LH-001 | Discuss and finalise topic with guide | — | Done | 1 |
| LH-002 | Background research: data incident costs, NSE UDiFF change | — | Done | 1 |
| LH-003 | Select data feeds and technology stack | — | Done | 1 |
| LH-004 | Prepare synopsis | — | Done | 1 |

## Week 2 — Foundations

| ID | Task | Refs | Status | Week done |
|---|---|---|---|---|
| LH-005 | Write project documentation: CLAUDE, README, context, PRD, SRS, design, style guide, tech stack, backlog | — | Done | 2 |
| LH-006 | Create service accounts: Cloudflare R2, MotherDuck, Neon, Google AI Studio, Groq, LangSmith. Deferred while everything runs locally (D-23); R2 needs a payment method on file | NFR-COST-01 | Todo | |
| LH-007 | Scaffold Python project: pyproject, uv, src layout, ruff, mypy, pytest, pre-commit (incl. gitleaks, commit lint), Makefile, .editorconfig, .gitignore, .env.example, LICENSE | NFR-MAINT-02, NFR-SEC-01 | Done | 2 |
| LH-008 | Configure GitHub repo: branch protection, PR and issue templates, Dependabot | NFR-SEC-05 | Todo | |
| LH-009 | CI workflow for Python: format, lint, types, tests | NFR-MAINT-02 | Done | 2 |
| LH-010 | Docker Compose with Postgres + pgvector; Dockerfile for the Python app | NFR-PORT-01 | Done | 2 |
| LH-011 | Core module: settings, structured logging, error hierarchy | FR-OBS-02 | Done | 2 |
| LH-012 | Data spike: NSE bhavcopy, security master, corporate actions (URLs, headers, history, reachability from Actions) | IF-SW-01 | Done | 2 |
| LH-013 | Data spike: AMFI NAVAll (URLs, history, formats). FBIL split out to LH-080 | IF-SW-01 | Done | 2 |
| LH-014 | Postgres store foundation: SQLAlchemy, Alembic, `ingestion_log` table | FR-ING-06 | Done | 2 |
| LH-015 | R2 storage client: content-addressed keys, put-if-absent, checksums | FR-ING-05, IF-SW-02 | Done | 2 |
| LH-016 | Per-source calendars as seeds (NSE, AMFI, FBIL; 2024 onwards) | FR-ING-07 | Todo | |
| LH-017 | NSE CM-UDiFF bhavcopy loader | FR-ING-01, FR-ING-10 | Done | 2 |
| LH-018 | NSE security master and corporate actions loaders | FR-ING-02 | Done | 2 |
| LH-019 | AMFI NAVAll loader | FR-ING-03 | Done | 2 |
| LH-020 | FBIL reference rates loader | FR-ING-04 | Todo | |
| LH-021 | `lineageheal` CLI: `ingest` and `backfill` commands | FR-ING-08, FR-ORC-05 | Done | 2 |
| LH-084 | Mid-project demo script: timed run-through, talking points, built-versus-planned, likely questions | — | Done | 2 |
| LH-080 | Data spike: FBIL reference rates access. The site is a JavaScript app behind reCAPTCHA; find a documented download or API route, or agree a replacement feed. Blocks LH-020 | IF-SW-01, FR-ING-04 | Todo | |
| LH-081 | Secret scanning in CI: add a gitleaks job over the pull request diff (the pre-commit hook only scans staged changes, so it cannot run in CI). Needs approval for the action | NFR-SEC-01 | Todo | |

## Week 3 — Lakehouse and orchestration

| ID | Task | Refs | Status | Week done |
|---|---|---|---|---|
| LH-022 | dbt project scaffold: profiles with dev/ci/bench/prod targets, schema macro, guard tests. Packages and sqlfluff are added with the first models that need them (LH-024, LH-027) | FR-WH-06 | Done | 2 |
| LH-023 | Raw-zone sources in local-path mode for non-prod targets. The DuckDB R2 secret for prod moved to LH-082 | FR-WH-01 | Done | 2 |
| LH-024 | Bronze models (5) with docs and tests. AMFI, NSE bhavcopy (zips are staged, D-22), NSE security master and NSE corporate actions done; only FBIL remains, blocked by LH-080 | FR-WH-01, FR-WH-04 | In progress | |
| LH-025 | Silver models (5) with docs and tests. Done: version selection, `slv_amfi__nav`, `slv_nse__securities`, `slv_nse__corporate_actions` (parsed) and `slv_nse__equity_eod`, with the par-value cross-feed check. Remaining: FBIL (blocked by LH-080) | FR-WH-02, FR-WH-04 | In progress | |
| LH-026 | Gold models (4) with docs and tests, including cross-feed summary | FR-WH-03, FR-WH-04 | Todo | |
| LH-027 | Elementary spike and anomaly monitors (fallback: custom z-score tests) | FR-WH-05 | Todo | |
| LH-028 | Domain quality checks: price ranges, unexplained price jumps (use corporate actions: NSE does not adjust the previous close for splits), NAV and FX bounds. The par-value cross-feed check is done in LH-025 | FR-WH-04 | Todo | |
| LH-029 | dbt build in CI against fixture data | NFR-MAINT-02 | Todo | |
| LH-030 | Dagster raw assets with daily partitions | FR-ORC-01 | Todo | |
| LH-031 | dagster-dbt integration | FR-ORC-01 | Todo | |
| LH-032 | Dagster run storage on Postgres; Compose services for webserver and daemon | FR-ORC-03 | Todo | |
| LH-033 | `pipeline.yml`: scheduled and manual runs on MotherDuck | FR-ORC-02, FR-ORC-05 | Todo | |
| LH-034 | Persist dbt artifacts and record failure signals per run | FR-WH-07, FR-ORC-04 | Todo | |
| LH-035 | Historical backfill from January 2024 to R2 and MotherDuck | FR-ING-08 | Todo | |
| LH-082 | DuckDB R2 secret and httpfs for the prod target: read the raw zone from R2 instead of a local path | FR-WH-01 | Todo | |
| LH-085 | Speed up the dbt scenario tests: parse the project once and reuse it, and run them outside the coverage run | NFR-MAINT-02 | Done | 3 |
| LH-086 | Reconcile raw file arrival times with the ingestion log, as a Detector signal and a test: file times can be reset by copying or restoring a raw zone, the log cannot | FR-DET-01 | Todo | |

## Week 4 — Lineage, detection and investigation

| ID | Task | Refs | Status | Week done |
|---|---|---|---|---|
| LH-036 | Application schema and migrations: runs, signals, incidents, agent runs, evidence, fixes, reviews, knowledge, lineage | §3.4 | Todo | |
| LH-037 | OpenLineage events from loaders with schema facets | FR-ING-09, FR-LIN-01 | Todo | |
| LH-038 | OpenLineage capture from dbt runs | FR-LIN-01 | Todo | |
| LH-039 | Column-level lineage builder: manifest + sqlglot, raw-to-bronze edges, table-level fallback, versioning | FR-LIN-02, FR-LIN-04, FR-LIN-05 | Todo | |
| LH-040 | Lineage traversal service | FR-LIN-03 | Todo | |
| LH-041 | LLM layer: providers, rate limiter, retries, fallback, structured output, fake provider | IF-SW-05, NFR-REL-03 | Todo | |
| LH-042 | Versioned prompt registry | NFR-MAINT-03 | Todo | |
| LH-043 | LangSmith tracing and agent metrics persistence | FR-OBS-01, FR-OBS-03 | Todo | |
| LH-044 | Detector: signal collection from dbt, monitors, ingestion log, calendars | FR-DET-01 | Todo | |
| LH-045 | Detector: lineage-based correlation, deduplication, classification | FR-DET-02 to 05 | Todo | |
| LH-046 | Runbooks for the five fault classes | FR-KB-01 | Todo | |
| LH-047 | Knowledge indexing: fastembed, pgvector, full-text | FR-KB-01 | Todo | |
| LH-048 | Hybrid retrieval with reciprocal rank fusion and filters | FR-KB-02 | Todo | |
| LH-049 | Investigator tools (read-only, bounded output) | FR-INV-02, NFR-SEC-04 | Todo | |
| LH-050 | Investigator: lineage-guided search, hypotheses, budget, fix location | FR-INV-01, FR-INV-03 to 06 | Todo | |
| LH-051 | `lineageheal heal` end to end through diagnosis on a manually injected fault | FR-ORC-04 | Todo | |

## Week 5 — Healing, API and dashboard

| ID | Task | Refs | Status | Week done |
|---|---|---|---|---|
| LH-052 | Fixer: patch generation with path allowlist and size limit | FR-FIX-01 | Todo | |
| LH-053 | Sandbox verifier: git worktree, DuckDB copy, before/after test comparison, retries | FR-FIX-02, FR-FIX-03, FR-FIX-06 | Todo | |
| LH-054 | GitHub App setup and `vcs` client: branches, pull requests, issues, reviews, merges | FR-FIX-04, IF-SW-06, NFR-SEC-02 | Todo | |
| LH-055 | Suggested-fix issues for root causes outside dbt | FR-FIX-05 | Todo | |
| LH-056 | LangGraph workflow wiring with Postgres checkpoints, review interrupt and resume | FR-FIX-07, FR-REV-04 | Todo | |
| LH-057 | `heal.yml`: on pipeline failure, manual, and resume modes | FR-ORC-04, NFR-REL-02 | Todo | |
| LH-058 | FastAPI skeleton: health, problem details, pagination, OpenAPI | FR-API-01, FR-API-03 | Todo | |
| LH-059 | GitHub sign-in, sessions, CSRF, approver allowlist | FR-API-02, FR-REV-03, NFR-SEC-03 | Todo | |
| LH-060 | Read endpoints: feed health, incidents, lineage, fixes, runbooks | FR-API-01 | Todo | |
| LH-061 | Approve and reject endpoints with GitHub reconciliation | FR-REV-01, FR-REV-02, FR-API-04 | Todo | |
| LH-062 | Web scaffold: Vite, TypeScript, Tailwind, shadcn/ui, router, TanStack Query, generated client, ESLint, Prettier, Vitest; frontend CI job | NFR-MAINT-02 | Todo | |
| LH-063 | Overview and incidents list screens | FR-UI-01, FR-UI-02 | Todo | |
| LH-064 | Incident detail: evidence, lineage graph, patch diff | FR-UI-03 to 05 | Todo | |
| LH-065 | Sign-in and approve/reject controls | FR-UI-06, FR-UI-09 | Todo | |
| LH-066 | Runbooks screen | FR-UI-08 | Todo | |

## Week 6 — Benchmark, deployment and polish

| ID | Task | Refs | Status | Week done |
|---|---|---|---|---|
| LH-067 | Fault specification schema and injectors for five classes. AMFI report injectors exist from LH-083; NSE and FBIL injectors and the spec schema remain | FR-BEN-01, FR-BEN-02 | Todo | |
| LH-083 | Demo fault injector: five AMFI faults and a local-only `dev inject-fault` command (first slice of LH-067) | FR-BEN-02 | Done | 2 |
| LH-068 | Fault corpus of about 100 with seed/test split | FR-BEN-01, FR-BEN-07 | Todo | |
| LH-069 | Benchmark runner: sandbox setup, isolated index, resumable results | FR-BEN-03, FR-BEN-06, FR-KB-03 | Todo | |
| LH-070 | Baselines: rule-based and plain LLM | FR-BEN-03 | Todo | |
| LH-071 | July 2024 NSE held-out replay | FR-BEN-05 | Todo | |
| LH-072 | Metrics and Markdown/JSON report generation | FR-BEN-04, NFR-REPRO-01 | Todo | |
| LH-073 | `benchmark.yml` workflow | FR-BEN-06 | Todo | |
| LH-074 | Full benchmark run and results analysis | NFR-PERF-01 | Todo | |
| LH-075 | Benchmark screen in dashboard | FR-UI-07 | Todo | |
| LH-076 | Deploy API to Render and dashboard to Cloudflare Pages | §2.4 | Todo | |
| LH-077 | Playwright end-to-end test of the review flow | FR-UI-06, FR-REV-01 | Todo | |
| LH-078 | Live demo rehearsal: corrupted source file to approved fix | FR-FIX-04, FR-REV-01 | Todo | |
| LH-079 | Documentation polish: README results, demo video, final diagrams | — | Todo | |

## Done log

A running record of completed work, used for the weekly diary.

| Week | ID | Summary |
|---|---|---|
| 1 | LH-001 | Topic finalised with guide |
| 1 | LH-002 | Researched data-incident costs and the July 2024 NSE UDiFF change |
| 1 | LH-003 | Chose NSE, AMFI and FBIL feeds and the technology stack |
| 1 | LH-004 | Synopsis prepared |
| 2 | LH-005 | Wrote project documentation: CLAUDE, README, context, PRD, SRS, design, style guide, tech stack, backlog |
| 2 | LH-007 | Scaffolded the Python project: uv, src layout, ruff, strict mypy, pytest with layer markers, pre-commit (gitleaks, commit lint), Makefile, .env.example, LICENSE |
| 2 | LH-009 | CI workflow for Python: format, lint, strict types and tests on every pull request and push to main, actions pinned to commit SHAs |
| 2 | LH-011 | Core module: typed settings with R2 credentials accessor, structlog setup with secret redaction, error hierarchy |
| 2 | LH-012 | NSE spike: bhavcopy (UDiFF from Jan 2024, old format to 5 Jul 2024, row-for-row equal), security master and corporate actions mapped; browser-like User-Agent needed; reachability from Actions deferred to LH-033; paired fixtures added |
| 2 | LH-013 | AMFI spike: history report is date-addressable, live NAVAll is a snapshot, layouts differ; fixtures added; FBIL split out to LH-080 |
| 2 | LH-015 | Raw-zone storage client: content-addressed write-once keys, checksum-verified reads, R2 and local backends |
| 2 | LH-017 | NSE UDiFF bhavcopy loader: browser-like headers, zip integrity check, 404 as no data, `ingest nse` and `backfill nse`; verified live |
| 2 | LH-019 | AMFI loader: polite fetcher with retries and rate limit, per-date report download, no-data handling, verified live |
| 2 | LH-021 | `lineageheal` CLI with `ingest` and `backfill` (AMFI only so far; NSE and FBIL plug in as their loaders land) |
| 2 | LH-022 | dbt project scaffold: dev, ci, bench and prod targets (only prod on MotherDuck), exact schema names per layer, usage statistics off, guard tests |
| 2 | LH-023 | Raw-zone sources in local-path mode: bronze reads the files the loaders wrote, through dbt sources that start every lineage path |
| 2 | LH-083 | Fault injectors for AMFI reports (blank, negative, duplicate, wrong-date, renamed column) and a `dev inject-fault` command that refuses to touch R2; each fault proven to trip the expected dbt check |
| 2 | LH-084 | Demo script `docs/demo.md`: 10-minute run-through verified by executing its own command blocks end to end, plus talking points, built-versus-not-built and likely questions |
| 2 | LH-018 | NSE security master and corporate actions loaders on a shared base with the bhavcopy loader; `--dataset` option on ingest and backfill; corrected an earlier misreading of the security master as ragged |
| 2 | LH-024 | Bronze, two of five models: AMFI NAVs and the NSE bhavcopy, with 36 checks. NSE zips are unzipped into a derived staged area (D-22); a file in the pre-July-2024 format is rejected by the model |
| 2 | LH-010 | Docker Compose with Postgres 17 and pgvector (loopback only, healthchecked, named volume) and a pinned, non-root application image of 852 MB; guard tests for the configuration |
| 2 | LH-014 | Postgres store: SQLAlchemy models, hand-written Alembic migration and an `ingestion_log` table whose rules the database enforces; every ingest, backfill and injected fault is logged when `DATABASE_URL` is set; `db upgrade`, `db status` and `log` commands; `stored_versions` orders a republished day's files by arrival |
| 2 | LH-025 | Silver, part one: `brz_meta__raw_file_versions` and the `latest_version_rows` macro choose each day's latest version by arrival time, and `slv_amfi__nav` conforms plan and option; verified on the real republished AMFI report, in both arrival orders |
| 2 | LH-025 | Silver, part two: `slv_nse__securities` (par value in rupees), `slv_nse__equity_eod` and `slv_nse__corporate_actions`, whose parser reads 25 real wording shapes and flags rewording; the par-value cross-feed check catches a paise-to-rupees switch that bronze cannot see |
| 3 | LH-085 | Test suite from 3m50s to 50s: the dbt project is parsed once per session and reused (110 s to 39 s for 86 scenarios), and those scenarios run in a second step without coverage, which had doubled their time; 345 tests and 99.53% coverage unchanged |

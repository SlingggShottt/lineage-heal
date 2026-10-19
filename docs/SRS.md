# Software Requirements Specification — LineageHeal

| | |
|---|---|
| **Standard** | Structured after ISO/IEC/IEEE 29148:2018 |
| **Status** | Baseline for build |
| **Version** | 1.0 |
| **Last updated** | Week 2 |
| **Related** | [PRD](PRD.md) · [Design](design_architecture.md) · [Context](context.md) · [Backlog](../backlog.md) |

## 1. Introduction

### 1.1 Purpose

This document specifies the functional and non-functional requirements for LineageHeal. It is the reference for implementation, testing and acceptance. Every requirement has a unique ID that the backlog, tests and pull requests can cite.

### 1.2 Scope

LineageHeal ingests daily public financial market data (NSE, AMFI, FBIL) into a bronze → silver → gold lakehouse, detects data-quality failures, uses lineage-aware LLM agents to locate root causes and propose verified fixes, and gives humans a dashboard to approve or reject them. It also includes a fault-injection benchmark for evaluation. Out of scope: investment analytics, intraday data, automatic merging, and automatic patching outside the dbt project.

### 1.3 Definitions

Domain and project terms are defined in the [context glossary](context.md#glossary). Additional terms used here:

| Term | Meaning |
|---|---|
| Signal | A single failure observation: a failed dbt test, a model error, an anomaly alert or an ingestion error |
| Node | A column in the lineage graph, identified by dataset and column name |
| Affected subgraph | The failing models plus all their downstream models |
| Approver | A GitHub account listed in `APPROVER_GITHUB_LOGINS` |
| Verification methods | **T** test, **D** demonstration, **I** inspection, **A** analysis |

### 1.4 References

- ISO/IEC/IEEE 29148:2018, *Systems and software engineering — Life cycle processes — Requirements engineering*
- [PRD.md](PRD.md), [design_architecture.md](design_architecture.md), [techstack.md](techstack.md), [style_guide.md](style_guide.md)
- OpenLineage specification: https://openlineage.io/docs/spec/
- RFC 9457, *Problem Details for HTTP APIs*

### 1.5 Document overview

Section 2 describes the product in context. Section 3 lists specific requirements. Section 4 covers verification, and Section 5 traces user stories to requirements.

## 2. Overall description

### 2.1 Product perspective

LineageHeal is a standalone system composed of: Python loaders, a dbt project, Dagster orchestration, a lineage service, a LangGraph agent workflow, a FastAPI backend and a React dashboard. It depends on external services for storage (Cloudflare R2), warehousing (MotherDuck), application data (Neon Postgres), LLM inference (Gemini, Groq), tracing (LangSmith), source control (GitHub) and scheduling (GitHub Actions). See [design_architecture.md](design_architecture.md#2-system-context).

### 2.2 Product functions

1. Ingest daily feeds and store raw files immutably.
2. Transform raw data into tested bronze, silver and gold models.
3. Build and maintain a column-level lineage graph.
4. Detect failures and group them into incidents.
5. Investigate incidents to identify the root cause with evidence.
6. Produce a verified dbt patch as a pull request, or a suggested change as an issue.
7. Let approvers approve or reject fixes from a dashboard.
8. Learn from resolved incidents through retrieval.
9. Evaluate the approach with a reproducible benchmark.

### 2.3 User classes

| Class | Access | Description |
|---|---|---|
| Approver | Read and write | Team members who review and decide on fixes |
| Viewer | Read-only, no login required | Demo visitors, data consumers |
| Operator | Repository and CLI access | Team members running pipelines, healing and the benchmark |
| System | Service credentials | GitHub Actions workflows and the LineageHeal GitHub App |

### 2.4 Operating environment

- Development: Ubuntu Linux with Docker and Docker Compose.
- Scheduled execution: GitHub Actions hosted Ubuntu runners.
- API hosting: Render free web service. Dashboard hosting: Cloudflare Pages.
- Supported browsers: current versions of Chrome, Firefox and Edge.

### 2.5 Design and implementation constraints

- C1: Only free tiers of external services may be used.
- C2: Python 3.12; TypeScript in strict mode.
- C3: dbt Core 1.x with dbt-duckdb (decision D-15).
- C4: The Fixer may only change files under `dbt/` (D-08, D-14).
- C5: No automatic merge path may exist (D-09).
- C6: Free-tier rate limits for LLM APIs and warehouse compute apply ([techstack.md](techstack.md#free-tier-budget)).

### 2.6 Assumptions and dependencies

- A1: NSE, AMFI and FBIL continue to publish files publicly.
- A2: Free tiers remain broadly as documented in October 2026.
- A3: Source sites remain reachable from GitHub Actions runners, or ingestion can fall back to running locally (open question, Week 2 spike).

## 3. Specific requirements

All requirements are mandatory unless marked **(S)** for "should".

### 3.1 External interfaces

#### 3.1.1 User interfaces

| ID | Requirement | Verify |
|---|---|---|
| IF-UI-01 | The system shall provide a web dashboard with the screens defined in FR-UI. | D |
| IF-UI-02 | The dashboard shall show a sign-in control; signed-out users shall see all read-only content. | D |

#### 3.1.2 Software interfaces

| ID | Interface | Requirement | Verify |
|---|---|---|---|
| IF-SW-01 | NSE, AMFI, FBIL websites | The system shall retrieve files over HTTPS using configurable URLs and headers. | T |
| IF-SW-02 | Cloudflare R2 | The system shall store and read raw files through the S3-compatible API. | T |
| IF-SW-03 | MotherDuck / DuckDB | The system shall run dbt against MotherDuck for `prod` and local DuckDB for `dev`, `ci` and `bench`. | T |
| IF-SW-04 | Postgres (Neon) | The system shall store application state, lineage and retrieval data in Postgres with pgvector. | T |
| IF-SW-05 | Gemini and Groq APIs | The system shall call LLMs only through the internal LLM layer. | I |
| IF-SW-06 | GitHub | The system shall create branches, pull requests and issues through the LineageHeal GitHub App, and merge only with an approver's user token. | T |
| IF-SW-07 | LangSmith | The system shall send agent traces to LangSmith when tracing is enabled. | D |

#### 3.1.3 Communication interfaces

| ID | Requirement | Verify |
|---|---|---|
| IF-COM-01 | All external communication shall use HTTPS/TLS. | I |
| IF-COM-02 | The API shall expose a REST interface documented by an OpenAPI 3 schema. | I |

### 3.2 Functional requirements

#### 3.2.1 Ingestion (FR-ING)

| ID | Requirement | Verify |
|---|---|---|
| FR-ING-01 | The system shall download the NSE CM-UDiFF bhavcopy for a given trade date and store it unchanged in R2. | T |
| FR-ING-02 | The system shall download the NSE security master and corporate actions files and store them unchanged in R2. | T |
| FR-ING-03 | The system shall download the AMFI NAVAll file for a given date and store it unchanged in R2. | T |
| FR-ING-04 | The system shall download FBIL reference rates for a given date and store them unchanged in R2. | T |
| FR-ING-05 | Ingestion shall be idempotent per (source, dataset, trade date): an identical file is not stored twice; a changed file is stored as a new object, never overwriting the existing one. | T |
| FR-ING-06 | The system shall record each ingestion attempt: source, dataset, trade date, object key, size, SHA-256, HTTP status, outcome and timestamps. | T |
| FR-ING-07 | The system shall use per-source calendars to distinguish "no file expected" (holiday) from "file missing". | T |
| FR-ING-08 | The system shall support backfilling a date range from 1 January 2024 onwards. | D |
| FR-ING-09 | Each successful ingestion shall emit an OpenLineage event including the file's column names as a schema facet. | T |
| FR-ING-10 | Loaders shall retry transient failures with exponential backoff and respect a configurable minimum interval between requests. | T |

#### 3.2.2 Lakehouse (FR-WH)

| ID | Requirement | Verify |
|---|---|---|
| FR-WH-01 | Bronze models shall read raw files (from R2 in `prod`, from a local path otherwise) and apply types, one model per dataset. | T |
| FR-WH-02 | Silver models shall clean, conform names and units, and deduplicate on declared keys. | T |
| FR-WH-03 | Gold models shall provide at least: daily equity prices with corporate-action adjustment, daily mutual fund NAVs, daily FX reference rates, and a cross-feed daily market summary. | T |
| FR-WH-04 | Every model shall have descriptions and tests covering keys, nullability, accepted values, relationships and value ranges where applicable. | I |
| FR-WH-05 | Anomaly monitors shall cover row volume, freshness and the distribution of key numeric columns. | T |
| FR-WH-06 | The dbt project shall define `dev`, `ci`, `bench` and `prod` targets. | I |
| FR-WH-07 | Each dbt run shall persist its artifacts (`manifest.json`, `run_results.json`) for use by detection and lineage. | T |

#### 3.2.3 Orchestration (FR-ORC)

| ID | Requirement | Verify |
|---|---|---|
| FR-ORC-01 | Dagster shall define raw datasets (daily-partitioned) and dbt models as assets. | I |
| FR-ORC-02 | A scheduled GitHub Actions workflow shall run the pipeline daily for the previous trade date, at a configurable time after all feeds are published. | D |
| FR-ORC-03 | Dagster run history shall persist across runs. | D |
| FR-ORC-04 | When a pipeline run fails or produces failure signals, the healing workflow shall start automatically with that run's artifacts. | D |
| FR-ORC-05 | Operators shall be able to run the pipeline manually for a date or date range. | D |

#### 3.2.4 Lineage (FR-LIN)

| ID | Requirement | Verify |
|---|---|---|
| FR-LIN-01 | The system shall collect and store OpenLineage events from loader and dbt runs. | T |
| FR-LIN-02 | The system shall build a column-level lineage graph spanning raw file columns, bronze, silver and gold, with an edge type for each dependency. | T |
| FR-LIN-03 | The system shall provide upstream and downstream traversal from any node, with a depth limit. | T |
| FR-LIN-04 | The graph shall be versioned by dbt manifest hash and rebuilt when models change. | T |
| FR-LIN-05 | Where SQL cannot be parsed at column level, the system shall fall back to table-level edges and flag them. | T |

#### 3.2.5 Detection (FR-DET)

| ID | Requirement | Verify |
|---|---|---|
| FR-DET-01 | The Detector shall collect signals from dbt test failures, model errors, anomaly monitors, ingestion errors and missing expected files. | T |
| FR-DET-02 | Signals sharing a common upstream cause in the lineage graph shall be grouped into one incident. | T |
| FR-DET-03 | Each incident shall record its signals, affected models and columns, downstream gold tables and a severity. | T |
| FR-DET-04 | The Detector shall assign a fault class (schema drift, nulls, duplicates, unit change, late data, or unknown) with a confidence. | T |
| FR-DET-05 | Re-running detection for the same unresolved cause shall not create a duplicate incident. | T |

#### 3.2.6 Investigation (FR-INV)

| ID | Requirement | Verify |
|---|---|---|
| FR-INV-01 | The Investigator shall search upstream from the failing columns using the lineage graph. | T |
| FR-INV-02 | The Investigator shall use only read-only tools: lineage queries, raw file schemas, schema comparison, column profiles, day-over-day comparison, row samples, model SQL, test failure details, ingestion log, corporate actions and calendars. | I |
| FR-INV-03 | The Investigator shall retrieve the most similar past incidents and runbooks. | T |
| FR-INV-04 | The Investigator shall output: root-cause node, fault class, explanation, evidence list, confidence, and up to three ranked alternative hypotheses. | T |
| FR-INV-05 | Each investigation shall respect configurable limits on LLM calls and wall-clock time; when exceeded, the incident is marked unresolved with partial findings. | T |
| FR-INV-06 | The Investigator shall decide whether the fix belongs inside or outside the dbt project. | T |

#### 3.2.7 Fixing (FR-FIX)

| ID | Requirement | Verify |
|---|---|---|
| FR-FIX-01 | The Fixer shall produce patches that modify only allow-listed paths under `dbt/`; any other path shall be rejected before verification. | T |
| FR-FIX-02 | Each patch shall be verified in an isolated sandbox by building the affected subgraph on DuckDB with the incident's data: previously failing tests must pass and previously passing tests must still pass. | T |
| FR-FIX-03 | If verification fails, the Fixer may revise the patch using the failure output, up to a configurable number of attempts (default 2). | T |
| FR-FIX-04 | A verified patch shall be opened as a pull request by the GitHub App, with a description containing the root cause, evidence, lineage path, test results and incident link. | D |
| FR-FIX-05 | When the root cause is outside the dbt project, the Fixer shall open a GitHub issue containing the diagnosis and a drafted change, and shall not open a pull request. | D |
| FR-FIX-06 | If no patch passes verification, no pull request shall be opened and the incident shall be marked "fix unverified" with all attempts attached. | T |
| FR-FIX-07 | The system shall never merge a pull request without an approver's decision. | I, T |

#### 3.2.8 Review (FR-REV)

| ID | Requirement | Verify |
|---|---|---|
| FR-REV-01 | When an approver approves, the system shall submit an approving review and squash-merge the pull request using the approver's GitHub identity, and record the decision. | D |
| FR-REV-02 | Rejection shall require a written reason; the system shall close the pull request and record the decision and reason. | T |
| FR-REV-03 | Only approvers shall be able to approve, reject or trigger actions. | T |
| FR-REV-04 | After a decision, the incident and its outcome shall be indexed for retrieval before the next healing run. | T |

#### 3.2.9 Knowledge and retrieval (FR-KB)

| ID | Requirement | Verify |
|---|---|---|
| FR-KB-01 | The system shall index runbooks and decided incidents, incrementally. | T |
| FR-KB-02 | Retrieval shall combine vector and full-text search using reciprocal rank fusion, with optional filters for fault class and source. | T |
| FR-KB-03 | Benchmark runs shall use an isolated index containing only runbooks and the benchmark's seed split. | T |

#### 3.2.10 API (FR-API)

| ID | Requirement | Verify |
|---|---|---|
| FR-API-01 | The API shall expose endpoints for health, authentication, feed health, incidents, incident lineage, patches, decisions, runbooks and benchmark results. | T |
| FR-API-02 | Authentication shall use GitHub sign-in with server-side sessions; unauthenticated requests receive read-only access. | T |
| FR-API-03 | Errors shall use RFC 9457 problem details; list endpoints shall be paginated. | T |
| FR-API-04 | When an incident is read, the API shall reconcile its pull request or issue state with GitHub. | T |

#### 3.2.11 Dashboard (FR-UI)

| ID | Requirement | Verify |
|---|---|---|
| FR-UI-01 | An overview screen shall show feed freshness, latest run status, open incidents and headline benchmark metrics. | D |
| FR-UI-02 | An incidents screen shall list incidents with filters for status, fault class and feed. | D |
| FR-UI-03 | An incident detail screen shall show summary, root cause, confidence, evidence, similar past incidents, sandbox results and a link to the agent trace. | D |
| FR-UI-04 | The incident detail screen shall show an interactive lineage graph highlighting the path from root cause to failing columns. | D |
| FR-UI-05 | The incident detail screen shall show the patch as a side-by-side diff. | D |
| FR-UI-06 | Approvers shall see approve and reject controls with confirmation; rejection shall require a reason. Non-approvers shall not see the controls. | T |
| FR-UI-07 | A benchmark screen shall show overall and per-class results against baselines. | D |
| FR-UI-08 | **(S)** A runbooks screen shall list and display runbooks. | D |
| FR-UI-09 | Users shall be able to sign in and sign out with GitHub. | T |

#### 3.2.12 Benchmark (FR-BEN)

| ID | Requirement | Verify |
|---|---|---|
| FR-BEN-01 | Faults shall be defined as versioned YAML specifications: about 100, covering all five fault classes and all three feeds, on dates from 2024 onwards. | I |
| FR-BEN-02 | Fault injectors shall be deterministic for a given specification and seed, and shall only modify copies of data. | T |
| FR-BEN-03 | The runner shall evaluate LineageHeal, a rule-based baseline and a plain-LLM baseline under identical conditions. | T |
| FR-BEN-04 | The runner shall report: top-1 and top-3 root-cause accuracy, share of patches passing tests unedited, median and 90th-percentile detection-to-fix time, and LLM calls and tokens per incident, overall and per fault class. | T |
| FR-BEN-05 | The runner shall replay NSE's July 2024 format change as a held-out case. | D |
| FR-BEN-06 | Benchmark runs shall be resumable, persist per-fault results, and produce Markdown and JSON reports. | T |
| FR-BEN-07 | Faults shall be split into seed and test sets; only test-set results are reported, and no test-set fault may appear in the retrieval index. | I, T |

#### 3.2.13 Observability (FR-OBS)

| ID | Requirement | Verify |
|---|---|---|
| FR-OBS-01 | Every agent run shall be traced with the incident ID as metadata when tracing is enabled. | D |
| FR-OBS-02 | All components shall emit structured JSON logs carrying run and incident IDs where applicable. | I |
| FR-OBS-03 | Agent run metrics (timings, LLM calls, tokens, outcome) shall be stored in Postgres. | T |

### 3.3 Non-functional requirements

| ID | Category | Requirement | Verify |
|---|---|---|---|
| NFR-PERF-01 | Performance | Median detection-to-fix time on benchmark test faults shall be under 10 minutes (target 5). | A |
| NFR-PERF-02 | Performance | The daily pipeline for one trade date shall complete within 15 minutes on a GitHub-hosted runner. | D |
| NFR-PERF-03 | Performance | Warm API read endpoints shall respond within 500 ms at the 95th percentile. | T |
| NFR-REL-01 | Reliability | Transient ingestion failures recovered by retries shall not create incidents. | T |
| NFR-REL-02 | Reliability | Re-running the healing workflow for an incident shall not create duplicate pull requests or issues. | T |
| NFR-REL-03 | Reliability | The LLM layer shall fall back to the secondary provider on rate-limit or server errors. | T |
| NFR-SEC-01 | Security | Secrets shall be supplied only through environment variables or GitHub Actions secrets, and scanned for in pre-commit and CI. | I |
| NFR-SEC-02 | Security | The GitHub App shall have only the permissions listed in the design. | I |
| NFR-SEC-03 | Security | All state-changing endpoints shall enforce authorisation on the server and be protected against CSRF. | T |
| NFR-SEC-04 | Security | Data values passed to LLMs shall be treated as untrusted: delimited, size-limited, and never able to widen tool permissions or patch paths. | T |
| NFR-SEC-05 | Security | The `main` branch shall be protected: pull requests and passing CI required. | I |
| NFR-MAINT-01 | Maintainability | Test coverage shall be at least 80% on core Python packages. | A |
| NFR-MAINT-02 | Maintainability | CI shall enforce formatting, linting, type checks and tests for Python, dbt and the frontend. | I |
| NFR-MAINT-03 | Maintainability | Prompts shall be versioned files; every change shall bump the version. | I |
| NFR-PORT-01 | Portability | The full stack shall run locally with Docker Compose on Linux. | D |
| NFR-USAB-01 | Usability | The dashboard shall be keyboard-navigable and meet WCAG 2.1 AA colour contrast. | I |
| NFR-COST-01 | Cost | The system shall run entirely within free tiers. | A |
| NFR-COMP-01 | Compliance | Raw source files shall not be committed to the repository; only small samples may be used as test fixtures. | I |
| NFR-REPRO-01 | Reproducibility | Benchmark runs shall record dependency lockfile hash, model IDs, prompt versions and seeds, and use temperature 0. | I |

### 3.4 Data requirements

| Entity | Purpose | Retention |
|---|---|---|
| Raw files | Exact copies of published files in R2 | Indefinite (immutable) |
| Ingestion log | One row per ingestion attempt | Indefinite |
| Pipeline run | One row per pipeline execution | Indefinite |
| Signal | One failure observation | Indefinite |
| Incident | Grouped signals with status and diagnosis | Indefinite |
| Agent run | One healing execution, with metrics | Indefinite |
| Evidence | Tool outputs supporting a hypothesis | Indefinite |
| Fix | Patch or suggestion, sandbox results, GitHub link | Indefinite |
| Review | Approver decision and reason | Indefinite |
| Lineage event | OpenLineage event payload | 90 days |
| Lineage node / edge | Materialised column-level graph per manifest version | Current and previous version |
| Knowledge document / chunk | Indexed runbooks and incidents with embeddings | Indefinite |
| Benchmark run / result | Benchmark configuration and per-fault outcomes | Indefinite |

The logical model is in [design_architecture.md](design_architecture.md#9-application-data-model).

## 4. Verification

- **Unit tests** (pytest, Vitest) cover requirements marked T at component level, with network and LLM calls mocked.
- **Integration tests** run against Docker Compose services (Postgres, local DuckDB) and fixture data.
- **dbt tests** verify FR-WH requirements in CI against fixture data.
- **End-to-end tests** (Playwright) verify the review flow against a seeded database.
- **Demonstrations** are recorded in the demo video and the Week 6 rehearsal.
- **Analysis** requirements are evaluated from benchmark reports and coverage reports.

## 5. Traceability

| User story (PRD) | Requirements |
|---|---|
| US-01 Daily ingestion | FR-ING-01 to 10, FR-WH-01 to 03, FR-ORC-01 to 05 |
| US-02 Automatic incidents | FR-DET-01 to 05, FR-WH-05 |
| US-03 Root cause with lineage path | FR-LIN-01 to 05, FR-INV-01, 02, 04, FR-UI-04 |
| US-04 Similar incidents and runbooks | FR-INV-03, FR-KB-01, 02 |
| US-05 Verified pull request | FR-FIX-01 to 04, 06 |
| US-06 One-screen review | FR-UI-03 to 06, FR-REV-01 |
| US-07 Rejection reason | FR-REV-02, FR-REV-04 |
| US-08 Fix outside dbt | FR-INV-06, FR-FIX-05 |
| US-09 Feed and table health | FR-UI-01, FR-API-01 |
| US-10 Reproducible benchmark | FR-BEN-01 to 07, NFR-REPRO-01 |
| US-11 Traced agent steps | FR-OBS-01 to 03 |
| US-12 Read-only demo | FR-API-02, FR-UI-06, FR-REV-03 |

## Appendix A — Open issues

See [context.md](context.md#open-questions). Requirements affected by open questions: FR-ORC-02 (source reachability from runners), FR-ING-04 (FBIL history), FR-WH-05 (Elementary compatibility).

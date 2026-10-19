# Product Requirements Document — LineageHeal

| | |
|---|---|
| **Status** | Approved for build |
| **Version** | 1.0 |
| **Last updated** | Week 2 |
| **Related** | [Context](context.md) · [SRS](SRS.md) · [Design](design_architecture.md) · [Backlog](../backlog.md) |

## 1. Overview

LineageHeal is a self-healing data pipeline for Indian financial market data. It ingests daily public feeds from NSE, AMFI and FBIL into a bronze → silver → gold lakehouse. When a data-quality check fails, LLM agents trace the failure through a column-level lineage graph to its root cause, propose a verified fix as a pull request, and wait for a human to approve it.

## 2. Problem

Pipelines built on public financial feeds break often and unpredictably: publishers change formats, values arrive late, blank or in a different unit. Data-observability tools are good at noticing *that* something broke. The expensive part is *diagnosis*: an engineer has to trace the failure upstream through several layers of transformations to find the change that caused it, then write and test a fix. Industry survey data puts the average resolution time at around 15 hours per incident.

## 3. Goals and success metrics

### Product goals

| ID | Goal | Measure | Target |
|---|---|---|---|
| G1 | Find the real root cause | Root-cause accuracy on the benchmark | ≥ 70–80% |
| G2 | Cut time to a reviewable fix | Median time from detection to pull request or suggested fix | < 5–10 minutes |
| G3 | Propose fixes that actually work | Share of patches passing dbt tests without edits | ≥ 70% |
| G4 | Beat simpler approaches | Accuracy vs rule-based and plain-LLM baselines | Higher than both, on every fault class where a difference is measurable |
| G5 | Keep humans in control | Fixes merged without human approval | 0 |

### Engineering goals

| ID | Goal | Measure |
|---|---|---|
| E1 | Reliable daily pipeline | Scheduled runs succeed on all trading days without manual intervention, apart from genuine source faults |
| E2 | Production-grade codebase | CI passes on `main`; test coverage ≥ 80% on core Python packages |
| E3 | Zero running cost | All services on free tiers |
| E4 | Reproducible evaluation | Benchmark can be rerun from a clean checkout with one command |

## 4. Non-goals

- Investment advice, analytics products or trading signals.
- Automatic merging of fixes, under any configuration.
- Real-time or intraday data.
- Automatic patching of loader code or source data. Faults rooted outside dbt get a diagnosis and a suggested fix only.
- Paid infrastructure.

## 5. Personas

**Priya, on-call data engineer (primary).** Gets paged when the morning pipeline fails. Wants to know what broke, why, and what to change, without reading every model between the source and the failing table.

**Arjun, reviewer.** Approves changes to production data models. Needs enough evidence (lineage path, diff, test results, reasoning) to trust or reject a fix in a few minutes.

**Meera, data consumer.** Uses gold tables for dashboards and research. Needs the tables to be correct, or clearly flagged when they are not.

**Researcher.** Evaluates the approach. Needs a reproducible benchmark with defined metrics and fair baselines.

## 6. User stories

| ID | As a… | I want… | So that… |
|---|---|---|---|
| US-01 | data engineer | the pipeline to ingest NSE, AMFI and FBIL data every trading day | gold tables are current without manual work |
| US-02 | data engineer | an incident opened automatically when a test or anomaly check fails | I don't depend on someone noticing |
| US-03 | data engineer | the incident to show the most likely root cause and the lineage path to it | I don't trace it by hand |
| US-04 | data engineer | similar past incidents and relevant runbooks shown with the diagnosis | known problems are solved the known way |
| US-05 | reviewer | a pull request containing a dbt patch that has already passed `dbt build` | I review a working fix, not a guess |
| US-06 | reviewer | to approve or reject from one screen with the diff, evidence and agent reasoning | review takes minutes |
| US-07 | reviewer | to give a reason when I reject | the system learns from the rejection |
| US-08 | data engineer | a diagnosis and a drafted change when the cause is outside dbt | source-side changes still get a head start |
| US-09 | data consumer | to see which feeds and tables are healthy, stale or affected by an open incident | I know whether to trust today's data |
| US-10 | researcher | to run the fault-injection benchmark with one command and get a results report | results are reproducible |
| US-11 | data engineer | every agent step traced | I can debug the agents themselves |
| US-12 | visitor to the demo | to explore incidents and fixes read-only | I can understand the system without being able to change anything |

## 7. Scope

All features below are in scope for the build. The order reflects build sequence, and if time runs short, later items are trimmed in depth rather than dropped.

| ID | Feature | Summary |
|---|---|---|
| F1 | Ingestion | Loaders for NSE CM-UDiFF bhavcopy, NSE security master and corporate actions, AMFI NAVAll and FBIL reference rates; raw files stored immutably in R2; historical backfill from January 2024 |
| F2 | Lakehouse | dbt bronze, silver and gold models on MotherDuck (prod) and DuckDB (dev, CI, benchmark) with schema tests and anomaly monitors |
| F3 | Orchestration | Dagster assets and schedules, running daily in GitHub Actions |
| F4 | Lineage | Column-level lineage graph from OpenLineage events and sqlglot parsing |
| F5 | Healing agents | Detector, Investigator and Fixer in LangGraph; hybrid retrieval over incidents and runbooks; sandbox verification; pull requests for dbt fixes; issues for out-of-dbt suggestions |
| F6 | Dashboard | React dashboard with GitHub login: pipeline health, incidents, lineage graph, patch diff, agent reasoning, approve and reject |
| F7 | Benchmark | Fault injectors for five fault classes, held-out July 2024 NSE case, rule-based and plain-LLM baselines, metrics report |
| F8 | Observability | LangSmith tracing, structured logs, persisted run metrics |

## 8. Key user flows

**Daily run (no faults).** The scheduled workflow ingests the day's files, builds all layers and runs all tests. The dashboard shows every feed as healthy with today's date.

**Incident with a dbt fix.**
1. A silver test fails after NSE renames a column.
2. The Detector opens an incident with the failing test, affected models and downstream impact.
3. The Investigator walks the lineage graph upstream, compares the raw file's columns with the bronze model, retrieves similar incidents, and names the root cause with evidence.
4. The Fixer patches the bronze model, runs `dbt build` on the affected subgraph in a sandbox, and opens a pull request.
5. The reviewer opens the incident in the dashboard, reads the root cause, inspects the lineage path and diff, and approves. The pull request is merged under the reviewer's GitHub identity.

**Rejection.** The reviewer rejects with a reason. The pull request is closed, and the incident and reason are stored so future investigations can retrieve them.

**Root cause outside dbt.** NSE changes a file's URL pattern, so no file is downloaded. The Investigator diagnoses a source change from the loader error and the missing raw object. The Fixer drafts the loader change as a GitHub issue, and the dashboard marks the incident as needing a manual fix.

**Benchmark.** A researcher triggers the benchmark workflow. Faults are injected into copies of historical data, each system (LineageHeal and the two baselines) attempts every fault on local DuckDB, and a results report with per-class metrics is produced.

## 9. Dashboard screens

| Screen | Shows |
|---|---|
| Overview | Feed freshness, latest run status, open incidents, headline benchmark metrics |
| Incidents | Filterable list: status, fault class, feed, affected models, time to fix |
| Incident detail | Summary, root cause and confidence, interactive lineage path, evidence, similar past incidents, patch diff, sandbox test results, link to agent trace, approve and reject actions |
| Benchmark | Per-class and overall results against baselines, with charts |
| Runbooks | The runbooks used for retrieval |

## 10. Release plan

| Week | Milestone | Exit criteria |
|---|---|---|
| 2 | Foundations | Repo, tooling, CI and Docker in place; data spike done; loaders writing to R2 |
| 3 | Lakehouse | All layers built and tested on MotherDuck; Dagster schedule running in Actions; backfill complete |
| 4 | Diagnosis | Lineage graph built; Detector and Investigator diagnose injected faults end to end |
| 5 | Healing and UI | Fixer opens verified pull requests and issues; dashboard supports full review and approval |
| 6 | Evaluation and launch | Full benchmark with baselines and held-out case; deployed demo; documentation and demo video complete |

## 11. Assumptions and dependencies

- NSE, AMFI and FBIL keep publishing their files publicly and accessibly.
- Free tiers remain roughly as documented in [techstack.md](techstack.md).
- Both team members can create the required accounts.

## 12. Risks

See [context.md](context.md#risks).

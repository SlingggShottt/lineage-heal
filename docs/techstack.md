# Tech Stack

> What LineageHeal is built with, why each tool was chosen, what we considered instead, and the free-tier limits we design around. Exact versions are pinned in `uv.lock` and `web/pnpm-lock.yaml`; this document records version lines and reasoning.
>
> **Last verified:** October 2026. Free tiers change often. Re-check the limits below before any large benchmark run.

## Selection principles

1. **Free to run end to end.** Every service has a permanent free tier, not a trial.
2. **Industry-standard.** Tools that data and AI engineering teams actually use, so the skills transfer.
3. **Typed and testable.** Static types in Python and TypeScript, and every layer testable locally.
4. **Local-first.** The whole system runs in Docker Compose on a laptop. Cloud services are swapped in through configuration.
5. **Boring plumbing, novel core.** Proven tools for ingestion, storage and UI, so the effort goes into lineage-aware healing.

## At a glance

| Layer | Choice | Version line |
|---|---|---|
| Language | Python | 3.12 |
| Packaging | uv | latest |
| Command-line interface | Typer | latest |
| Raw storage | Cloudflare R2 (S3-compatible) | — |
| Warehouse | DuckDB (local), MotherDuck (shared) | DuckDB 1.x |
| Transformation | dbt Core + dbt-duckdb | 1.x (latest supported by dbt-duckdb) |
| Data quality | dbt tests, dbt_utils, dbt_expectations, Elementary | latest compatible |
| Orchestration | Dagster OSS + dagster-dbt | 1.x |
| Lineage | OpenLineage, sqlglot, NetworkX | latest |
| Agents | LangGraph + LangChain core, Postgres checkpointer | 1.x |
| LLMs | Gemini (primary), Groq (fallback) | see [Models](#models) |
| Retrieval | Postgres + pgvector, fastembed | Postgres 17 |
| App database | SQLAlchemy 2.0, psycopg 3, Alembic | 2.x / 3.x |
| API | FastAPI, Uvicorn, Pydantic v2 | latest |
| Frontend | React, TypeScript, Vite, Tailwind CSS | React 19, Node 24 LTS |
| Observability | LangSmith, structlog | — |
| CI/CD | GitHub Actions | — |
| Hosting | Render (API), Cloudflare Pages (web), Neon (Postgres) | — |
| Containers | Docker, Docker Compose | — |

## Python platform

| Tool | Purpose |
|---|---|
| **uv** | Dependency management, lockfile, virtual environments, Python version management |
| **uv_build** | Build backend for the `lineageheal` package; versioned in lockstep with uv |
| **ruff** | Linting and formatting (replaces black, isort and flake8) |
| **mypy** | Static type checking, strict mode on `src/` |
| **pytest**, pytest-cov, pytest-asyncio | Test runner, coverage, async tests |
| **respx** | Mocking httpx requests in tests |
| **pre-commit** | Runs ruff, mypy, sqlfluff, gitleaks and commit-message checks before each commit |
| **pydantic v2** + pydantic-settings | Data models, validation, typed configuration from environment variables |
| **structlog** | Structured JSON logging |
| **httpx** + **tenacity** | HTTP client with timeouts, and retries with backoff |
| **Typer** | The `lineageheal` command-line tool (`ingest`, `backfill`, `detect`, `heal`, `index`, `bench`), so GitHub Actions and developers run the same code path |

**Considered:** Poetry (slower; uv has become the default for new projects), black + isort + flake8 (ruff does all three faster), pyright (strong, but mypy is more common in data teams), argparse and Click for the CLI (argparse needs more boilerplate; Typer builds on Click and derives commands from type hints).

## Ingestion and raw storage

Loaders are plain Python modules using httpx. Each one downloads a feed for a trade date and writes the file, unchanged, to **Cloudflare R2** through its S3-compatible API using **boto3**.

A local-directory backend sits behind the same interface for tests and sandboxes. boto3 carries its own timeouts and retries (standard mode, five attempts), since it does not use httpx.

**Why R2:** S3-compatible, no egress fees (DuckDB and MotherDuck read the raw zone repeatedly), and a permanent free tier.

**Considered:** AWS S3 (egress charges, and its free tier expires after 12 months), Backblaze B2 (viable fallback if R2 sign-up is a problem).

## Warehouse and transformation

| Tool | Role |
|---|---|
| **DuckDB** | Local development, CI tests and the benchmark sandbox |
| **MotherDuck** | Shared, persistent warehouse for the live pipeline |
| **dbt Core** + **dbt-duckdb** | Bronze, silver and gold models; one project, with `dev`, `ci`, `bench` and `prod` targets |
| **dbt_utils**, **dbt_expectations** | Reusable generic tests |
| **Elementary** | Anomaly detection (volume, freshness, column distributions) and test result history |
| **sqlfluff** | SQL linting with the dbt templater |

DuckDB reads raw files directly from R2 using its R2 secret type, so no separate loading step is needed between storage and bronze.

**Why dbt 1.x and not dbt 2.0:** dbt 2.0 (Rust engine, built-in DuckDB adapter) was released in September 2026. Dagster's dbt integration, OpenLineage's dbt integration, Elementary and sqlfluff's templater all target the Python engine, and LineageHeal depends on all four. We will revisit after the build.

**Considered:** BigQuery sandbox (vendor lock-in and sandbox restrictions), Snowflake (trial only), Postgres as the warehouse (row-oriented, not built for analytics).

## Orchestration

**Dagster OSS** with **dagster-dbt** models every loader output and dbt model as a software-defined asset, so the Dagster asset graph mirrors the lakehouse layers. Scheduled runs execute in **GitHub Actions**. Run history is persisted with **dagster-postgres**, so the local Dagster UI can browse scheduled runs.

**Considered:** Airflow (heavier to run, task-centric rather than asset-centric), Prefect (capable, but its asset model is less mature for layered tables), Dagster+ (hosted; paid after a trial).

## Lineage

| Tool | Role |
|---|---|
| **OpenLineage** (Python client, `openlineage-dbt`) | Standard lineage events for loader runs and dbt runs |
| **sqlglot** | Parses compiled dbt SQL to derive column-level lineage |
| **NetworkX** | In-memory graph for upstream and downstream traversal |

OpenLineage events are stored as JSONB in Postgres, and the column-level graph is rebuilt from events plus the dbt manifest.

**Considered:** Marquez (reference OpenLineage server, but needs an always-on service), dbt manifest alone (table-level only; column-level needs SQL parsing).

## Agents and LLMs

**LangGraph** runs the Detector → Investigator → Fixer workflow as a stateful graph, with explicit human-in-the-loop interrupts. **LangChain core** supplies the model interfaces. Agent outputs are validated as Pydantic models (structured output).

**langgraph-checkpoint-postgres** persists graph state to Postgres. A healing run pauses at the review interrupt and its GitHub Actions job ends; when an approver decides, a new job resumes the graph from the saved checkpoint. Without a durable checkpointer, the workflow would have to stay running for however long review takes.

### Models

| Role | Model | Provider |
|---|---|---|
| Investigation and patch writing | Gemini 3.5 Flash | Google AI Studio (free tier) |
| Lightweight steps (classification, summaries) | Gemini 3.1 Flash-Lite | Google AI Studio (free tier) |
| Fallback when Gemini is rate-limited or down | `openai/gpt-oss-120b` | Groq (free tier) |

Model IDs live in configuration, never in code. All calls go through `lineageheal.llm`, which handles rate limiting, retries and fallback.

**Considered:** CrewAI and AutoGen (less explicit control over state and branching), OpenAI and Anthropic APIs (no free tier), local models via Ollama (not available in CI runners), LangGraph's in-memory or SQLite checkpointers (lost when an ephemeral runner exits).

## Retrieval (RAG)

| Tool | Role |
|---|---|
| **Postgres + pgvector** (Neon) | Vector search over past incidents and runbooks |
| **Postgres full-text search** | Keyword search, combined with vector search using reciprocal rank fusion |
| **fastembed** with `BAAI/bge-small-en-v1.5` | Local 384-dimension embeddings, CPU-only, no API calls |

A cross-encoder reranker (also via fastembed) will be evaluated during the build and kept only if it measurably improves root-cause accuracy.

**Considered:** Pinecone and Qdrant Cloud (an extra service to manage, and idle limits on free clusters), Chroma (a separate store when Postgres already holds the data), Gemini embeddings (rate limits, and results could shift between model versions, hurting benchmark reproducibility).

## Application backend

| Tool | Role |
|---|---|
| **FastAPI** + **Uvicorn** | REST API with automatic OpenAPI schema |
| **SQLAlchemy 2.0** (async) + **psycopg 3** | Database access |
| **Alembic** | Schema migrations |
| **Authlib** | GitHub sign-in through the GitHub App's user-authorisation flow |
| **PyGithub** with a **GitHub App** | Branches, pull requests, issues and merges by the LineageHeal bot |

## Frontend

| Tool | Role |
|---|---|
| **React 19** + **TypeScript** (strict) + **Vite** | Single-page dashboard |
| **Tailwind CSS** + **shadcn/ui** | Styling and accessible components (built on Radix) |
| **TanStack Query** | Server state, caching, polling |
| **React Router** | Routing |
| **React Flow** (`@xyflow/react`) | Interactive lineage graph |
| **Monaco diff editor** | Side-by-side patch review |
| **Recharts** | Benchmark and pipeline-health charts |
| **openapi-typescript** + **openapi-fetch** | API types and client generated from FastAPI's OpenAPI schema |
| **Vitest**, **React Testing Library**, **MSW** | Unit and component tests with mocked API |
| **Playwright** | End-to-end tests |
| **ESLint** + **Prettier** | Linting and formatting |
| **pnpm** | Package management |

**Considered:** Next.js (server rendering isn't needed when FastAPI is the backend), Streamlit (faster to build, but a weaker frontend engineering signal).

## Observability

- **LangSmith** traces every agent run: prompts, tool calls, tokens, latency.
- **structlog** writes JSON logs from every component.
- **Run metrics** (incident outcome, root-cause correctness, timings) are stored in Postgres, because LangSmith's free plan keeps traces for only 14 days.

## CI/CD and infrastructure

| Workflow | Trigger | Does |
|---|---|---|
| `ci.yml` | Every pull request and push to `main` | Python lint, types and tests; dbt build on DuckDB with fixtures; frontend lint, types, tests and build |
| `pipeline.yml` | Daily cron, manual | Ingest feeds, run dbt on MotherDuck |
| `heal.yml` | Pipeline failure, manual, resume after review | Run the agent workflow on the new incident, or resume it from its checkpoint |
| `benchmark.yml` | Manual only | Fault-injection benchmark on local DuckDB |

Third-party actions are kept to a minimum and pinned to commit SHAs: `actions/checkout` and `astral-sh/setup-uv` (both MIT). The CI job installs the same uv version that created `uv.lock` and fails if the lockfile is out of date.

Deployments: Render auto-deploys the API from `main`; Cloudflare Pages builds the dashboard from `main`. Dependabot keeps dependencies current.

## Free-tier budget

| Service | Free allowance (Oct 2026) | Expected use | Guardrail |
|---|---|---|---|
| GitHub Actions | Unlimited minutes for public repos on standard runners; 2,000 min/month if private | Daily pipeline + CI | Keep the repo public; scheduled workflows pause after 60 days without repo activity |
| Cloudflare R2 | 10 GB storage plus monthly operation quotas | Under 2 GB of compressed raw files | Raw files stored compressed |
| MotherDuck (Lite) | 10 GB storage, 10 CU-hours/month, 3 users | Daily incremental runs | Benchmark runs on local DuckDB (D-11); usage checked weekly |
| Neon | 100 CU-hours and 0.5 GB per project; scales to zero after 5 min idle | Under 0.3 GB | Separate projects for production and benchmark; retention policy on lineage events |
| Gemini API | Flash and Flash-Lite free; per-project limits shown in AI Studio | Development + about 1,500–2,500 calls for the full benchmark | Client-side rate limiter; only public data is sent |
| Groq | Free with per-model limits (commonly 30 RPM / 1,000 RPD) | Fallback only | Same rate limiter |
| LangSmith | 1 seat, 5,000 traces/month, 14-day retention | A few hundred traces per month | Each developer uses a personal account; tracing off in unit tests; metrics persisted to Postgres |
| Render | 512 MB web service, spins down after 15 min, ~1 min to wake | Demo API | Warm-up request before demos |
| Cloudflare Pages | Free static hosting with a monthly build quota | Dashboard | Build only from `main` |

## Accounts and credentials

One shared account per service holds production credentials, stored as GitHub Actions secrets. Locally, each developer copies `.env.example` to `.env`.

| Service | Environment variables |
|---|---|
| Cloudflare R2 | `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` |
| MotherDuck | `MOTHERDUCK_TOKEN` |
| Neon | `DATABASE_URL` |
| Google AI Studio | `GEMINI_API_KEY` |
| Groq | `GROQ_API_KEY` |
| LangSmith | `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` |
| GitHub App (bot actions) | `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_APP_INSTALLATION_ID` |
| GitHub App (dashboard sign-in) | `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET`, `SESSION_SECRET`, `APPROVER_GITHUB_LOGINS` |

The GitHub App provides both the bot identity and user sign-in; the client ID and secret come from the App's settings page, so no separate OAuth App is needed.

Note: Cloudflare may ask for a payment method before enabling R2, even for free-tier usage. If that is a blocker, Backblaze B2 is the fallback, using the same S3 code path.

## Deliberately not used

| Tool | Why not |
|---|---|
| dbt 2.0 | Too new for the integrations we depend on (see above) |
| Marquez | Needs an always-on server; Postgres covers our lineage storage needs |
| Dagster+ | Paid after a trial |
| Supabase | Free projects pause after inactivity; Neon's scale-to-zero suits intermittent use better |
| Hosted vector databases | An extra service when pgvector already covers the need |
| Kafka or streaming tools | Feeds are daily batch files |

# CLAUDE.md

Guidance for Claude Code when working in this repository. Read this file in full at the start of every session.

@docs/context.md

## What this project is

LineageHeal is a self-healing data pipeline for Indian financial market data. Python loaders ingest daily public feeds (NSE bhavcopy, AMFI NAVs, FBIL reference rates) into a bronze → silver → gold lakehouse built with dbt on MotherDuck and orchestrated by Dagster. When a data-quality check fails, a LangGraph multi-agent system (Detector → Investigator → Fixer) traces the fault through a column-level lineage graph, retrieves similar past incidents, and opens a GitHub pull request containing a verified dbt patch. A human approves or rejects every fix from a React dashboard.

## Sources of truth

| Question | Read |
|---|---|
| Why does this exist? What is in and out of scope? | `docs/PRD.md` |
| Exact requirements and acceptance criteria | `docs/SRS.md` |
| How the components fit together | `docs/design_architecture.md` |
| Naming, code and documentation conventions | `docs/style_guide.md` |
| Libraries, versions and free-tier limits | `docs/techstack.md` |
| Project background, decisions, constraints, status | `docs/context.md` |
| GitHub settings that live outside the repository (branch protection, merge rules) | `docs/repository_settings.md` |
| What to work on next | `backlog.md` |

If these documents conflict with each other or with the code, stop and ask. Do not silently pick one.

## How we work

We are building the whole project task by task, following `backlog.md`. We want to understand every file, not just receive it.

### Starting a session

1. Read this file, `backlog.md` and `docs/context.md`.
2. Summarise in a few lines where the project stands and confirm the current diary week with us (it is recorded under "Current status" in `docs/context.md`).
3. Pick the next task: any item marked `In progress` first, otherwise the first `Todo` item in week order whose prerequisites are done. Tell us which task and why.

### Working on a task

1. **Plan first.** Before creating anything, give us:
   - the ordered list of files you will create or change, one line each on its purpose;
   - every new dependency, with what it is, why this task needs it, the alternative considered, and its licence;
   - any questions you need answered. Ask, never assume.

   Then wait for approval. Mark the task `In progress` in `backlog.md` once approved.
2. **Branch.** Give us the command to create the task branch from an up-to-date `main` (`git checkout main && git pull && git checkout -b <type>/<id>-<slug>`) and wait until we confirm.
3. **One file at a time.** For each file in the approved order:
   - **Before writing:** explain what the file is, why the project needs it, and what problem it solves.
   - **Write the file** (or make the change, if it already exists).
   - **After writing:** walk us through how it works section by section, explain important choices and trade-offs, and tell us how to check it works.
   - **Doc sync:** check the table below and update any document the file affects.
   - **Stop and wait** for us to reply "next". Handle questions or change requests first.

   If we reply "batch", you may write the remaining small, boilerplate files of the current task without pausing, then explain them together.
4. **Manual tasks.** Some tasks need us to act outside the repo (creating accounts, configuring GitHub, deploying). For those, give step-by-step instructions, tell us exactly which values to put where (never ask us to paste secrets into the chat), and wait for confirmation.
5. **Finish the task.** Run every quality gate that applies, report results, complete the end-of-task doc updates, then give the commit message and commands (see "When you finish a task"). Wait for us to confirm the push and merge before starting the next task.

### Doc sync

Keep the documentation true at all times. Doc changes go in the same commit as the code that caused them.

| When this happens | Update |
|---|---|
| A task starts | `backlog.md`: status `In progress` |
| A task finishes | `backlog.md`: status `Done`, week, done-log line. `docs/context.md`: "Current status" |
| A dependency is added or removed | `docs/techstack.md` (tool, purpose, alternatives considered) |
| A new environment variable or setting | `.env.example`, and the credentials table in `docs/techstack.md` if it's a service credential |
| A decision is made or changed | `docs/context.md` "Key decisions" (next D-number). If significant, also an ADR in `docs/adr/` |
| Architecture, data model, API or agent design changes | `docs/design_architecture.md` |
| A spike produces findings | The relevant section of `docs/design_architecture.md` (e.g. source access details) |
| A requirement turns out wrong or missing | Propose the change to `docs/SRS.md` and ask before editing. Scope changes also touch `docs/PRD.md` |
| A new convention is adopted | `docs/style_guide.md` |
| An open question is answered or a new risk appears | `docs/context.md` "Open questions" or "Risks" |
| Setup steps or commands change | `README.md` "Getting started" and the Commands section of this file |
| New work is discovered | `backlog.md`: new item with the next free ID, under the right week |

Never change goals, metrics or scope in `docs/PRD.md` or `docs/SRS.md` without asking first.

## Repository map

```
lineage-heal/
├── src/lineageheal/          Python package
│   ├── core/                 Settings, logging, errors, shared types
│   ├── ingestion/            Feed loaders (nse, amfi, fbil) and R2 storage client
│   ├── orchestration/        Dagster assets, jobs, schedules, sensors
│   ├── lineage/              dbt artifact parsing, OpenLineage collection, sqlglot parsing, lineage graph
│   ├── agents/               LangGraph graph, Detector/Investigator/Fixer nodes, tools, prompts
│   ├── rag/                  Chunking, embeddings, hybrid retrieval over incidents and runbooks
│   ├── llm/                  Provider abstraction (Gemini primary, Groq fallback)
│   ├── vcs/                  GitHub client: branches, patches, pull requests, issues
│   ├── store/                SQLAlchemy models, repositories, Alembic migrations (Postgres)
│   ├── api/                  FastAPI app: routers, schemas, dependencies, auth
│   ├── benchmark/            Fault injectors, runner, metrics, baselines
│   └── cli.py                Typer entry point: ingest, backfill, detect, heal, index, bench
├── dbt/                      dbt project: models/{bronze,silver,gold}, macros, tests, seeds
├── web/                      React + TypeScript dashboard (Vite)
├── tests/                    unit/, integration/, e2e/ — mirrors src/ layout
├── docs/                     PRD, SRS, design, style guide, tech stack, context, ADRs, runbooks
├── docker/                   Dockerfiles (docker-compose.yml lives at the repo root)
└── .github/workflows/        CI, scheduled pipeline, heal, benchmark
```

## Commands

```bash
# Setup
uv sync --all-extras                 # Python dependencies
pnpm --dir web install               # Frontend dependencies
cp .env.example .env                 # Fill in values. Never commit .env.

# Local stack
make up                              # Postgres with pgvector; the API and dashboard are added by later tasks
make dagster                         # Dagster UI and daemon at http://localhost:3000 (raw assets by trade date)
make down
docker compose run --rm app ingest amfi --date 2026-10-15   # any lineageheal command, in a container

# Ingestion (writes to RAW_ZONE_DIR, or to R2 when RAW_ZONE_BACKEND=r2)
uv run lineageheal ingest amfi --date 2026-10-15
uv run lineageheal ingest nse --date 2026-10-15
uv run lineageheal ingest nse --dataset security_master --date 2026-10-15   # or corporate_actions
uv run lineageheal backfill amfi --start 2026-10-01 --end 2026-10-15   # resumes; --refresh re-downloads, --skip-weekends for NSE
uv run lineageheal db upgrade                    # needs DATABASE_URL and `make up`
uv run lineageheal calendar fetch-nse            # NSE's official holidays for this year into the calendar seed
uv run lineageheal log --limit 10                # recent ingestion attempts
uv run lineageheal signals RUN_ID                # failure signals recorded for a pipeline run
uv run lineageheal dev inject-fault amfi --fault blank-nav --date 2026-10-15   # local raw zone only

# Quality gates — all must pass before proposing a commit
uv run ruff format . && uv run ruff check .
uv run mypy src
uv run pytest
make test-integration                # tests that need the local Postgres (start it with `make up`)
uv run dbt build --project-dir dbt --profiles-dir dbt --target dev
pnpm --dir web lint && pnpm --dir web typecheck && pnpm --dir web test
```

A `Makefile` wraps these (`make setup`, `make up`, `make check`, `make test`). Prefer make targets once they exist. If a command above does not exist yet, set it up as part of the relevant backlog task rather than inventing an alternative. Only run the gates that apply to what exists so far.

## Working agreements (non-negotiable)

1. **Never commit, push or rewrite history.** Do not run `git commit`, `git push`, `git merge`, `git rebase`, `git reset --hard` or `git tag`. When a task is done, provide a commit message and the exact commands. We run them ourselves.
2. **No AI attribution** in commit messages, PR descriptions, code comments or file headers. No `Co-Authored-By` trailers.
3. **Ask before adding any dependency**: Python package, npm package, dbt package, Docker image or GitHub Action. Say what it is, why it is needed, which alternative you considered, and its licence. Wait for approval.
4. **Write tests alongside code** in the same task. A task without tests is not done.
5. **Keep the docs in sync** using the doc-sync table above.
6. **Keep changes small and reviewable.** One backlog item per change. If a change grows beyond roughly 400 lines, stop and propose a split.
7. **Ask when unsure.** Do not guess requirements, data formats or credentials. Ask, or present options with trade-offs.
8. **Never handle secrets in chat.** Tell us which variable to set and where; never ask us to paste a key, and never print `.env` contents.

Also ask before you: change a gold-layer schema, change a public API contract, write a migration that drops or rewrites data, edit a CI workflow, delete files, or run anything that consumes LLM quota at scale (e.g. a full benchmark run).

## Comments and documentation in code

Write comments the way a senior engineer would in a production codebase:

- Comments explain **why**, not what. Never restate what the code or setting obviously does.
- Every config file (`pyproject.toml`, YAML, `Makefile`, `.gitignore`, `.editorconfig`, `.env.example`, Dockerfiles, workflows) starts with a short header comment explaining its purpose, with brief comments on non-obvious settings, grouped by section.
- Python modules start with a module docstring. Public functions and classes use the Google docstring style from `docs/style_guide.md`.
- dbt models are documented in their YAML files; SQL comments are reserved for non-obvious logic.
- TypeScript uses TSDoc on exported functions, hooks and components whose purpose isn't obvious from the name.
- Formats that don't support comments (such as JSON) get none; explain them in chat instead.
- Mark known limitations as `TODO(LH-###): <what and why>`, always linked to a backlog item.
- No filler comments, no commented-out code.

## Definition of Done

- Passes every quality gate that applies.
- New or changed behaviour is covered by tests. Network and LLM calls are mocked in unit tests.
- Public functions and classes are typed and have docstrings.
- Every new or changed dbt model has a YAML entry with descriptions and tests.
- All doc-sync updates are made, including `.env.example`.
- `backlog.md` is updated, and a commit message with commands has been provided.

## Engineering rules

### Python
- Python 3.12, fully type-hinted. mypy runs in strict mode on `src/`.
- Read configuration only through `lineageheal.core.settings` (pydantic-settings). Never read `os.environ` anywhere else.
- Log with structlog using key-value fields. Never use `print()` in library code.
- Make HTTP calls with httpx, always with explicit timeouts and tenacity retries. Mock with respx in tests.
- Never put secrets in code, logs, fixtures, error messages or test output.

### Data
- Raw files in R2 are immutable. Loaders write new objects and never overwrite or delete raw data.
- Loaders are idempotent per `(source, trade_date)`.
- Store raw files as published (schema-on-read). Typing and renaming happen in dbt, which is where schema drift should surface.
- Store prices, NAVs and rates as `DECIMAL` in the warehouse, never as floating point.
- Trade dates are Asia/Kolkata calendar dates. A missing file on an exchange or bank holiday is not an error.
- Be polite to source sites: browser-like headers where required, low request rates, and cached downloads during development.
- Tests use small fixture files under `tests/fixtures/`. Tests never download live data.

### dbt
- Bronze = typed raw, silver = cleaned and conformed, gold = business-ready. A model may only read from its own layer or a lower one.
- Every model has a YAML entry with model and column descriptions plus tests.
- Targets: `dev`, `ci` and `bench` run on local DuckDB; `prod` runs on MotherDuck. Never run the benchmark against `prod`.

### Agents and LLMs
- All LLM calls go through `lineageheal.llm`. Agent code never imports a provider SDK directly.
- Model IDs come from configuration, never hard-coded.
- Prompts live as versioned files in `src/lineageheal/agents/prompts/`, not as inline strings.
- The Fixer may only modify files under `dbt/` (models, macros, schema YAML). It never touches loaders, raw data, CI or application code.
- When the root cause is outside `dbt/`, the Fixer drafts a suggested change as a GitHub issue and dashboard entry. It never opens a pull request for non-dbt code.
- Every patch must pass `dbt build` on the affected subgraph in a sandbox before a pull request is opened.
- Nothing merges without human approval. Do not build any auto-merge path, even behind a flag.
- Pull requests and issues are created by the LineageHeal GitHub App, never with a team member's personal token.
- Unit tests use recorded or fake LLM responses. CI unit tests never call real LLM APIs.
- Stay within free-tier rate limits documented in `docs/techstack.md`.

### API and security
- Only GitHub accounts listed in `APPROVER_GITHUB_LOGINS` can approve, reject or trigger actions. Everyone else is read-only.
- Every state-changing endpoint checks authorisation on the server. Never rely on the UI hiding a button.

### Frontend
- TypeScript in strict mode. Server state lives in TanStack Query.
- API types are generated from the FastAPI OpenAPI schema, not written by hand.

## When you finish a task

Reply with:

1. **Summary**: what changed and why.
2. **Verification**: commands run and their results.
3. **Docs updated**: every document changed by doc sync, one line each.
4. **Commit**: a Conventional Commit message and the commands, for example:

```bash
git add src/lineageheal/ingestion/nse.py tests/unit/ingestion/test_nse.py backlog.md docs/context.md
git commit -m "feat(ingestion): add NSE UDiFF bhavcopy loader" -m "Refs: LH-017"
git push -u origin feat/LH-017-nse-bhavcopy-loader
```

Then tell us to open the pull request, wait for CI, and squash-merge. Start the next task only after we confirm the merge.

Branch names follow `<type>/<backlog-id>-<short-slug>`. Backlog IDs use the form `LH-###`.

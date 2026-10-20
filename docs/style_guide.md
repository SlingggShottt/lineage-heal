# Style Guide — LineageHeal

Conventions for code, data models, prompts, documentation and Git. Tooling enforces most of these (ruff, mypy, sqlfluff, ESLint, Prettier, pre-commit). This guide covers what tools cannot check.

## 1. Principles

1. **Readable over clever.** Code is read far more often than it is written.
2. **Explicit over implicit.** Types, names and errors say what they mean.
3. **Small units.** Short functions, focused modules, small pull requests.
4. **Tests are documentation.** A test name should read as a statement of behaviour.
5. **Consistency beats preference.** Follow the existing pattern unless there is a documented reason to change it.

## 2. Git workflow

### 2.1 Branches

GitHub Flow: `main` is always deployable, and all work happens on short-lived branches merged through pull requests.

| Branch | Format | Example |
|---|---|---|
| Feature, fix, docs, etc. | `<type>/<backlog-id>-<slug>` | `feat/LH-017-nse-bhavcopy-loader` |
| Agent-generated fix | `heal/<incident-id>` | `heal/INC-0042` |

### 2.2 Commit messages

[Conventional Commits](https://www.conventionalcommits.org/), enforced by a pre-commit hook.

```
<type>(<scope>): <imperative summary, max 72 chars>

<optional body: what and why, wrapped at 72 chars>

Refs: LH-017
```

**Types:** `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `build`, `ci`, `chore`.

**Scopes:** `ingestion`, `dbt`, `orchestration`, `lineage`, `agents`, `rag`, `llm`, `vcs`, `store`, `api`, `web`, `benchmark`, `core`, `ci`, `docker`, `docs`, `deps`.

No AI attribution trailers in commits or pull requests.

### 2.3 Pull requests

- One backlog item per pull request; aim for under 400 changed lines.
- Squash-merge into `main`; the squash message follows the commit format above.
- CI must pass before merging.

Pull request description:

```markdown
## What
<one or two sentences>

## Why
<link to backlog item and SRS requirement IDs>

## How
<key implementation notes, trade-offs>

## Testing
<what was run; screenshots for UI changes>

## Checklist
- [ ] Tests added or updated
- [ ] Docs and .env.example updated if needed
- [ ] backlog.md updated
```

## 3. Python

### 3.1 Layout and naming

| Element | Convention | Example |
|---|---|---|
| Modules, packages | `snake_case` | `cm_bhavcopy.py` |
| Classes | `PascalCase` | `BhavcopyLoader` |
| Functions, variables | `snake_case` | `load_trade_date` |
| Constants | `UPPER_SNAKE_CASE` | `MAX_RETRIES` |
| Private | Leading underscore | `_parse_header` |
| Type aliases | `PascalCase` | `NodeId` |

### 3.2 Types and data

- Every function signature is fully typed. No `Any` without a comment explaining why.
- Use Pydantic models at boundaries (API, config, LLM output, file specs) and dataclasses or plain types internally.
- Use `datetime.date` for trade dates and timezone-aware `datetime` (UTC) for timestamps. Never naive datetimes.
- Use `decimal.Decimal` for money and rates in Python code that computes with them.

### 3.3 Docstrings

Google style, required on all public modules, classes and functions.

```python
def load_trade_date(source: Source, trade_date: date) -> IngestionResult:
    """Download one day's file for a source and store it in the raw zone.

    Args:
        source: The feed to download.
        trade_date: The trade date in Asia/Kolkata.

    Returns:
        The ingestion result, including the stored object key and checksum.

    Raises:
        SourceUnavailableError: The file could not be downloaded after retries.
    """
```

### 3.4 Errors

- All project exceptions inherit from `LineageHealError` in `lineageheal.core.errors`, grouped by concern (`IngestionError`, `LineageError`, `AgentError`, …).
- Raise specific exceptions; never `raise Exception`.
- Catch only what you can handle. Never use a bare `except:`.
- Error messages state what failed and include identifiers, never secrets.

### 3.5 Logging

```python
log = structlog.get_logger(__name__)
log.info("file_stored", source="nse", dataset="cm_bhavcopy", trade_date=str(d), key=key)
```

- Event names are `snake_case` past-tense facts (`file_stored`, `incident_opened`).
- Context goes in key-value fields, not in the message string.
- `debug` for internals, `info` for lifecycle events, `warning` for recoverable problems, `error` for failures.

### 3.6 Async

The API and database layer are async. Loaders and the CLI may be synchronous. Never call blocking I/O inside an async function without moving it to a thread.

### 3.7 Tests

- Layout mirrors `src/`: `tests/unit/ingestion/test_nse.py` tests `src/lineageheal/ingestion/nse.py`.
- Name tests as behaviour: `test_identical_file_is_not_stored_twice`.
- Arrange, act, assert, separated by blank lines.
- Markers: `unit` (default), `integration` (needs Docker services), `e2e`, `slow`, and `dbt` (the tests that run real dbt builds). Markers come from the folder a test lives in, not from decorators.
- `make test` runs the Python tests with coverage and then the `dbt` tests without it: those exercise SQL rather than our Python, and coverage tracing doubles their run time.
- dbt scenario tests share one parsed project per session (the `parsed_project` fixture in `tests/unit/dbt/conftest.py`). Each test gets an emptied raw-zone folder and its own database file, which must be named `test.duckdb`. Parsing once is what keeps about 90 builds fast.
- Fixtures in `conftest.py` at the narrowest level that needs them; sample data in `tests/fixtures/`.
- Reference requirement IDs in the docstring when a test verifies one: `"""FR-ING-05."""`

## 4. SQL and dbt

### 4.1 Model naming

| Layer | Prefix | Pattern | Example |
|---|---|---|---|
| Bronze | `brz_` | `brz_<source>__<dataset>` | `brz_nse__cm_bhavcopy` |
| Silver | `slv_` | `slv_<source>__<entity>` | `slv_amfi__nav` |
| Gold | `gld_` | `gld_<entity>` | `gld_equity_daily_prices` |
| Seeds | `seed_` | `seed_<name>` | `seed_calendar_nse` |

### 4.2 Column naming

| Kind | Convention | Example |
|---|---|---|
| All columns | `snake_case`, singular | `close_price_inr` |
| Dates | `_date` suffix | `trade_date`, `nav_date` |
| Timestamps | `_at` suffix, UTC | `ingested_at` |
| Identifiers | `_id` or the domain name | `scheme_code`, `isin` |
| Currency amounts | Currency suffix | `close_price_inr`, `turnover_inr` |
| Ratios | `_pct` (0–100) or `_ratio` (0–1) | `change_pct` |
| Booleans | `is_` or `has_` prefix | `is_adjusted` |
| Metadata | Leading underscore | `_source_file`, `_ingested_at` |

Bronze models rename source columns to these conventions. Silver and gold never reference raw column names.

### 4.3 Types

- Prices and NAVs: `DECIMAL(18, 4)`. FX rates: `DECIMAL(18, 6)`. Volumes and counts: `BIGINT`.
- Never `FLOAT` or `DOUBLE` for money or rates.

### 4.4 SQL style

Enforced by sqlfluff: lowercase keywords and functions, trailing commas, explicit `as` for aliases, four-space indentation.

Structure every model as import CTEs, then logic CTEs, then a final select:

```sql
with

bhavcopy as (
    select * from {{ ref('brz_nse__cm_bhavcopy') }}
),

equities as (
    select
        trade_date,
        isin,
        symbol,
        close_price_inr,
    from bhavcopy
    where series = 'EQ'
),

final as (
    select * from equities
)

select * from final
```

### 4.5 YAML

Each model has an entry in a `_<layer>__models.yml` file in its folder, with a model description, column descriptions, and tests. Primary keys always get `unique` and `not_null` (or a combination uniqueness test).

## 5. Prompts

- Location: `src/lineageheal/agents/prompts/`.
- File name: `<agent>.<purpose>.v<N>.md`, for example `investigator.system.v1.md`.
- Any change creates a new version file; old versions stay for reproducibility.
- Variables use Jinja syntax (`{{ incident_summary }}`) and are listed in a front-matter block.
- Untrusted data is always inserted inside clearly delimited tags, with an instruction that its contents are data, not instructions.
- Prompts never contain secrets, URLs with tokens or personal data.

## 6. TypeScript and React

| Element | Convention | Example |
|---|---|---|
| Component files | `PascalCase.tsx` | `LineageGraph.tsx` |
| Hooks | `useCamelCase.ts` | `useIncident.ts` |
| Other modules | `camelCase.ts` | `formatDate.ts` |
| Types and interfaces | `PascalCase` | `IncidentSummary` |
| Constants | `UPPER_SNAKE_CASE` | `POLL_INTERVAL_MS` |

- Organise by feature: `web/src/features/incidents/`, `web/src/features/benchmark/`; shared UI in `web/src/components/`.
- Function components only. Props typed with an explicit type, not inferred from usage.
- No `any`. No hand-written API types; import from the generated client.
- Server state in TanStack Query; local UI state in component state. No global state library unless agreed.
- Styling with Tailwind utility classes and shadcn/ui components. No inline style objects except for dynamic values.
- Accessibility: every interactive element is reachable by keyboard, has an accessible name, and meets WCAG 2.1 AA contrast.
- Tests next to components: `LineageGraph.test.tsx`.

## 7. API conventions

- Resource paths are plural nouns: `/incidents/{id}`. Actions on a resource use a verb sub-path: `/incidents/{id}/approve`.
- JSON fields in `snake_case`.
- Dates as `YYYY-MM-DD`; timestamps as ISO 8601 in UTC with `Z`.
- Errors as RFC 9457 problem details with a stable `type` URI.
- Pagination with `limit` and an opaque `cursor`; responses include `next_cursor`.
- Every endpoint declares its response model, so the OpenAPI schema is complete.

## 8. Documentation

- Markdown, sentence-case headings, one sentence per idea, active voice.
- Diagrams in Mermaid so they render on GitHub and stay diffable.
- Link to requirement IDs and backlog IDs instead of repeating content.
- Update docs in the same pull request as the code change.

### 8.1 ADR template (`docs/adr/NNNN-title.md`)

```markdown
# NNNN. <Decision title>

- **Status:** Proposed | Accepted | Superseded by NNNN
- **Date:** YYYY-MM-DD

## Context
<the problem and constraints>

## Decision
<what we chose>

## Alternatives considered
<options and why they lost>

## Consequences
<what becomes easier, harder, or must be revisited>
```

### 8.2 Runbook template (`docs/runbooks/<slug>.md`)

Runbooks are also retrieval documents for the agents, so they follow a fixed shape.

```markdown
# <Fault pattern name>

**Fault class:** <class> · **Feeds:** <feeds>

## Symptoms
<which tests or monitors fail, what the data looks like>

## Diagnosis
<steps and the evidence that confirms this cause>

## Fix
<typical change, and where it belongs (dbt or external)>

## Prevention
<tests or monitors that would catch it earlier>
```

# QueryMind CLI

**Describe what you want in plain English. Get SQL that is correct for *your actual database*, verified before you ever see it.**

QueryMind lives in your terminal and your project. It reads your real MySQL schema (tables, keys, enum values, column comments), drafts a query with an LLM, checks it with a parser and `EXPLAIN`, runs it safely, and repairs its own mistakes. You never paste a schema or hand-fix a column name.

```
$ querymind ask "which countries bring in the most revenue, and what share of the total is each?"

  > Drafted query
  > Repair 1/2: The database rejected the query at planning time: Unknown column 'oi.price'
  > Drafted query
╭──────────────────────────────── SQL ─────────────────────────────────╮
│ WITH revenue AS (SELECT c.country, SUM(oi.quantity * oi.unit_price   │
│   * (1 - oi.discount_pct / 100)) AS revenue FROM orders AS o JOIN ...│
╰──────────────────────────────────────────────────────────────────────╯
What it does: Ranks countries by net revenue and shows each one's share of the total.
Assumption: Excluded cancelled and refunded orders, per the status column comment
┏━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┓
┃ country ┃ revenue   ┃ revenue_rank ┃ pct_of_total ┃
┡━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━┩
│ US      │ 329632.82 │ 1            │ 23.3         │
│ DE      │ 302412.04 │ 2            │ 21.4         │
...
8 rows · 0.004s · gemini/gemini-2.5-flash · 2 model calls · 1 self-repair
```

## Why this exists

Browser-based text-to-SQL tools are stateless: you paste a schema, get SQL back, and hope. The usual failure is the model inventing a table or column it was never shown. QueryMind removes that failure mode:

| Typical web tool | QueryMind CLI |
|---|---|
| You paste the schema by hand | Reads the live schema, foreign keys, enums, and comments itself |
| One shot, no verification | Parses, plans (`EXPLAIN`), and executes before showing you |
| Wrong column? You fix it | The exact MySQL error goes back to the model, which repairs it |
| No idea what your business terms mean | Reads `QUERYMIND.md` ("revenue = net of discounts, excl. cancelled") |
| Locked to one AI vendor | Any OpenAI-compatible API; free tiers work; automatic fallback |

No model training required: it works with hosted LLMs, including free tiers, or a local model via Ollama.

## Quick start (about 3 minutes)

```bash
git clone https://github.com/<you>/querymind-cli && cd querymind-cli
pip install -e .

# 1. Demo database (needs Docker). Or point QueryMind at your own MySQL.
docker compose up -d
export QUERYMIND_DB_URL=mysql://qm_readonly:qm_readonly_pw@127.0.0.1:3307/shop

# 2. A free LLM key: https://aistudio.google.com  (Gemini), or console.groq.com (Groq)
querymind keys set gemini          # prompts with hidden input

# 3. Ask
querymind ask "who are our 5 best customers by net spend this year?"
querymind chat                     # interactive, supports follow-ups
```

For your own project, run `querymind init` inside it: it detects connection settings from your `.env` or `docker-compose.yml`, creates a `QUERYMIND.md` for business rules, and writes a config file that contains **no secrets**.

## Swapping API keys and providers

This is designed so keys never end up in git and switching is one command.

**Where keys live.** Looked up in this order, first match wins:

1. the shell environment (`export GEMINI_API_KEY=...`)
2. `./.env` in your project (git-ignored; see `.env.example`)
3. `~/.config/querymind/credentials.env`, mode `600`, **outside any repository**

```bash
querymind keys set gemini        # store or replace a key (hidden prompt)
querymind keys list              # where each key comes from, values masked
querymind keys remove gemini
querymind keys set QUERYMIND_DB_PASSWORD   # works for any ENV_VAR_NAME too
```

**Where config lives.** Config files only name the *environment variable* holding a key, never the key itself, so `.querymind/config.toml` is safe to commit. Precedence: defaults < `~/.config/querymind/config.toml` < `.querymind/config.toml` < env vars < CLI flags.

**Swapping providers**

```bash
querymind provider list                      # status, model, fallback order
querymind provider use groq                  # try Groq first; others become fallbacks
querymind provider models gemini --grep flash   # live model ids for your key
querymind provider set-model gemini <id>
querymind provider test                      # verify keys + latency
querymind ask "..." -p groq -m <model>       # one-off, no fallback
```

**Any OpenAI-compatible endpoint** (OpenRouter, a company gateway, vLLM, LM Studio...):

```bash
querymind provider add mygateway --base-url https://llm.example.com/v1 --model my-model
querymind keys set mygateway
```

**Automatic fallback.** The default chain is `gemini -> groq -> ollama`. Providers without a key are skipped. If one hits a rate limit, a bad key, or an outage, QueryMind moves to the next, so free-tier limits do not surface as failures. Built-in presets: Gemini, Groq, OpenRouter, Mistral, Ollama.

> **Heads-up:** free-tier limits and model ids change often. Defaults in `config.py` are starting points; use `provider models` to see what your key can access. Check each provider's terms, including how free-tier data may be used.

## How it works

```
question -> read schema -> draft SQL -> validate (sqlglot) -> EXPLAIN -> execute (read-only)
                                ^                                           |
                                +------------- self-repair on error --------+
```

- Small and medium schemas go to the model in one compact prompt, so most questions take 1-2 model calls (kind to rate limits). Large schemas get an overview plus a `describe_tables` step.
- The model replies in single JSON actions (`describe_tables`, `clarify`, `final`, ...), which works on every provider without depending on native tool-calling support.
- Genuinely ambiguous requests ("top customers") produce a short clarifying question with options instead of a guess.
- Empty results on filtered queries trigger one double-check of filter values before accepting.

## Safety model

Model output is treated as untrusted input, with independent layers:

1. **Parse, don't pattern-match.** sqlglot must parse exactly one statement whose root is a query. Anything else (including `INTO OUTFILE`, `LOAD DATA`, `CALL`, `SHOW`) is rejected.
2. **No writes anywhere in the tree**, so a `DELETE` hidden inside a CTE is caught. Dangerous functions (`LOAD_FILE`, `SLEEP`, `BENCHMARK`, `GET_LOCK`...) are blocked.
3. **Tables must exist; cross-database access is refused.**
4. **We execute regenerated SQL, never the model's raw text**, with comments stripped, so `/*!50000 ... */` tricks never reach the server.
5. **Row cap** injected or clamped; results fetched with a hard limit.
6. **Database-level defense:** read-only session, server-side execution timeout, and (recommended) a `SELECT`-only user. See `demo/02_readonly_user.sql`.

Phase 0 is **read-only by design**. Use a least-privilege database user regardless.

### Privacy

By default QueryMind sends the model **schema only: no row values**. Opt in with `--allow-samples` (or `privacy = "samples"`) to let it inspect distinct values and sample rows, with emails and phone numbers masked. For fully local operation use Ollama. Keep in mind that your schema, question text, and `QUERYMIND.md` are sent to whichever provider you choose.

## Commands

| Command | What it does |
|---|---|
| `ask "..."` | One question. `--sql-only`, `--json`, `--csv out.csv`, `-p/-m`, `--allow-samples`, `--max-rows` |
| `chat` | Interactive session with follow-ups, `/save`, and `/export` |
| `run "SELECT ..."` | Your own SQL through the same safety pipeline (no LLM) |
| `schema [table]` | What QueryMind sees in your database |
| `explain <query.sql>` | Analyze query execution plan, warnings, index suggestions, and plain-English intent |
| `modify <query.sql> "..."`| Modify an existing SQL query using natural language, verified by database |
| `write "INSERT/UPDATE..."`| Approval-gated writes with transactional dry-run and row preview |
| `export [options]` | Export a verified query as a .sql file, Python snippet, or JS snippet |
| `remember "<rule>"` | Add a business rule or term definition to QUERYMIND.md |
| `history` | View recent query session history |
| `save <name> "<sql>"` | Save a query under a memorable alias |
| `saved` | List all saved queries |
| `mcp` | Run as an MCP stdio server for Claude Code and other agents |
| `init` | Set up the current project |
| `doctor` | Diagnose config, keys, git hygiene (`.env` not ignored?), connectivity |
| `provider ...`, `keys ...` | Manage providers and keys |

## Development

```bash
pip install -e ".[dev]"
pytest                 # 131 unit tests, no DB or API keys needed
docker compose up -d
QUERYMIND_TEST_DB_URL=mysql://qm_readonly:qm_readonly_pw@127.0.0.1:3307/shop pytest   # + live DB tests
ruff check src tests
```

Project layout:

```
src/querymind/
  cli.py            Typer commands
  config.py         layered config + provider presets
  secrets.py        key lookup/storage (env -> .env -> credentials.env)
  agent/            the loop, prompts, JSON action protocol
  llm/              OpenAI-compatible client + fallback router
  adapters/         DatabaseAdapter interface + MySQL/MariaDB implementation
  safety/           sqlglot validator, masking
  workspace.py      QUERYMIND.md and .env/docker-compose discovery
```

### Adding a database

The agent only talks to `DatabaseAdapter` (`adapters/base.py`): `introspect`, `explain`, `execute_readonly`, `sample_rows`, `distinct_values`, `dialect_hints`. A PostgreSQL adapter is one new file plus a dialect name for the validator. No agent changes.

## Roadmap

- [x] Phase 0: MySQL/MariaDB, provider router, safety layer, self-repair, project notes
- [x] Benchmark suite with execution-accuracy scoring (easy/medium/hard tiering, 45 questions)
- [x] Learn from corrections: store feedback queries, retrieve similar ones as few-shot examples
- [x] Workspace scanning: migrations and ORM models (Prisma, SQLAlchemy, Django)
- [x] Approval-gated writes with transaction dry-runs and interactive commit confirmation
- [x] PostgreSQL and SQLite adapters
- [x] MCP server mode so other coding agents can call QueryMind
- [ ] Phase 3 final: MongoDB adapter (aggregation pipelines)

## Limitations

- Results can be plausible but wrong. Always read the SQL and explanation; ambiguous business terms belong in `QUERYMIND.md`.
- Free-tier models are weaker than frontier models on the hardest queries; expect occasional repair rounds.
- Developed and tested against MariaDB 10.11 (MySQL wire-compatible). The included CI workflow targets MySQL 8.4; MySQL-specific paths such as `MAX_EXECUTION_TIME` are worth confirming on your own server with `querymind doctor` and the integration tests.

## License

MIT

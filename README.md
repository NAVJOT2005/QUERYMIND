# QueryMind CLI

**Workspace-aware natural-language-to-SQL for your terminal. It reads your real schema, verifies every query safely, and works with free LLM APIs.**

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Tests](https://img.shields.io/badge/tests-131%20passing-brightgreen)
![Databases](https://img.shields.io/badge/databases-MySQL%20%7C%20PostgreSQL%20%7C%20SQLite-informational)
![LLMs](https://img.shields.io/badge/LLMs-Gemini%20%7C%20Groq%20%7C%20Ollama-orange)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

```bash
querymind ask "top 5 customers by revenue in 2024"
```

The shorter alias `qm` works for every command, for example `qm ask "..."`.

---

## Table of Contents

- [Why QueryMind](#why-querymind)
- [Key Features](#key-features)
- [How It Works](#how-it-works)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Command Reference](#command-reference)
- [Configuration](#configuration)
- [LLM Providers](#llm-providers)
- [Safety, Privacy and Write Protections](#safety-privacy-and-write-protections)
- [Workspace Awareness and QUERYMIND.md](#workspace-awareness-and-querymindmd)
- [Use as an MCP Server](#use-as-an-mcp-server)
- [Benchmarks](#benchmarks)
- [Project Structure](#project-structure)
- [Development and Testing](#development-and-testing)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

---

## Why QueryMind

Most browser-based text-to-SQL tools are stateless, one-shot prompt forms. You paste your `CREATE TABLE` statements, get a query back, and when the model hallucinates a column or breaks a join, you are left debugging SQL by hand.

QueryMind fixes the root cause by living inside your project terminal as an autonomous agent. It does not guess. It inspects your live schema, reads your project files, tests its own SQL, and repairs its own mistakes before showing you anything.

| Typical text-to-SQL tool | QueryMind CLI |
|---|---|
| You paste the schema manually | Introspects the live database (keys, enums, comments, relationships) |
| Knows nothing about your project | Scans `.env`, `docker-compose.yml`, Prisma, SQLAlchemy, Django models, and `.sql` files |
| Returns SQL and hopes it works | Parses, runs EXPLAIN, executes read-only, and self-repairs (up to 3 rounds) |
| No safety net | Zero-trust: blocks dangerous statements, masks PII, clamps result limits |
| Forgets everything | Remembers business definitions and learns from feedback |

---

## Key Features

- **Real schema grounding:** live introspection of tables, columns, primary and foreign keys, enums, and comments.
- **Workspace awareness:** auto-detects database configuration and understands your ORM models.
- **Verify and self-repair loop:** `sqlglot` AST parsing, `EXPLAIN` checks, full-table-scan detection, read-only execution, and error feedback to the LLM.
- **Interactive chat REPL:** multi-turn conversations with follow-up questions.
- **Approval-gated writes:** dry-run, before and after previews, and rollback by default.
- **Index advisor:** turns `EXPLAIN` warnings into concrete `CREATE INDEX` suggestions.
- **Business memory:** teach it terms like "active user" through `QUERYMIND.md`.
- **Learns from feedback:** similar past questions are injected as few-shot examples.
- **Export:** output queries as `.sql`, Python, or JavaScript snippets.
- **History and bookmarks:** revisit and save queries under aliases.
- **MCP server mode:** use QueryMind as a tool from Claude Code and other agents.
- **Multiple LLM providers:** Gemini, Groq, or fully offline with Ollama.
- **Multiple databases:** MySQL and MariaDB, PostgreSQL, and SQLite.

---

## How It Works

```mermaid
flowchart LR
    A[Your question] --> B[Explore<br/>schema and workspace]
    B --> C[Draft SQL]
    C --> D{Verify}
    D -->|AST parse, EXPLAIN,<br/>scan check, read-only run| E{Passed?}
    E -->|Yes| F[Results and SQL]
    E -->|No, up to 3 rounds| G[Self-Repair<br/>error fed to LLM]
    G --> C
```

1. **Explore:** the agent calls schema tools (`list_tables`, `describe_table`, and others) through a JSON action protocol and learns only what it needs.
2. **Draft:** the LLM writes SQL using real schema context, your `QUERYMIND.md` rules, and similar past questions.
3. **Verify:** the query is parsed into an AST, checked by the safety validator, analyzed with `EXPLAIN` (including full-table-scan detection), and executed in a read-only transaction.
4. **Self-repair:** any syntax or runtime error is fed back to the LLM for up to 3 repair rounds.
5. **Deliver:** you get the verified SQL, the results, and optional index advice.

---

## Installation

**Requirements:** Python 3.11 or newer, and access to a MySQL/MariaDB, PostgreSQL, or SQLite database.

From the root of the repository, create a virtual environment and install:

```bash
python -m venv venv

# Linux / macOS
source venv/bin/activate

# Windows (PowerShell)
venv\Scripts\activate

pip install -e .
```

For development tools (pytest and ruff), install the extras:

```bash
pip install -e ".[dev]"
```

Verify the installation:

```bash
querymind --help
```

### Database drivers

MySQL and MariaDB support (`pymysql`) and SQLite support (built into Python) work out of the box. To use PostgreSQL, also install a PostgreSQL driver:

```bash
pip install "psycopg[binary]"
```

(`pg8000` is also supported as a pure-Python alternative.)

---

## Quick Start

```bash
# 1. Auto-detect your database configuration from the current project
querymind init

# 2. Store an LLM API key safely (outside your repository)
querymind keys set gemini

# 3. Check connectivity, credentials, and git hygiene
querymind doctor

# 4. Ask a question
querymind ask "how many orders were placed last month?"

# 5. Or start an interactive session
querymind chat
```

No API key? Run fully offline with a local model:

```bash
querymind provider use ollama
```

---

## Command Reference

| Command | Purpose | Example |
|---|---|---|
| `querymind ask` | Plain English to verified SQL with results | `querymind ask "top 5 customers by revenue in 2024"` |
| `querymind chat` | Interactive multi-turn REPL with context | `querymind chat` (supports `/save`, `/export`, and follow-ups) |
| `querymind run` | Run your own SQL through the safety pipeline | `querymind run "SELECT * FROM customers"` |
| `querymind schema` | Inspect database tables, columns, and keys | `querymind schema customers` |
| `querymind explain` | Analyze query execution plans and index advice | `querymind explain query.sql` |
| `querymind modify` | Natural-language edits to an existing `.sql` file | `querymind modify report.sql "filter only paid orders"` |
| `querymind write` | Approval-gated writes with dry-run previews | `querymind write "UPDATE users SET status='active' WHERE id=1"` |
| `querymind export` | Export a query to `.sql`, Python, or JavaScript | `querymind export --format python -o script.py` |
| `querymind remember` | Save business logic rules to `QUERYMIND.md` | `querymind remember "active user = logged in last 30 days"` |
| `querymind history` | View recent questions and executed queries | `querymind history --limit 10` |
| `querymind save` | Bookmark a query under an alias | `querymind save monthly_rev "SELECT ..."` |
| `querymind saved` | List all saved query bookmarks | `querymind saved` |
| `querymind mcp` | Run as an MCP server for Claude Code and agents | `querymind mcp` |
| `querymind init` | Auto-detect database config from the project and set up | `querymind init` |
| `querymind doctor` | Check connectivity, credentials, and git hygiene | `querymind doctor` |
| `querymind provider` | Manage LLM providers (Gemini, Groq, Ollama) | `querymind provider use groq` |
| `querymind keys` | Store API keys safely outside the repository | `querymind keys set gemini` |

Run `querymind <command> --help` to see every option. Every command is also available through the `qm` alias.

---

## Configuration

Configuration is hierarchical. Later sources override earlier ones:

1. Built-in defaults
2. TOML config file
3. Environment variables
4. CLI flags

Credentials are never stored inside your project. They live in this file, with permissions set to `600`:

```
~/.config/querymind/credentials.env
```

Run `querymind init` to auto-detect your database settings from `.env`, `docker-compose.yml`, or your ORM configuration. Run `querymind doctor` at any time to confirm that your database connection and credentials are working.

---

## LLM Providers

QueryMind talks to LLMs through an OpenAI-compatible client, which lets it work with free-tier APIs as well as local models.

| Provider | Type | Setup |
|---|---|---|
| Gemini | Cloud | `querymind keys set gemini` |
| Groq | Cloud | `querymind keys set groq` |
| Ollama | Local, fully offline | Install Ollama, then `querymind provider use ollama` |

Switch providers at any time:

```bash
querymind provider use groq
```

The built-in LLM router supports fallbacks between providers.

---

## Safety, Privacy and Write Protections

QueryMind is built on a zero-trust model.

### Read-only by default

- Read queries run in a read-only transaction (`SET SESSION TRANSACTION READ ONLY` on MySQL, or the equivalent on other engines).
- Strict server-side timeouts (such as `MAX_EXECUTION_TIME`) and clamped result limits are enforced.

### AST validation

- Queries are parsed with `sqlglot` and must contain exactly one statement.
- Dangerous operations are unconditionally blocked: `DROP`, `ALTER`, `TRUNCATE`, `CALL`, `INTO OUTFILE`, `LOAD_FILE`, and `SLEEP`.

### Approval-gated writes

`querymind write` is the only path that can change data, and it is gated:

- Requires an explicit `--yes` flag or an interactive `[y/N]` confirmation.
- Runs inside a transaction with a dry-run that counts affected rows and shows before and after previews.
- Executes `ROLLBACK` unless you explicitly confirm `COMMIT`.
- Blocks any `UPDATE` or `DELETE` that has no `WHERE` clause.

### Data privacy tiers

| Tier | What the LLM sees |
|---|---|
| Schema-only (default) | Schema metadata only, never live row data |
| Sample mode (opt-in) | Small row samples, with automatic masking of emails, phone numbers, and sensitive identifiers |
| Local mode | Everything stays on your machine via Ollama |

### Secret hygiene

- API keys and database credentials are stored outside any git repository.
- `querymind doctor` flags credential and git hygiene problems.

> **Recommendation:** QueryMind adds several safety layers, but you should still connect with a least-privilege (ideally read-only) database user and test write operations on non-production data first.

---

## Workspace Awareness and QUERYMIND.md

QueryMind scans your project to understand your data model:

- `.env` and `docker-compose.yml` for connection settings
- Prisma schemas
- SQLAlchemy models
- Django models
- Existing `.sql` files
- `QUERYMIND.md` for business definitions

Teach it your vocabulary:

```bash
querymind remember "active user = logged in within the last 30 days"
querymind remember "revenue = sum of paid orders, excluding refunds"
```

These rules are saved to `QUERYMIND.md` and applied to future questions. Commit this file so your whole team shares the same definitions.

---

## Use as an MCP Server

QueryMind can run as a [Model Context Protocol](https://modelcontextprotocol.io) stdio server, so agents such as Claude Code can query your database through the same safety pipeline.

```bash
querymind mcp
```

Example client configuration:

```json
{
  "mcpServers": {
    "querymind": {
      "command": "querymind",
      "args": ["mcp"]
    }
  }
}
```

---

## Benchmarks

A built-in benchmark suite measures end-to-end quality.

**Dataset:** `bench/questions.json` contains 45 questions across three tiers.

| Tier | Count | Covers |
|---|---|---|
| Easy | 15 | Single-table filters, aggregations, orderings, distinct counts |
| Medium | 15 | Multi-table joins, subqueries, `GROUP BY` with `HAVING`, date arithmetic |
| Hard | 15 | CTEs, window functions (`ROW_NUMBER()`, `DENSE_RANK()`), churn, year-over-year revenue, running totals |

**Runner:** `bench/runner.py` reports execution accuracy, repair attempts required, median query latency, and estimated token cost, shown as Rich console tables plus a Markdown summary.

```bash
python bench/runner.py
```

---

## Project Structure

```
querymind-cli/
├── pyproject.toml
├── README.md
├── src/querymind/
│   ├── cli.py                 # Typer CLI (17 commands + Rich REPL)
│   ├── config.py              # Hierarchical config (TOML, env, flags)
│   ├── secrets.py             # Out-of-repo credential management
│   ├── export.py              # Exporters (.sql, Python, JavaScript)
│   ├── history.py             # Session history and saved queries
│   ├── mcp_server.py          # MCP stdio server
│   ├── workspace_scanner.py   # Prisma / SQLAlchemy / Django parsers
│   ├── agent/
│   │   ├── agent.py           # Explore -> Draft -> Verify -> Self-Repair loop
│   │   ├── prompts.py         # Prompt templates and few-shot injector
│   │   └── protocol.py        # JSON action protocol
│   ├── adapters/
│   │   ├── base.py            # DatabaseAdapter ABC, Schema, Table, Column, PlanInfo
│   │   ├── mysql.py           # MySQL / MariaDB
│   │   ├── postgres.py        # PostgreSQL
│   │   └── sqlite.py          # SQLite
│   ├── safety/
│   │   ├── validator.py       # AST parsing, read-only gating, PII masking
│   │   ├── index_advisor.py   # EXPLAIN warnings -> CREATE INDEX suggestions
│   │   └── writer.py          # Dry-run and approval-gated writes
│   └── learning/
│       ├── similarity.py      # Jaccard n-gram similarity
│       └── feedback.py        # FeedbackStore (.querymind/feedback.jsonl)
├── bench/
│   ├── questions.json         # 45 tiered evaluation questions
│   └── runner.py              # Benchmark runner
└── tests/                     # 131 automated unit tests
```

The codebase is decoupled through the `DatabaseAdapter` abstract base class, so new database engines can be added without touching the agent.

---

## Development and Testing

Install the development extras and run the test suite:

```bash
pip install -e ".[dev]"
pytest -q
```

Expected result: 131 passed, 9 skipped.

The 9 skipped tests are live integration tests marked `integration`. They need a running MySQL or MariaDB server and are skipped unless you point them at one by setting the `QUERYMIND_TEST_DB_URL` environment variable:

```bash
# Linux / macOS
export QUERYMIND_TEST_DB_URL="mysql://user:password@localhost:3306/testdb"

# Windows (PowerShell)
$env:QUERYMIND_TEST_DB_URL = "mysql://user:password@localhost:3306/testdb"

pytest -q
```

Use a disposable test database, never one that holds real data.

Linting is handled by ruff:

```bash
ruff check .
```

**Test coverage includes:** LLM router fallbacks, protocol actions, ORM parsing (Prisma, SQLAlchemy, Django), feedback store similarity, index advisor, transactional writer, SQLite adapter lifecycle, export utilities, and AST safety validators.

---

## Roadmap

- [x] **Phase 0, MVP:** schema-grounded `ask` with verify loop
- [x] **Phase 1, Product quality:** chat REPL, history, config, doctor, workspace scanner
- [x] **Phase 2, Writes and polish:** approval-gated writes, index advisor, exporters, MCP server
- [x] **Phase 3, Relational expansion:** PostgreSQL and SQLite adapters
- [ ] **Next, MongoDB:** deferred because document aggregation needs a distinct query pipeline (the `DatabaseAdapter` design already accommodates it)

---

## Contributing

Contributions are welcome.

1. Fork the repository.
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Add tests for your change.
4. Make sure `pytest -q` and `ruff check .` pass.
5. Open a pull request describing what you changed and why.

Please never commit API keys, database credentials, or real data.

---

## License

Distributed under the MIT License. See the [LICENSE](LICENSE) file for details.

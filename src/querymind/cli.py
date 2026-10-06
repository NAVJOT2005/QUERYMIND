"""QueryMind command line interface."""

from __future__ import annotations

import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import typer
from rich.table import Table as RichTable

from querymind import __version__, secrets, workspace
from querymind.adapters.mysql import MySQLAdapter
from querymind.agent.agent import Agent, AgentResult
from querymind.config import (
    Settings,
    load_settings,
    update_toml,
    usable_chain,
)
from querymind.errors import QueryMindError
from querymind.export import (
    export_sql_file,
    generate_javascript_snippet,
    generate_python_snippet,
)
from querymind.history import (
    delete_saved_query,
    get_saved_query,
    list_saved_queries,
    load_history,
    record_history,
    save_query,
)
from querymind.llm import build_router, openai_compat
from querymind.mcp_server import run_mcp_server
from querymind.paths import (
    credentials_path,
    find_project_root,
    history_path,
    project_config_path,
    user_config_path,
)
from querymind.render import (
    err,
    out,
    print_agent_result,
    print_schema,
    print_sql,
    print_table,
    write_csv,
)
from querymind.safety import validate
from querymind.safety.writer import execute_write_transaction, validate_write


app = typer.Typer(
    help="Ask your database questions in plain English, from inside your project.",
    no_args_is_help=True, add_completion=False, pretty_exceptions_enable=False,
)
provider_app = typer.Typer(help="Manage and swap LLM providers.", no_args_is_help=True)
keys_app = typer.Typer(help="Store and swap API keys (kept outside your repo).", no_args_is_help=True)
app.add_typer(provider_app, name="provider")
app.add_typer(keys_app, name="keys")

_VERBOSE = False


def _version(value: bool):
    if value:
        typer.echo(f"querymind {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True,
                                 help="Show version and exit."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show tracebacks and extra detail."),
):
    global _VERBOSE
    _VERBOSE = verbose


def _fail(exc: Exception) -> typer.Exit:
    if _VERBOSE:
        import traceback
        traceback.print_exc()
    err.print(f"[red]Error:[/] {exc}")
    return typer.Exit(1)


# ---------------------------------------------------------------------------
# session plumbing
# ---------------------------------------------------------------------------
@contextmanager
def _session(settings: Settings, status=None, on_clarify=None):
    """Connect the DB, build the LLM router, and yield a ready Agent."""
    adapter = MySQLAdapter(settings.db_config(), timeout_s=int(settings.db["timeout_s"]))
    adapter.connect()
    try:
        def on_event(msg: str) -> None:
            if msg.startswith(("Repair", "Query returned", "Drafted")) or "failed" in msg:
                err.print(f"[dim yellow]  > {msg}[/]")
            elif status is not None:
                status.update(f"[cyan]{msg}[/]")

        router = build_router(settings, on_event)
        agent = Agent(
            adapter, router, settings.agent, int(settings.db["max_rows"]),
            notes=workspace.load_notes(settings.root), on_event=on_event, on_clarify=on_clarify,
        )
        yield agent
    finally:
        adapter.close()


def _ask_clarify(status):
    def handler(question: str, options: list[str]) -> str | None:
        if not sys.stdin.isatty():
            return None
        if status is not None:
            status.stop()
        err.print(f"\n[bold yellow]I need one detail:[/] {question}")
        for i, opt in enumerate(options, 1):
            err.print(f"  [cyan]{i}[/]) {opt}")
        raw = typer.prompt("Pick a number or type your own answer", default="", show_default=False)
        if status is not None:
            status.start()
        raw = raw.strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        return raw or None
    return handler


# ---------------------------------------------------------------------------
# core commands
# ---------------------------------------------------------------------------
@app.command()
def ask(
    question: str = typer.Argument(..., help="What you want, in plain English."),
    provider: str = typer.Option(None, "--provider", "-p", help="Use only this provider (no fallback)."),
    model: str = typer.Option(None, "--model", "-m", help="Override the model for --provider."),
    samples: bool = typer.Option(False, "--allow-samples",
                                 help="Let the model see masked sample values (default: schema only)."),
    max_rows: int = typer.Option(None, "--max-rows", help="Row cap for this query."),
    csv_path: Path = typer.Option(None, "--csv", help="Also write the full result to a CSV file."),
    sql_only: bool = typer.Option(False, "--sql-only", help="Print only the SQL (still verified)."),
    as_json: bool = typer.Option(False, "--json", help="Print machine-readable JSON."),
):
    """Turn a plain-English request into verified SQL and run it safely."""
    try:
        settings = load_settings(provider=provider, model=model,
                                 privacy="samples" if samples else None, max_rows=max_rows)
        with err.status("[cyan]Reading your schema...[/]") as status:
            with _session(settings, status, _ask_clarify(status)) as agent:
                result = agent.ask(question)
    except QueryMindError as exc:
        raise _fail(exc)

    if result.status == "clarify":
        q, opts = result.clarification
        err.print(f"[yellow]Needs clarification:[/] {q}")
        for o in opts:
            err.print(f"  - {o}")
        err.print("[dim]Re-run with a more specific request.[/]")
        raise typer.Exit(2)
    if result.status != "ok":
        print_agent_result(result)
        raise typer.Exit(1)

    record_history(
        question=question,
        sql=result.sql,
        status=result.status,
        database=str(settings.db.get("name", "")),
        elapsed_s=result.result.elapsed_s if result.result else 0.0,
    )

    if as_json:
        out.print_json(json.dumps(_as_dict(result), default=str))
    elif sql_only:
        sys.stdout.write(result.sql + "\n")
    else:
        print_agent_result(result)
        if result.index_suggestions:
            out.print("\n[bold cyan]Index recommendations:[/] Consider adding:")
            for idx in result.index_suggestions:
                out.print(f"  [green]{idx}[/]")
    if csv_path and result.result:
        write_csv(csv_path, result.result)
        err.print(f"[green]Saved {len(result.result.rows)} rows to {csv_path}[/]")



def _as_dict(r: AgentResult) -> dict:
    return {
        "sql": r.sql, "explanation": r.explanation, "assumptions": r.assumptions,
        "columns": r.result.columns if r.result else [], "rows": r.result.rows if r.result else [],
        "warnings": r.plan_warnings, "provider": r.provider, "model": r.model,
        "model_calls": r.model_calls, "repairs": r.repairs,
    }


@app.command()
def run(sql: str = typer.Argument(..., help="SQL you wrote yourself.")):
    """Run your own SELECT through the same safety + row-limit pipeline (no LLM involved)."""
    try:
        settings = load_settings()
        adapter = MySQLAdapter(settings.db_config(), timeout_s=int(settings.db["timeout_s"]))
        with adapter:
            schema = adapter.introspect()
            checked = validate(sql, set(schema.table_names), schema.database, adapter.dialect,
                               int(settings.db["max_rows"]))
            plan = adapter.explain(checked.sql)
            if not plan.ok:
                raise typer.BadParameter(plan.error)
            res = adapter.execute_readonly(checked.sql, int(settings.db["max_rows"]))
    except (QueryMindError, typer.BadParameter) as exc:
        raise _fail(exc)
    print_sql(checked.sql)
    print_table(res)
    for w in plan.warnings:
        out.print(f"[yellow]Performance:[/] {w}")
    out.print(f"[dim]{len(res.rows)} rows · {res.elapsed_s:.3f}s[/]")


@app.command()
def schema(table: str = typer.Argument(None, help="Show full detail for one table.")):
    """Show what QueryMind sees in your database."""
    try:
        settings = load_settings()
        with MySQLAdapter(settings.db_config()) as adapter:
            print_schema(adapter.introspect(), table)
    except QueryMindError as exc:
        raise _fail(exc)


@app.command()
def chat(
    provider: str = typer.Option(None, "--provider", "-p"),
    model: str = typer.Option(None, "--model", "-m"),
    samples: bool = typer.Option(False, "--allow-samples"),
):
    """Interactive session with follow-ups ("now only 2024", "break that down by country")."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory

    try:
        settings = load_settings(provider=provider, model=model,
                                 privacy="samples" if samples else None)
        history_path().parent.mkdir(parents=True, exist_ok=True)
        prompt = PromptSession(history=FileHistory(str(history_path())))
        turns: list[tuple[str, str]] = []
        last: AgentResult | None = None
        with _session(settings, None, _ask_clarify(None)) as agent:
            names = ", ".join(agent.schema.table_names[:8])
            out.print(f"[bold]QueryMind[/] connected to [cyan]{agent.schema.database}[/] "
                      f"({agent.adapter.server_info()}). Tables: {names}")
            out.print("[dim]Ask anything. /sql last query, /schema, /refresh, /help, /exit[/]")
            while True:
                try:
                    line = prompt.prompt("qm> ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not line:
                    continue
                if line in ("/exit", "/quit"):
                    break
                if line == "/help":
                    out.print("/sql  /save <name>  /export <file.sql>  /schema  /refresh  /exit  (anything else is a question)")
                    continue
                if line == "/schema":
                    print_schema(agent.schema)
                    continue
                if line == "/refresh":
                    agent.refresh_schema()
                    out.print("[green]Schema re-read.[/]")
                    continue
                if line == "/sql":
                    print_sql(last.sql) if last and last.sql else out.print("[dim]Nothing yet.[/]")
                    continue
                if line.startswith("/save"):
                    parts = line.split(maxsplit=1)
                    if len(parts) > 1 and last and last.sql:
                        save_query(parts[1].strip(), last.sql, description=turns[-1][0] if turns else "")
                        out.print(f"[green]Saved query as '{parts[1].strip()}'.[/]")
                    elif not last or not last.sql:
                        out.print("[dim]No query to save yet.[/]")
                    else:
                        out.print("[yellow]Usage: /save <name>[/]")
                    continue
                if line.startswith("/export"):
                    parts = line.split(maxsplit=1)
                    if len(parts) > 1 and last and last.sql:
                        export_sql_file(Path(parts[1].strip()), last.sql, question=turns[-1][0] if turns else "", explanation=last.explanation)
                        out.print(f"[green]Exported to {parts[1].strip()}[/]")
                    elif not last or not last.sql:
                        out.print("[dim]No query to export yet.[/]")
                    else:
                        out.print("[yellow]Usage: /export <file.sql>[/]")
                    continue

                try:
                    with err.status("[cyan]Thinking...[/]") as status:
                        agent.on_event = lambda m, s=status: s.update(f"[cyan]{m}[/]")
                        res = agent.ask(line, turns)
                except QueryMindError as exc:
                    err.print(f"[red]Error:[/] {exc}")
                    continue
                if res.status == "clarify":
                    err.print(f"[yellow]{res.clarification[0]}[/] [dim](rephrase and ask again)[/]")
                    continue
                print_agent_result(res)
                if res.status == "ok":
                    last = res
                    turns.append((line, res.sql))
    except QueryMindError as exc:
        raise _fail(exc)


# ---------------------------------------------------------------------------
# init / doctor
# ---------------------------------------------------------------------------
@app.command()
def init():
    """Set up QueryMind for the current project (no secrets are written to the repo)."""
    root = find_project_root()
    out.print(f"[bold]Project root:[/] {root}")
    hints = workspace.discover_db(root)
    host, port, user, name = "127.0.0.1", 3306, "", ""
    if hints:
        h = hints[0]
        out.print(f"Found a database in [cyan]{h.source}[/]: {h.user}@{h.host}:{h.port}/{h.name}")
        if typer.confirm("Use it?", default=True):
            host, port, user, name = h.host, h.port, h.user, h.name
    host = typer.prompt("MySQL host", default=host)
    port = int(typer.prompt("Port", default=port))
    user = typer.prompt("User (a READ-ONLY user is strongly recommended)", default=user or None)
    name = typer.prompt("Database name", default=name or None)

    password = typer.prompt("Password (stored outside the repo; blank for none)",
                            default="", hide_input=True, show_default=False)
    if password:
        secrets.set_secret("QUERYMIND_DB_PASSWORD", password)

    cfg_path = project_config_path(root)
    update_toml(cfg_path, lambda d: d.setdefault("database", {}).update(
        {"host": host, "port": port, "user": user, "name": name,
         "password_env": "QUERYMIND_DB_PASSWORD"}))
    out.print(f"[green]Wrote[/] {cfg_path} [dim](contains no secrets; safe to commit)[/]")

    notes = root / "QUERYMIND.md"
    if not notes.exists():
        notes.write_text(workspace.NOTES_TEMPLATE)
        out.print(f"[green]Created[/] {notes.name}: describe your business rules there.")

    try:
        settings = load_settings()
        with MySQLAdapter(settings.db_config()) as adapter:
            n = len(adapter.introspect().tables)
            out.print(f"[green]Connected:[/] {adapter.server_info()}, {n} tables found.")
    except QueryMindError as exc:
        err.print(f"[yellow]Could not connect yet:[/] {exc}")
        settings = load_settings()

    if not usable_chain(settings):
        out.print("\n[bold]No LLM provider is ready.[/] Gemini has a free tier (aistudio.google.com).")
        if typer.confirm("Paste a Gemini API key now?", default=False):
            key = typer.prompt("GEMINI_API_KEY", hide_input=True)
            secrets.set_secret("GEMINI_API_KEY", key)
            out.print(f"[green]Saved[/] to {credentials_path()}")
        else:
            out.print("Later: [cyan]querymind keys set gemini[/]  (or groq, openrouter, ...)")

    if workspace.gitignore_covers_env(root) is False:
        out.print("[bold yellow]Warning:[/] your .env is not in .gitignore. Add a line `.env` "
                  "before committing.")
    out.print("\nTry: [cyan]querymind ask \"how many rows are in each table?\"[/]")


@app.command()
def doctor():
    """Diagnose configuration, keys, git hygiene, and connectivity."""
    ok = lambda m: out.print(f"[green]ok[/]   {m}")
    bad = lambda m: out.print(f"[red]fail[/] {m}")
    warn = lambda m: out.print(f"[yellow]warn[/] {m}")
    try:
        settings = load_settings()
    except QueryMindError as exc:
        bad(f"config: {exc}")
        raise typer.Exit(1)
    root = settings.root
    ok(f"project root: {root}")
    for label, path in (("user config", user_config_path()), ("project config", project_config_path(root))):
        (ok if path.is_file() else warn)(f"{label}: {path}{'' if path.is_file() else ' (not created)'}")
    ok("QUERYMIND.md found") if workspace.load_notes(root) else warn("no QUERYMIND.md (optional)")
    if workspace.gitignore_covers_env(root) is False:
        bad(".env exists but is NOT git-ignored. Add `.env` to .gitignore.")
    creds = credentials_path()
    if creds.is_file() and (creds.stat().st_mode & 0o077):
        warn(f"{creds} is readable by other users; run: chmod 600 {creds}")
    usable = usable_chain(settings)
    (ok if usable else bad)(f"usable providers in chain: {', '.join(p.name for p in usable) or 'none'}")
    try:
        with MySQLAdapter(settings.db_config(), timeout_s=int(settings.db["timeout_s"])) as adapter:
            ok(f"database: {adapter.server_info()}, {len(adapter.introspect().tables)} tables")
    except QueryMindError as exc:
        bad(f"database: {exc}")


# ---------------------------------------------------------------------------
# provider management
# ---------------------------------------------------------------------------
def _cfg_target(project: bool) -> Path:
    return project_config_path(find_project_root()) if project else user_config_path()


@provider_app.command("list")
def provider_list():
    """Show every provider, its model, key status, and fallback order."""
    settings = load_settings()
    chain = settings.chain
    table = RichTable(header_style="bold cyan")
    for col in ("Provider", "Order", "Model", "Key", "Endpoint"):
        table.add_column(col, overflow="fold")
    for name, p in sorted(settings.providers.items(), key=lambda kv: (kv[0] not in chain,
                          chain.index(kv[0]) if kv[0] in chain else 99, kv[0])):
        if not p.requires_key:
            key = "[green]no key needed[/]"
        elif p.api_key:
            key = f"[green]{p.key_source}[/] {secrets.mask(p.api_key)}"
        else:
            key = f"[red]missing[/] ({p.api_key_env})"
        order = str(chain.index(name) + 1) if name in chain else "-"
        table.add_row(name, order, p.model or "[red](unset)[/]", key, p.base_url)
    out.print(table)
    out.print("[dim]Order = fallback sequence. Change: `querymind provider use <name>`. "
              "Add key: `querymind keys set <name>`.[/]")


@provider_app.command("use")
def provider_use(name: str, project: bool = typer.Option(False, "--project",
                 help="Save to this project's config instead of your user config.")):
    """Make NAME the first provider tried (others remain as fallbacks)."""
    settings = load_settings()
    if name not in settings.providers:
        raise _fail(QueryMindError(f"Unknown provider '{name}'. Known: {', '.join(sorted(settings.providers))}"))
    new_chain = [name] + [c for c in settings.chain if c != name]

    def mutate(d):
        d.setdefault("llm", {})["chain"] = new_chain
    update_toml(_cfg_target(project), mutate)
    out.print(f"[green]Provider order:[/] {' -> '.join(new_chain)}")


@provider_app.command("set-model")
def provider_set_model(name: str, model: str, project: bool = typer.Option(False, "--project")):
    """Change the model used by a provider."""
    def mutate(d):
        d.setdefault("llm", {}).setdefault("providers", {}).setdefault(name, {})["model"] = model
    update_toml(_cfg_target(project), mutate)
    out.print(f"[green]{name} will use model[/] {model}")


@provider_app.command("add")
def provider_add(
    name: str,
    base_url: str = typer.Option(..., "--base-url", help="OpenAI-compatible endpoint."),
    model: str = typer.Option(..., "--model"),
    key_env: str = typer.Option(None, "--key-env", help="Env var holding the key (default NAME_API_KEY)."),
    no_key: bool = typer.Option(False, "--no-key", help="Endpoint needs no key (local server)."),
    project: bool = typer.Option(False, "--project"),
):
    """Register any OpenAI-compatible endpoint as a provider."""
    env_name = key_env or f"{name.upper().replace('-', '_')}_API_KEY"

    def mutate(d):
        d.setdefault("llm", {}).setdefault("providers", {})[name] = {
            "base_url": base_url, "model": model, "api_key_env": env_name,
            "requires_key": not no_key}
        chain = d["llm"].setdefault("chain", load_settings().chain)
        if name not in chain:
            chain.append(name)
    update_toml(_cfg_target(project), mutate)
    out.print(f"[green]Added provider[/] {name}. "
              f"{'' if no_key else f'Set its key: querymind keys set {name}'}")


@provider_app.command("models")
def provider_models(name: str, grep: str = typer.Option(None, "--grep", help="Filter model ids.")):
    """List live model ids for a provider (needs its key)."""
    settings = load_settings()
    p = settings.providers.get(name)
    if p is None:
        raise _fail(QueryMindError(f"Unknown provider '{name}'."))
    try:
        client = openai_compat.make_client(p.base_url, p.api_key, 30)
        ids = openai_compat.list_models(client)
    except QueryMindError as exc:
        raise _fail(exc)
    for mid in ids:
        if not grep or grep.lower() in mid.lower():
            out.print(mid)


@provider_app.command("test")
def provider_test(name: str = typer.Argument(None, help="Provider to test (default: whole chain).")):
    """Send a tiny request to verify keys, models, and latency."""
    settings = load_settings()
    names = [name] if name else settings.chain
    failed = False
    for n in names:
        p = settings.providers.get(n)
        if p is None or not p.usable:
            out.print(f"[yellow]skip[/] {n}: not usable (missing key or model)")
            failed = failed or bool(name)
            continue
        started = time.perf_counter()
        try:
            client = openai_compat.make_client(p.base_url, p.api_key, 30)
            openai_compat.complete(client, p.model,
                                   [{"role": "user", "content": "Reply with the single word: ok"}], 0.0, 20)
            out.print(f"[green]ok[/]   {n} ({p.model}) {time.perf_counter() - started:.2f}s")
        except QueryMindError as exc:
            failed = True
            out.print(f"[red]fail[/] {n} ({p.model}): {exc}")
    raise typer.Exit(1 if failed else 0)


# ---------------------------------------------------------------------------
# key management
# ---------------------------------------------------------------------------
def _env_name(target: str) -> str:
    settings = load_settings()
    if target in settings.providers:
        return settings.providers[target].api_key_env
    if target == target.upper() and target.replace("_", "").isalnum():
        return target
    raise QueryMindError(f"'{target}' is neither a known provider nor an ENV_VAR_NAME.")


@keys_app.command("set")
def keys_set(target: str = typer.Argument(..., help="Provider name (gemini) or ENV_VAR_NAME."),
             value: str = typer.Option(None, "--value", help="Avoid: appears in shell history.")):
    """Store (or replace) a key in ~/.config/querymind/credentials.env, outside your repo."""
    try:
        env_name = _env_name(target)
        if value is None:
            value = (typer.prompt(f"{env_name}", hide_input=True) if sys.stdin.isatty()
                     else sys.stdin.readline())
        secrets.set_secret(env_name, value)
    except (QueryMindError, ValueError) as exc:
        raise _fail(exc)
    out.print(f"[green]Saved[/] {env_name} ({secrets.mask(value.strip())}) to {credentials_path()}")


@keys_app.command("list")
def keys_list():
    """Show where each key comes from (values are masked)."""
    settings = load_settings()
    table = RichTable(header_style="bold cyan")
    for col in ("Provider", "Variable", "Status", "Source"):
        table.add_column(col)
    for name, p in sorted(settings.providers.items()):
        if not p.requires_key:
            table.add_row(name, "-", "[dim]not needed[/]", "")
        elif p.api_key:
            table.add_row(name, p.api_key_env, f"[green]{secrets.mask(p.api_key)}[/]", p.key_source or "")
        else:
            table.add_row(name, p.api_key_env, "[red]missing[/]", "")
    out.print(table)
    out.print("[dim]Lookup order: shell env -> ./.env -> ~/.config/querymind/credentials.env[/]")


@keys_app.command("remove")
def keys_remove(target: str):
    """Delete a key you stored with `keys set`."""
    try:
        env_name = _env_name(target)
    except QueryMindError as exc:
        raise _fail(exc)
    out.print(f"[green]Removed[/] {env_name}" if secrets.remove_secret(env_name)
              else f"[yellow]{env_name} was not in credentials.env[/] (check your shell env / .env)")


# ---------------------------------------------------------------------------

# product quality & write commands (Phase 1 & 2)
# ---------------------------------------------------------------------------
@app.command()
def explain(
    target: str = typer.Argument(..., help="Path to a .sql file or a raw SQL string to explain."),
):
    """Explain a query: analyze execution plan, warnings, index suggestions, and plain-English intent."""
    p = Path(target)
    if p.is_file():
        sql = p.read_text(encoding="utf-8", errors="replace")
    else:
        sql = target

    try:
        settings = load_settings()
        with MySQLAdapter(settings.db_config(), timeout_s=int(settings.db["timeout_s"])) as adapter:
            schema = adapter.introspect()
            checked = validate(sql, set(schema.table_names), schema.database, adapter.dialect, int(settings.db["max_rows"]))
            plan = adapter.explain(checked.sql)
    except (QueryMindError, Exception) as exc:
        raise _fail(exc)

    print_sql(checked.sql)
    if not plan.ok:
        err.print(f"[red]Planning failed:[/] {plan.error}")
        raise typer.Exit(1)

    out.print("\n[bold]Plan Analysis:[/]")
    if plan.warnings:
        for w in plan.warnings:
            out.print(f"  [yellow]Performance:[/] {w}")
    else:
        out.print("  [green]Plan OK - no full table scans or large filesorts detected.[/]")

    if plan.index_suggestions:
        out.print("\n[bold cyan]Index Recommendations:[/]")
        for idx in plan.index_suggestions:
            out.print(f"  [green]{idx}[/]")

    # Plain English explanation from LLM if a provider is configured
    try:
        if usable_chain(settings):
            with err.status("[cyan]Generating explanation...[/]"):
                router = build_router(settings)
                prompt = (
                    f"Explain this SQL query concisely in 2-3 plain English sentences for a developer. "
                    f"Mention what tables it joins, what filters it applies, and what metric it calculates:\n\n{checked.sql}"
                )
                comp = router.complete([{"role": "user", "content": prompt}])
                out.print(f"\n[bold]Plain English Explanation:[/] {comp.text.strip()}")
    except Exception:
        pass


@app.command()
def modify(
    file_path: Path = typer.Argument(..., help="Path to .sql file to modify."),
    instruction: str = typer.Argument(..., help="Modification instruction (e.g. 'filter for 2024')."),
    save: bool = typer.Option(False, "--save", "-s", help="Overwrite the file with modified SQL without prompt."),
):
    """Modify an existing .sql file using natural language, verified against your database."""
    if not file_path.is_file():
        raise _fail(QueryMindError(f"File not found: {file_path}"))
    existing_sql = file_path.read_text(encoding="utf-8", errors="replace")

    prompt = (
        f"Modify the following SQL query according to this instruction: {instruction}\n\n"
        f"Existing SQL:\n{existing_sql}"
    )

    try:
        settings = load_settings()
        with err.status("[cyan]Reading schema and modifying query...[/]") as status:
            with _session(settings, status, _ask_clarify(status)) as agent:
                result = agent.ask(prompt)
    except QueryMindError as exc:
        raise _fail(exc)

    if result.status != "ok":
        print_agent_result(result)
        raise typer.Exit(1)

    print_agent_result(result)

    should_write = save or (sys.stdin.isatty() and typer.confirm(f"Overwrite {file_path} with modified SQL?", default=True))
    if should_write:
        export_sql_file(file_path, result.sql, question=instruction, explanation=result.explanation, assumptions=result.assumptions)
        out.print(f"[green]Updated[/] {file_path}")


@app.command(name="write")
def write_cmd(
    sql_or_instruction: str = typer.Argument(..., help="SQL statement (INSERT/UPDATE/DELETE) to execute safely inside a transaction."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip interactive confirmation if verified."),
):
    """Approval-gated write execution with transaction dry-run and preview."""
    settings = load_settings()
    import pymysql
    try:
        conn = pymysql.connect(
            host=settings.db_config().host,
            port=settings.db_config().port,
            user=settings.db_config().user,
            password=settings.db_config().password,
            database=settings.db_config().database,
            connect_timeout=5,
            read_timeout=int(settings.db["timeout_s"]) + 5,
            autocommit=True,
            charset="utf8mb4",
        )
    except pymysql.MySQLError as exc:
        raise _fail(QueryMindError(f"Could not connect for write: {exc}"))

    try:
        with MySQLAdapter(settings.db_config()) as adapter:
            schema = adapter.introspect()
            validated = validate_write(sql_or_instruction, set(schema.table_names), schema.database, adapter.dialect)

        def confirm_cb(val, affected_count, cols, preview_rows) -> bool:
            out.print(f"\n[bold yellow]Dry-run executed inside a transaction:[/]")
            out.print(f"Target table: [cyan]{val.table}[/]")
            out.print(f"Statement type: [cyan]{val.statement_type}[/]")
            out.print(f"Affected rows: [bold red]{affected_count}[/]")
            if not val.has_where and val.statement_type in ("UPDATE", "DELETE"):
                err.print("[bold red]WARNING: No WHERE clause detected! This affects ALL rows in the table.[/]")
            if preview_rows and cols:
                out.print("\n[dim]Preview of affected rows (up to 10):[/]")
                preview_table = RichTable()
                for c in cols:
                    preview_table.add_column(c)
                for r in preview_rows[:10]:
                    preview_table.add_row(*[str(v) for v in r])
                out.print(preview_table)

            if yes:
                return True
            if not sys.stdin.isatty():
                return False
            return typer.confirm(f"Commit these changes to {val.table}?", default=False)

        res = execute_write_transaction(conn, validated, confirm_cb)
        if res.committed:
            out.print(f"[green]Transaction COMMITTED:[/] {res.affected_rows} rows affected.")
        else:
            out.print(f"[yellow]Transaction ROLLED BACK:[/] No changes made to database.")
    except Exception as exc:
        raise _fail(exc)
    finally:
        conn.close()


@app.command()
def export(
    question_or_sql: str = typer.Argument(..., help="Question or SQL to export."),
    sql_file: Path = typer.Option(None, "--sql", help="Save as .sql file."),
    snippet: str = typer.Option(None, "--snippet", help="Generate code snippet: 'python' or 'javascript'."),
):
    """Export a verified query as a .sql file or a Python/JS code snippet."""
    settings = load_settings()
    if question_or_sql.strip().upper().startswith(("SELECT", "WITH")):
        sql = question_or_sql.strip()
    else:
        with err.status("[cyan]Translating question to SQL...[/]") as status:
            with _session(settings, status, _ask_clarify(status)) as agent:
                res = agent.ask(question_or_sql)
                if res.status != "ok":
                    print_agent_result(res)
                    raise typer.Exit(1)
                sql = res.sql

    if sql_file:
        export_sql_file(sql_file, sql, question=question_or_sql)
        out.print(f"[green]Saved SQL to[/] {sql_file}")
    elif snippet:
        lang = snippet.lower().strip()
        db_cfg = settings.db_config()
        if lang in ("python", "py"):
            sys.stdout.write(generate_python_snippet(sql, db_cfg.host, db_cfg.port, db_cfg.user, db_cfg.database))
        elif lang in ("js", "javascript", "node", "ts"):
            sys.stdout.write(generate_javascript_snippet(sql, db_cfg.host, db_cfg.port, db_cfg.user, db_cfg.database))
        else:
            err.print(f"[red]Unknown snippet language '{snippet}'. Choose 'python' or 'javascript'.[/]")
            raise typer.Exit(1)
    else:
        print_sql(sql)


@app.command()
def remember(rule: str = typer.Argument(..., help="Business rule or definition to record in QUERYMIND.md.")):
    """Add a business rule or term definition to QUERYMIND.md."""
    root = find_project_root()
    notes_file = workspace.propose_memory_addition(root, rule)
    out.print(f"[green]Saved rule to[/] {notes_file.name}: {rule}")


@app.command(name="history")
def history_cmd(limit: int = typer.Option(15, "--limit", "-n", help="Number of queries to show.")):
    """Show recent query history."""
    entries = load_history(limit)
    if not entries:
        out.print("[dim]No query history recorded yet.[/]")
        return
    table = RichTable(header_style="bold cyan")
    table.add_column("Time", style="dim")
    table.add_column("Question")
    table.add_column("Status")
    table.add_column("SQL")
    for e in entries:
        t_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.get("timestamp", 0)))
        status_style = "[green]ok[/]" if e.get("status") == "ok" else "[red]failed[/]"
        sql_preview = e.get("sql", "").replace("\n", " ")[:60]
        table.add_row(t_str, e.get("question", ""), status_style, sql_preview)
    out.print(table)


@app.command(name="save")
def save_cmd(
    name: str = typer.Argument(..., help="Name for the saved query."),
    sql: str = typer.Option(None, "--sql", help="SQL string to save (defaults to last executed query)."),
    desc: str = typer.Option("", "--desc", help="Optional description."),
):
    """Save a query under a memorable name."""
    if not sql:
        entries = load_history(1)
        if not entries:
            err.print("[red]No recent query to save. Pass --sql '...'.[/]")
            raise typer.Exit(1)
        sql = entries[0].get("sql", "")
    save_query(name, sql, description=desc)
    out.print(f"[green]Saved query '{name}':[/]\n{sql}")


@app.command(name="saved")
def saved_cmd():
    """List all saved queries."""
    queries = list_saved_queries()
    if not queries:
        out.print("[dim]No saved queries found. Save one with: querymind save <name> --sql '...'[/]")
        return
    table = RichTable(header_style="bold cyan")
    table.add_column("Name", style="bold")
    table.add_column("Description")
    table.add_column("SQL")
    for name, q in sorted(queries.items()):
        sql_preview = q.get("sql", "").replace("\n", " ")[:60]
        table.add_row(name, q.get("description", ""), sql_preview)
    out.print(table)


@app.command(name="mcp")
def mcp_cmd():
    """Start QueryMind Model Context Protocol (MCP) stdio server."""
    run_mcp_server()


if __name__ == "__main__":
    app()


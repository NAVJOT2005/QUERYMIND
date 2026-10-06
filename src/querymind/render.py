"""Terminal output. Progress/chatter goes to stderr; results go to stdout so that
`querymind ask ... --sql-only | pbcopy` and `--json | jq` behave."""

from __future__ import annotations

import csv
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table as RichTable

from querymind.adapters.base import QueryResult, Schema
from querymind.agent.agent import AgentResult

out = Console()
err = Console(stderr=True)

DISPLAY_ROWS = 50


def print_sql(sql: str) -> None:
    out.print(Syntax(sql, "sql", theme="ansi_dark", word_wrap=True, background_color="default"))


def print_table(res: QueryResult, limit: int = DISPLAY_ROWS) -> None:
    table = RichTable(show_lines=False, header_style="bold cyan", row_styles=["", "dim"])
    for col in res.columns:
        table.add_column(str(col), overflow="fold", max_width=40)
    for row in res.rows[:limit]:
        table.add_row(*["NULL" if v is None else str(v) for v in row])
    out.print(table)
    if len(res.rows) > limit:
        out.print(f"[dim]... showing {limit} of {len(res.rows)} rows (use --csv to export all)[/]")


def print_agent_result(res: AgentResult, show_sql: bool = True) -> None:
    if res.status == "failed":
        err.print(Panel(res.error or "Could not produce a working query.",
                        title="[red]Could not answer[/]", border_style="red"))
        return
    if show_sql:
        out.print(Panel(Syntax(res.sql, "sql", theme="ansi_dark", word_wrap=True,
                               background_color="default"), title="SQL", border_style="cyan"))
    if res.explanation:
        out.print(f"[bold]What it does:[/] {res.explanation}")
    for note in res.assumptions:
        out.print(f"[yellow]Assumption:[/] {note}")
    for warn in res.plan_warnings:
        out.print(f"[yellow]Performance:[/] {warn}")
    if res.result is not None:
        if res.result.rows:
            print_table(res.result)
        else:
            out.print("[dim]No rows returned.[/]")
        if res.result.truncated:
            out.print("[dim]Result capped at the row limit (raise it with --max-rows).[/]")
        calls = f"{res.model_calls} model call{'s' if res.model_calls != 1 else ''}"
        fix = f" · {res.repairs} self-repair{'s' if res.repairs != 1 else ''}" if res.repairs else ""
        out.print(f"[dim]{len(res.result.rows)} rows · {res.result.elapsed_s:.3f}s · "
                  f"{res.provider}/{res.model} · {calls}{fix}[/]")


def print_schema(schema: Schema, table: str | None = None) -> None:
    if table:
        if schema.get(table) is None:
            err.print(f"[red]No such table:[/] {table}")
            return
        out.print(schema.describe([table]))
        return
    for t in schema.tables.values():
        kind = " [dim](view)[/]" if t.is_view else ""
        out.print(f"[bold cyan]{t.name}[/]{kind}  {', '.join(t.column_names())}")


def write_csv(path: Path, res: QueryResult) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(res.columns)
        writer.writerows(res.rows)

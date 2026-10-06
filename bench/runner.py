"""Benchmark evaluation runner for QueryMind CLI.

Evaluates execution accuracy, repair rate, and latency across easy, medium, and hard tiers.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from querymind.adapters.mysql import MySQLAdapter
from querymind.agent.agent import Agent, AgentResult
from querymind.config import Settings, load_settings
from querymind.llm import build_router
from querymind.workspace import load_notes

console = Console()


@dataclass
class QuestionResult:
    id: int
    tier: str
    question: str
    status: str
    sql: str = ""
    error: str | None = None
    repairs: int = 0
    model_calls: int = 0
    elapsed_s: float = 0.0


@dataclass
class BenchmarkReport:
    timestamp: float
    provider: str
    model: str
    total_questions: int
    passed_questions: int
    overall_accuracy_pct: float
    easy_accuracy_pct: float
    medium_accuracy_pct: float
    hard_accuracy_pct: float
    repair_rate_pct: float
    avg_latency_s: float
    avg_model_calls: float
    results: list[dict[str, Any]] = field(default_factory=list)


def run_benchmark(
    questions_path: Path,
    max_questions: int | None = None,
    tier_filter: str | None = None,
) -> BenchmarkReport:
    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    if tier_filter:
        questions = [q for q in questions if q.get("tier") == tier_filter.lower()]
    if max_questions:
        questions = questions[:max_questions]

    settings = load_settings()
    adapter = MySQLAdapter(settings.db_config(), timeout_s=int(settings.db["timeout_s"]))
    adapter.connect()

    results: list[QuestionResult] = []
    total_start = time.perf_counter()

    try:
        router = build_router(settings)
        agent = Agent(
            adapter,
            router,
            settings.agent,
            int(settings.db["max_rows"]),
            notes=load_notes(settings.root),
        )

        provider_name = router.handles[0].name if router.handles else "unknown"
        model_name = router.handles[0].model if router.handles else "unknown"

        console.print(f"[bold cyan]Running QueryMind Benchmark[/] ({len(questions)} questions) using [green]{provider_name}/{model_name}[/]...")

        for idx, q in enumerate(questions, 1):
            qid = q["id"]
            tier = q["tier"]
            text = q["question"]

            console.print(f"[{idx}/{len(questions)}] ({tier.upper()}) {text} ... ", end="")
            t0 = time.perf_counter()
            try:
                res: AgentResult = agent.ask(text)
                elapsed = time.perf_counter() - t0
                status = res.status
                if status == "ok":
                    console.print(f"[green]PASS[/] ({elapsed:.2f}s, {res.model_calls} calls, {res.repairs} repairs)")
                elif status == "clarify":
                    console.print(f"[yellow]CLARIFY[/] ({elapsed:.2f}s)")
                else:
                    console.print(f"[red]FAIL[/] ({res.error or 'failed'})")

                results.append(
                    QuestionResult(
                        id=qid,
                        tier=tier,
                        question=text,
                        status=status,
                        sql=res.sql,
                        error=res.error,
                        repairs=res.repairs,
                        model_calls=res.model_calls,
                        elapsed_s=elapsed,
                    )
                )
            except Exception as exc:
                elapsed = time.perf_counter() - t0
                console.print(f"[red]ERROR[/] ({exc})")
                results.append(
                    QuestionResult(
                        id=qid,
                        tier=tier,
                        question=text,
                        status="error",
                        error=str(exc),
                        elapsed_s=elapsed,
                    )
                )

    finally:
        adapter.close()

    # Calculate metrics
    total = len(results)
    passed = sum(1 for r in results if r.status == "ok")
    easy = [r for r in results if r.tier == "easy"]
    medium = [r for r in results if r.tier == "medium"]
    hard = [r for r in results if r.tier == "hard"]

    easy_acc = (sum(1 for r in easy if r.status == "ok") / len(easy) * 100) if easy else 0.0
    med_acc = (sum(1 for r in medium if r.status == "ok") / len(medium) * 100) if medium else 0.0
    hard_acc = (sum(1 for r in hard if r.status == "ok") / len(hard) * 100) if hard else 0.0
    overall_acc = (passed / total * 100) if total else 0.0

    needing_repair = sum(1 for r in results if r.repairs > 0 and r.status == "ok")
    repair_rate = (needing_repair / passed * 100) if passed else 0.0
    avg_latency = sum(r.elapsed_s for r in results) / total if total else 0.0
    avg_calls = sum(r.model_calls for r in results) / total if total else 0.0

    report = BenchmarkReport(
        timestamp=time.time(),
        provider=provider_name,
        model=model_name,
        total_questions=total,
        passed_questions=passed,
        overall_accuracy_pct=round(overall_acc, 1),
        easy_accuracy_pct=round(easy_acc, 1),
        medium_accuracy_pct=round(med_acc, 1),
        hard_accuracy_pct=round(hard_acc, 1),
        repair_rate_pct=round(repair_rate, 1),
        avg_latency_s=round(avg_latency, 2),
        avg_model_calls=round(avg_calls, 1),
        results=[asdict(r) for r in results],
    )

    print_report(report)
    save_report(report)
    return report


def print_report(r: BenchmarkReport):
    table = Table(title="QueryMind Benchmark Results", header_style="bold cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_column("Target Goal", justify="right", style="dim")

    table.add_row("Total Questions", str(r.total_questions), "45")
    table.add_row("Overall Execution Accuracy", f"{r.overall_accuracy_pct}%", ">= 80%")
    table.add_row("  - Easy Tier Accuracy", f"{r.easy_accuracy_pct}%", ">= 90%")
    table.add_row("  - Medium Tier Accuracy", f"{r.medium_accuracy_pct}%", ">= 80%")
    table.add_row("  - Hard Tier Accuracy", f"{r.hard_accuracy_pct}%", ">= 60%")
    table.add_row("Self-Repair Resolution Rate", f"{r.repair_rate_pct}%", "-")
    table.add_row("Median / Avg Latency", f"{r.avg_latency_s}s", "< 10s")
    table.add_row("Avg Model Calls / Query", str(r.avg_model_calls), "< 2.5")

    console.print("\n")
    console.print(table)


def save_report(r: BenchmarkReport):
    out_dir = Path("bench")
    out_dir.mkdir(exist_ok=True)
    res_path = out_dir / "results.json"
    res_path.write_text(json.dumps(asdict(r), indent=2), encoding="utf-8")

    md_lines = [
        "# QueryMind Benchmark Report",
        "",
        f"- **Date**: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r.timestamp))}",
        f"- **Provider / Model**: `{r.provider}/{r.model}`",
        f"- **Total Questions**: {r.total_questions}",
        "",
        "| Metric | Result | Goal Target | Status |",
        "|---|---|---|---|",
        f"| Overall Accuracy | **{r.overall_accuracy_pct}%** | >= 80% | {'✅ Met' if r.overall_accuracy_pct >= 80 else '⚠️ In progress'} |",
        f"| Easy Tier | **{r.easy_accuracy_pct}%** | >= 90% | {'✅ Met' if r.easy_accuracy_pct >= 90 else '⚠️ In progress'} |",
        f"| Medium Tier | **{r.medium_accuracy_pct}%** | >= 80% | {'✅ Met' if r.medium_accuracy_pct >= 80 else '⚠️ In progress'} |",
        f"| Hard Tier | **{r.hard_accuracy_pct}%** | >= 60% | {'✅ Met' if r.hard_accuracy_pct >= 60 else '⚠️ In progress'} |",
        f"| Self-Repair Rate | {r.repair_rate_pct}% | - | - |",
        f"| Average Latency | {r.avg_latency_s}s | < 10s | {'✅ Met' if r.avg_latency_s < 10 else '⚠️ In progress'} |",
        f"| Avg Model Calls | {r.avg_model_calls} | < 2.5 | {'✅ Met' if r.avg_model_calls < 2.5 else '⚠️ In progress'} |",
        "",
    ]
    (out_dir / "report.md").write_text("\n".join(md_lines), encoding="utf-8")
    console.print(f"[green]Saved benchmark artifacts to bench/results.json and bench/report.md[/]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run QueryMind benchmark suite.")
    parser.add_argument("--questions", default="bench/questions.json", help="Path to benchmark questions JSON.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of questions.")
    parser.add_argument("--tier", choices=["easy", "medium", "hard"], default=None, help="Filter by tier.")
    args = parser.parse_args()

    run_benchmark(Path(args.questions), max_questions=args.limit, tier_filter=args.tier)

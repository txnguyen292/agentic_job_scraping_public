from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer
from loguru import logger
from rich.console import Console
from rich.table import Table

from job_scraper.db import ensure_db, query_jobs
from job_scraper.utils.extraction_compare import compare_job_extraction_files
from job_scraper.pipeline import run_crawl


DEFAULT_DB_PATH = "data/jobs.db"
app = typer.Typer(add_completion=False, no_args_is_help=True, help="Job scraper CLI")
console = Console()


def _configure_logging(verbose: bool) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG" if verbose else "INFO",
        format="<level>{level: <8}</level> | {message}",
    )


def _format_score(value: float) -> str:
    return f"{value:.2f}"


def _row_to_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _emit_json(payload: Any) -> None:
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=True))


def _render_jobs_table(rows: list[Any]) -> None:
    if not rows:
        console.print("[yellow]No jobs found.[/yellow]")
        return

    table = Table(title="Top Jobs")
    table.add_column("Company", style="cyan")
    table.add_column("Title", style="bold")
    table.add_column("Remote")
    table.add_column("Overall", justify="right")
    table.add_column("AI/ML", justify="right")
    table.add_column("Startup", justify="right")
    table.add_column("URL", overflow="fold")

    for row in rows:
        table.add_row(
            row["company_name"],
            row["title"],
            row["remote_type"] or "unknown",
            _format_score(row["overall_score"]),
            _format_score(row["ai_ml_score"]),
            _format_score(row["startup_score"]),
            row["job_url"],
        )

    console.print(table)


def _render_crawl_summary(result) -> None:
    console.print(
        "[green]Crawl complete[/green] "
        f"run=[bold]{result.run_id}[/bold] status=[bold]{result.status}[/bold] "
        f"sources={result.source_count} jobs={result.discovered_count} writes={result.written_count} errors={result.error_count}"
    )

    table = Table(title="Source Results")
    table.add_column("Source", style="cyan")
    table.add_column("Status")
    table.add_column("Jobs", justify="right")
    table.add_column("Error", overflow="fold")

    for source_name, source_result in result.source_results.items():
        table.add_row(
            source_name,
            str(source_result.get("status", "")),
            str(source_result.get("job_count", "")),
            str(source_result.get("error", "")),
        )

    console.print(table)


@app.command("init-db")
def init_db(
    db: str = typer.Option(DEFAULT_DB_PATH, "--db", help="SQLite database path"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable debug logging."),
) -> None:
    _configure_logging(verbose)
    logger.debug("Initializing database at {}", db)
    conn = ensure_db(db)
    conn.close()
    payload = {"initialized": str(Path(db).resolve())}
    if json_output:
        _emit_json(payload)
        return
    console.print(f"[green]Initialized database[/green] at [bold]{payload['initialized']}[/bold]")

@app.command("crawl")
def crawl(
    db: str = typer.Option(DEFAULT_DB_PATH, "--db", help="SQLite database path"),
    source_file: str = typer.Option(
        "seeds/demo_sources.json",
        "--source-file",
        help="Path to the source configuration JSON file",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable debug logging."),
) -> None:
    _configure_logging(verbose)
    logger.debug("Starting crawl from {}", source_file)
    result = run_crawl(source_file, db)
    payload = {
        "run_id": result.run_id,
        "status": result.status,
        "source_count": result.source_count,
        "discovered_count": result.discovered_count,
        "written_count": result.written_count,
        "error_count": result.error_count,
        "source_results": result.source_results,
    }
    if json_output:
        _emit_json(payload)
        return
    _render_crawl_summary(result)


@app.command("top")
def top(
    db: str = typer.Option(DEFAULT_DB_PATH, "--db", help="SQLite database path"),
    keyword: str = typer.Option("", "--keyword", help="Keyword filter"),
    limit: int = typer.Option(10, "--limit", help="Maximum number of jobs to print"),
    relevant_only: bool = typer.Option(
        False,
        "--relevant-only",
        help="Show only jobs that passed the relevance threshold",
    ),
    min_score: float = typer.Option(None, "--min-score", help="Minimum overall score"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable debug logging."),
) -> None:
    _configure_logging(verbose)
    conn = ensure_db(db)
    try:
        rows = query_jobs(
            conn,
            keyword=keyword,
            relevant_only=relevant_only,
            min_score=min_score,
            limit=limit,
        )
    finally:
        conn.close()

    if json_output:
        _emit_json({"items": [_row_to_dict(row) for row in rows]})
        return
    _render_jobs_table(rows)


@app.command("compare-extraction")
def compare_extraction(
    actual: str = typer.Argument(..., help="Path to sandbox-produced job_extraction JSON."),
    expected: str = typer.Argument(..., help="Path to verified expected job_extraction JSON."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
) -> None:
    """Compare sandbox extraction output against a verified expected fixture."""
    result = compare_job_extraction_files(actual, expected)
    if json_output:
        _emit_json(result)
        raise typer.Exit(0 if result["status"] == "pass" else 1)

    status_style = "green" if result["status"] == "pass" else "red"
    console.print(
        f"[{status_style}]Extraction comparison: {result['status']}[/{status_style}] "
        f"matched={result['matched_job_count']} expected={result['expected_job_count']} actual={result['actual_job_count']}"
    )
    if result["missing_urls"]:
        console.print("[red]Missing URLs[/red]")
        for url in result["missing_urls"]:
            console.print(f"- {url}")
    if result["extra_urls"]:
        console.print("[yellow]Extra URLs[/yellow]")
        for url in result["extra_urls"]:
            console.print(f"- {url}")
    if result["field_mismatches"]:
        table = Table(title="Field Mismatches")
        table.add_column("URL", overflow="fold")
        table.add_column("Field")
        table.add_column("Expected", overflow="fold")
        table.add_column("Actual", overflow="fold")
        for mismatch in result["field_mismatches"]:
            table.add_row(
                str(mismatch["job_url"]),
                str(mismatch["field"]),
                str(mismatch["expected"]),
                str(mismatch["actual"]),
            )
        console.print(table)
    raise typer.Exit(0 if result["status"] == "pass" else 1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer
from loguru import logger
from rich.console import Console
from rich.table import Table


console = Console(stderr=True)


def configure_logging(debug: bool = False) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG" if debug else "INFO",
        format="<level>{level: <8}</level> | {message}",
    )


def abort(message: str, *, code: int = 1) -> None:
    console.print(f"[red]{message}[/red]")
    raise typer.Exit(code)


def emit_json(payload: Any) -> None:
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=True))


def emit_path(path: Path) -> None:
    typer.echo(str(path))


def render_error_table(title: str, errors: list[str]) -> None:
    table = Table(title=title)
    table.add_column("#", style="cyan", no_wrap=True)
    table.add_column("Issue", style="red")
    for index, error in enumerate(errors, start=1):
        table.add_row(str(index), error)
    console.print(table)

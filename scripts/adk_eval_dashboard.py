"""Generate a source-bound ADK eval dashboard from eval history JSON files."""

from __future__ import annotations

import functools
import http.server
import socketserver
import sys
import urllib.parse
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from utils import DEFAULT_INPUT, build_dashboard_data, render_dashboard_html, write_dashboard


DEFAULT_OUTPUT = Path("reports/adk-eval-dashboard.html")

console = Console(stderr=True)
app = typer.Typer(
    add_completion=False,
    help="Build and optionally serve an ADK eval dashboard from .evalset_result.json files.",
)


def serve_file(output: Path, port: int) -> None:
    output = output.resolve()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(output.parent))
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        url = f"http://127.0.0.1:{port}/{urllib.parse.quote(output.name)}"
        console.print(f"[green]Serving ADK eval dashboard:[/] {url}")
        httpd.serve_forever()


@app.command()
def main(
    inputs: Annotated[
        list[Path] | None,
        typer.Option("--input", "-i", help="ADK eval result file or directory. Repeatable."),
    ] = None,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="HTML dashboard output path."),
    ] = DEFAULT_OUTPUT,
    limit: Annotated[
        int | None,
        typer.Option("--limit", help="Use only the latest N eval result files."),
    ] = None,
    serve: Annotated[
        bool,
        typer.Option("--serve", help="Serve the generated dashboard after writing it."),
    ] = False,
    port: Annotated[int, typer.Option("--port", help="Port used with --serve.")] = 8044,
) -> None:
    """Build an HTML dashboard from ADK eval output JSON."""
    data = build_dashboard_data(inputs, limit=limit)
    written = write_dashboard(output, data)
    console.print(
        f"[green]Wrote[/] {written} from {len(data['source_files'])} result file(s), "
        f"{data['run_count']} run(s)."
    )
    if serve:
        serve_file(written, port)


if __name__ == "__main__":
    try:
        app()
    except KeyboardInterrupt:
        console.print("[yellow]Stopped dashboard server.[/]")
        sys.exit(130)

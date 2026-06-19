"""Refresh the ADK Observability model-pricing cache from LiteLLM."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console


MODULE_PATH = Path(__file__).with_name("adk_token_dashboard.py")
SPEC = importlib.util.spec_from_file_location("adk_token_dashboard", MODULE_PATH)
assert SPEC is not None
adk_token_dashboard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(adk_token_dashboard)

console = Console(stderr=True)
app = typer.Typer(
    add_completion=False,
    help="Refresh normalized USD-per-1M-token pricing for ADK Observability.",
)


@app.command()
def main(
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Normalized pricing cache output path."),
    ] = adk_token_dashboard.DEFAULT_PRICING_CACHE,
    source_url: Annotated[
        str,
        typer.Option("--source-url", help="Machine-readable LiteLLM pricing JSON URL."),
    ] = adk_token_dashboard.LITELLM_PRICING_URL,
) -> None:
    """Download LiteLLM pricing and save the normalized dashboard cache."""
    catalog = adk_token_dashboard.fetch_pricing_catalog(source_url)
    written = adk_token_dashboard.write_pricing_cache(output, catalog)
    console.print(
        f"[green]Wrote[/] {written} with "
        f"{len(catalog['rates_usd_per_1m'])} priced model entries from {source_url}."
    )


if __name__ == "__main__":
    app()

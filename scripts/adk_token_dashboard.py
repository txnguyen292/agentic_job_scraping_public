"""Generate an ADK runtime token distribution dashboard from session SQLite DBs."""

from __future__ import annotations

import datetime as dt
import functools
import http.server
import json
import math
import socketserver
import sqlite3
import subprocess
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console


DEFAULT_OUTPUT = Path("reports/adk-token-dashboard.html")
DEFAULT_PRICING_CACHE = Path("reports/adk-model-pricing.json")
DASHBOARD_DATA_PLACEHOLDER = "__TOKEN_DASHBOARD_DATA__"
TEMPLATE_PATH = Path(__file__).with_name("adk_token_dashboard.template.html")
LITELLM_PRICING_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)
RUNTIME_QUANTILE_METRICS = (
    ("total_tokens", "Total tokens"),
    ("prompt_tokens", "Prompt tokens"),
    ("cached_tokens", "Cached prompt"),
    ("noncached_prompt_tokens", "Non-cached prompt"),
    ("output_tokens", "Output tokens"),
    ("thoughts_tokens", "Thought tokens"),
    ("llm_events", "LLM calls"),
)
FALLBACK_MODEL_COST_RATES_USD_PER_1M = {
    "gpt-5.4-mini": {
        "input": 0.75,
        "cached_input": 0.075,
        "output": 4.5,
        "reasoning_output": 4.5,
    },
    "gpt-5.4": {
        "input": 2.5,
        "cached_input": 0.25,
        "output": 15.0,
        "reasoning_output": 15.0,
    },
    "gpt-5.5": {
        "input": 5.0,
        "cached_input": 0.5,
        "output": 30.0,
        "reasoning_output": 30.0,
    },
    "gpt-4o": {
        "input": 2.5,
        "cached_input": 1.25,
        "output": 10.0,
        "reasoning_output": 10.0,
    },
}

console = Console(stderr=True)
app = typer.Typer(
    add_completion=False,
    help="Build and optionally serve an ADK token dashboard from local session.db files.",
)


def utc_now_iso() -> str:
    return dt.datetime.now(tz=dt.UTC).isoformat()


def display_path(path: Path, project_root: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(project_root.resolve()))
    except ValueError:
        return str(resolved)


def iter_default_db_paths(project_root: Path) -> list[Path]:
    """Find ADK session stores in the current checkout only."""
    paths = set(project_root.resolve().glob("**/.adk/session.db"))
    return sorted(path for path in paths if path.is_file())


def runtime_label(path: Path, project_root: Path) -> str:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(project_root.resolve())
        parent = relative.parent.parent if relative.parent.name == ".adk" else relative.parent
        return f"current:{parent.as_posix() or '.'}"
    except ValueError:
        pass

    return display_path(resolved, project_root)


def read_live_processes() -> list[dict[str, str]]:
    try:
        output = subprocess.check_output(
            ["ps", "-axo", "pid,lstart,etime,command"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return []

    rows = []
    for line in output.splitlines()[1:]:
        if "adk web" not in line:
            continue
        columns = line.split(None, 8)
        if len(columns) < 9:
            continue
        rows.append(
            {
                "pid": columns[0],
                "started": " ".join(columns[1:6]),
                "elapsed": columns[6],
                "command": columns[8],
            }
        )
    return rows


def has_events_table(path: Path) -> bool:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "select 1 from sqlite_master where type = 'table' and name = 'events' limit 1"
        ).fetchone()
    finally:
        conn.close()
    return row is not None


def timestamp_to_iso(value: float | int | None) -> str:
    if value is None:
        return ""
    return dt.datetime.fromtimestamp(float(value), tz=dt.UTC).isoformat()


def event_json(row: sqlite3.Row) -> dict[str, Any]:
    raw = row["event_data"]
    return json.loads(raw) if raw else {}


def usage_metadata(event: dict[str, Any]) -> dict[str, Any] | None:
    usage = event.get("usage_metadata")
    if isinstance(usage, dict):
        return usage
    camel_usage = event.get("usageMetadata")
    if isinstance(camel_usage, dict):
        return camel_usage
    return None


def usage_int(usage: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = usage.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0
    return 0


def normalize_model_name(model: str) -> str:
    return model.removeprefix("openai/").lower()


def litellm_entry_to_rate(entry: dict[str, Any]) -> dict[str, float] | None:
    input_cost = entry.get("input_cost_per_token")
    output_cost = entry.get("output_cost_per_token")
    if input_cost is None or output_cost is None:
        return None
    cached_cost = entry.get("cache_read_input_token_cost", input_cost)
    output = float(output_cost) * 1_000_000
    return {
        "input": float(input_cost) * 1_000_000,
        "cached_input": float(cached_cost) * 1_000_000,
        "output": output,
        "reasoning_output": output,
    }


def normalize_litellm_pricing(raw: dict[str, Any]) -> dict[str, dict[str, float]]:
    rates = {}
    for model, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        rate = litellm_entry_to_rate(entry)
        if rate is not None:
            rates[normalize_model_name(str(model))] = rate
    return rates


def fetch_pricing_catalog(source_url: str = LITELLM_PRICING_URL, *, timeout: float = 20.0) -> dict[str, Any]:
    request = urllib.request.Request(
        source_url,
        headers={"User-Agent": "adk-observability-dashboard/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = json.load(response)
    rates = normalize_litellm_pricing(raw)
    return {
        "schema_version": 1,
        "source": "litellm",
        "source_url": source_url,
        "fetched_at": utc_now_iso(),
        "rates_usd_per_1m": rates,
    }


def fallback_pricing_catalog(reason: str = "") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": "bundled-fallback",
        "source_url": "",
        "fetched_at": "",
        "fallback_reason": reason,
        "rates_usd_per_1m": FALLBACK_MODEL_COST_RATES_USD_PER_1M,
    }


def write_pricing_cache(output: Path, catalog: dict[str, Any]) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(catalog, indent=2, sort_keys=True), encoding="utf-8")
    return output


def read_pricing_cache(path: Path) -> dict[str, Any]:
    catalog = json.loads(path.read_text(encoding="utf-8"))
    rates = catalog.get("rates_usd_per_1m")
    if not isinstance(rates, dict):
        raise ValueError(f"Pricing cache {path} is missing rates_usd_per_1m.")
    return catalog


def load_pricing_catalog(
    cache_path: Path = DEFAULT_PRICING_CACHE,
    *,
    source_url: str = LITELLM_PRICING_URL,
    refresh: bool = True,
) -> dict[str, Any]:
    if refresh:
        try:
            catalog = fetch_pricing_catalog(source_url)
            write_pricing_cache(cache_path, catalog)
            return catalog
        except Exception as exc:
            console.print(f"[yellow]Pricing refresh failed:[/] {exc}")

    if cache_path.exists():
        try:
            return read_pricing_cache(cache_path)
        except Exception as exc:
            console.print(f"[yellow]Pricing cache ignored:[/] {exc}")

    return fallback_pricing_catalog("Pricing refresh/cache unavailable.")


def cost_rate_for_model(
    model: str,
    pricing_rates: dict[str, dict[str, float]] | None = None,
) -> dict[str, float] | None:
    rates = pricing_rates or FALLBACK_MODEL_COST_RATES_USD_PER_1M
    normalized = normalize_model_name(model)
    if normalized in rates:
        return rates[normalized]
    for prefix in sorted(rates, key=len, reverse=True):
        if normalized.startswith(f"{prefix}-"):
            return rates[prefix]
    return None


def usage_costs(
    usage: dict[str, Any],
    model: str,
    *,
    pricing_rates: dict[str, dict[str, float]] | None = None,
) -> dict[str, float]:
    rates = cost_rate_for_model(model, pricing_rates)
    if rates is None:
        return {
            "prompt_cost": 0.0,
            "cached_cost": 0.0,
            "noncached_prompt_cost": 0.0,
            "output_cost": 0.0,
            "thoughts_cost": 0.0,
            "total_cost": 0.0,
        }

    prompt = usage_int(usage, "prompt_token_count", "promptTokenCount")
    cached = usage_int(usage, "cached_content_token_count", "cachedContentTokenCount")
    noncached = max(prompt - cached, 0)
    output = usage_int(usage, "candidates_token_count", "candidatesTokenCount")
    thoughts = usage_int(usage, "thoughts_token_count", "thoughtsTokenCount")
    cached_cost = cached * rates["cached_input"] / 1_000_000
    noncached_prompt_cost = noncached * rates["input"] / 1_000_000
    output_cost = output * rates["output"] / 1_000_000
    thoughts_cost = thoughts * rates["reasoning_output"] / 1_000_000
    prompt_cost = cached_cost + noncached_prompt_cost
    return {
        "prompt_cost": prompt_cost,
        "cached_cost": cached_cost,
        "noncached_prompt_cost": noncached_prompt_cost,
        "output_cost": output_cost,
        "thoughts_cost": thoughts_cost,
        "total_cost": prompt_cost + output_cost + thoughts_cost,
    }


def empty_counter() -> dict[str, int | float]:
    return {
        "llm_events": 0,
        "prompt_tokens": 0,
        "cached_tokens": 0,
        "noncached_prompt_tokens": 0,
        "output_tokens": 0,
        "thoughts_tokens": 0,
        "total_tokens": 0,
        "prompt_cost": 0.0,
        "cached_cost": 0.0,
        "noncached_prompt_cost": 0.0,
        "output_cost": 0.0,
        "thoughts_cost": 0.0,
        "total_cost": 0.0,
    }


def add_usage(
    counter: dict[str, int | float],
    usage: dict[str, Any],
    *,
    model: str = "",
    pricing_rates: dict[str, dict[str, float]] | None = None,
) -> None:
    prompt = usage_int(usage, "prompt_token_count", "promptTokenCount")
    cached = usage_int(usage, "cached_content_token_count", "cachedContentTokenCount")
    output = usage_int(usage, "candidates_token_count", "candidatesTokenCount")
    thoughts = usage_int(usage, "thoughts_token_count", "thoughtsTokenCount")
    total = usage_int(usage, "total_token_count", "totalTokenCount")
    counter["llm_events"] += 1
    counter["prompt_tokens"] += prompt
    counter["cached_tokens"] += cached
    counter["noncached_prompt_tokens"] += max(prompt - cached, 0)
    counter["output_tokens"] += output
    counter["thoughts_tokens"] += thoughts
    counter["total_tokens"] += total
    for key, value in usage_costs(usage, model, pricing_rates=pricing_rates).items():
        counter[key] += value


def parse_session_db(
    path: Path,
    *,
    label: str,
    pricing_rates: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        event_rows = conn.execute(
            "select app_name, user_id, session_id, invocation_id, timestamp, event_data "
            "from events order by timestamp"
        ).fetchall()
    finally:
        conn.close()

    runtime = empty_counter()
    models: dict[str, dict[str, Any]] = {}
    sessions: dict[str, dict[str, Any]] = {}
    timeline: dict[str, dict[str, int]] = defaultdict(empty_counter)

    for row in event_rows:
        event = event_json(row)
        usage = usage_metadata(event)
        if not usage:
            continue

        model = str(event.get("model_version") or event.get("modelVersion") or "unknown")
        session_id = str(row["session_id"])
        timestamp = float(row["timestamp"])
        bucket = dt.datetime.fromtimestamp(timestamp, tz=dt.UTC).date().isoformat()

        add_usage(runtime, usage, model=model, pricing_rates=pricing_rates)
        if model not in models:
            models[model] = {"runtime": label, "model": model, **empty_counter()}
        add_usage(models[model], usage, model=model, pricing_rates=pricing_rates)

        session = sessions.setdefault(
            session_id,
            {
                "runtime": label,
                "session_id": session_id,
                "model": model,
                "models": set(),
                "started_at": timestamp,
                "ended_at": timestamp,
                "app_name": row["app_name"],
                "user_id": row["user_id"],
                **empty_counter(),
            },
        )
        session["started_at"] = min(session["started_at"], timestamp)
        session["ended_at"] = max(session["ended_at"], timestamp)
        session["models"].add(model)
        add_usage(session, usage, model=model, pricing_rates=pricing_rates)

        add_usage(timeline[bucket], usage, model=model, pricing_rates=pricing_rates)

    session_rows = []
    for session in sessions.values():
        models_set = session.pop("models")
        session["models"] = sorted(models_set)
        session["model"] = ", ".join(session["models"]) or session["model"]
        session["started_at"] = timestamp_to_iso(session["started_at"])
        session["ended_at"] = timestamp_to_iso(session["ended_at"])
        session_rows.append(session)

    return {
        "runtime": label,
        "db_path": str(path.resolve()),
        "db_path_display": str(path),
        **runtime,
        "sessions": sorted(session_rows, key=lambda item: item["ended_at"], reverse=True),
        "models": sorted(models.values(), key=lambda item: item["total_tokens"], reverse=True),
        "timeline": [
            {"date": date, **counter}
            for date, counter in sorted(timeline.items(), key=lambda item: item[0])
        ],
    }


def combine_counters(items: list[dict[str, Any]]) -> dict[str, int | float]:
    combined = empty_counter()
    for item in items:
        for key in combined:
            combined[key] += item.get(key) or 0
    return combined


def enrich_percentages(rows: list[dict[str, Any]], total_tokens: int) -> None:
    for row in rows:
        prompt = int(row.get("prompt_tokens") or 0)
        cached = int(row.get("cached_tokens") or 0)
        total = int(row.get("total_tokens") or 0)
        row["cache_pct_of_prompt"] = round((cached / prompt) * 100, 1) if prompt else 0
        row["pct_total"] = round((total / total_tokens) * 100, 1) if total_tokens else 0


def quantile(values: list[int | float], probability: float) -> float:
    """Return a linear-interpolated quantile for a small runtime distribution."""
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def build_runtime_quantiles(runtime_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    quantiles = []
    for key, label in RUNTIME_QUANTILE_METRICS:
        values = [int(row.get(key) or 0) for row in runtime_rows]
        quantiles.append(
            {
                "metric": key,
                "label": label,
                "q1": quantile(values, 0.25),
                "median": quantile(values, 0.5),
                "q3": quantile(values, 0.75),
                "min": min(values) if values else 0,
                "max": max(values) if values else 0,
                "runtime_count": len(values),
            }
        )
    return quantiles


def build_dashboard_data(
    db_paths: list[Path] | None = None,
    *,
    project_root: Path | None = None,
    include_live_processes: bool = True,
    pricing_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = project_root or Path.cwd()
    pricing_catalog = pricing_catalog or fallback_pricing_catalog()
    pricing_rates = pricing_catalog.get("rates_usd_per_1m") or FALLBACK_MODEL_COST_RATES_USD_PER_1M
    paths = db_paths or iter_default_db_paths(root)
    deduped_paths = sorted({path.resolve() for path in paths if path.exists()})
    valid_paths = [path for path in deduped_paths if has_events_table(path)]
    skipped_paths = [path for path in deduped_paths if path not in valid_paths]
    runtimes = [
        parse_session_db(path, label=runtime_label(path, root), pricing_rates=pricing_rates)
        for path in valid_paths
    ]
    total = combine_counters(runtimes)
    total_sessions = sum(len(runtime["sessions"]) for runtime in runtimes)

    runtime_rows = []
    model_rows = []
    session_rows = []
    timeline_by_date: dict[str, dict[str, int]] = defaultdict(empty_counter)
    for runtime in runtimes:
        runtime_rows.append(
            {
                key: runtime[key]
                for key in (
                    "runtime",
                    "db_path",
                    "db_path_display",
                    "llm_events",
                    "prompt_tokens",
                    "cached_tokens",
                    "noncached_prompt_tokens",
                    "output_tokens",
                    "thoughts_tokens",
                    "total_tokens",
                    "prompt_cost",
                    "cached_cost",
                    "noncached_prompt_cost",
                    "output_cost",
                    "thoughts_cost",
                    "total_cost",
                )
            }
        )
        model_rows.extend(runtime["models"])
        session_rows.extend(runtime["sessions"])
        for bucket in runtime["timeline"]:
            addend = timeline_by_date[bucket["date"]]
            for key in empty_counter():
                addend[key] += bucket.get(key) or 0

    runtime_rows.sort(key=lambda item: item["total_tokens"], reverse=True)
    model_rows.sort(key=lambda item: item["total_tokens"], reverse=True)
    session_rows.sort(key=lambda item: item["ended_at"], reverse=True)
    enrich_percentages(runtime_rows, total["total_tokens"])
    enrich_percentages(model_rows, total["total_tokens"])
    enrich_percentages(session_rows, total["total_tokens"])
    observed_models = sorted({str(row.get("model") or "unknown") for row in model_rows})
    observed_rates = {
        model: rate
        for model in observed_models
        if (rate := cost_rate_for_model(model, pricing_rates)) is not None
    }
    unpriced_models = [
        model for model in observed_models if cost_rate_for_model(model, pricing_rates) is None
    ]
    pricing_source = str(pricing_catalog.get("source") or "unknown")
    pricing_source_url = str(pricing_catalog.get("source_url") or "")
    notes = [
        "Prompt tokens include cached prompt tokens when ADK reports both fields.",
        "Thought tokens are reported separately because ADK total_token_count excludes them for the observed OpenAI reasoning events.",
        "Runtime means one ADK session.db source, either discovered in this checkout or passed explicitly with --db.",
        f"Cost rates came from {pricing_source_url or pricing_source}.",
    ]
    if unpriced_models:
        notes.append(
            "No pricing was found for these observed models, so their cost contribution is $0: "
            + ", ".join(unpriced_models)
            + "."
        )

    return {
        "generated_at": utc_now_iso(),
        "project_root": display_path(root, root),
        "source_dbs": [display_path(path, root) for path in valid_paths],
        "skipped_dbs": [display_path(path, root) for path in skipped_paths],
        "runtime_count": len(runtimes),
        "session_count": total_sessions,
        "totals": total,
        "cost_note": f"Calculated locally from ADK token buckets using {pricing_source} USD-per-1M-token rates.",
        "cost_pricing_source": pricing_source,
        "cost_pricing_source_url": pricing_source_url,
        "cost_pricing_fetched_at": pricing_catalog.get("fetched_at") or "",
        "cost_unpriced_models": unpriced_models,
        "cost_rates_usd_per_1m": observed_rates,
        "runtimes": runtime_rows,
        "runtime_quantiles": build_runtime_quantiles(runtime_rows),
        "models": model_rows,
        "sessions": session_rows,
        "timeline": [
            {"date": date, **counter}
            for date, counter in sorted(timeline_by_date.items(), key=lambda item: item[0])
        ],
        "live_processes": read_live_processes() if include_live_processes else [],
        "notes": notes,
    }


def data_for_script(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def load_template(path: Path = TEMPLATE_PATH) -> str:
    return path.read_text(encoding="utf-8")


def render_dashboard_html(data: dict[str, Any], *, template: str | None = None) -> str:
    dashboard_template = template if template is not None else load_template()
    if DASHBOARD_DATA_PLACEHOLDER not in dashboard_template:
        raise ValueError(f"Dashboard template must contain {DASHBOARD_DATA_PLACEHOLDER!r}.")
    return dashboard_template.replace(DASHBOARD_DATA_PLACEHOLDER, data_for_script(data), 1)


def write_dashboard(output: Path, data: dict[str, Any]) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_dashboard_html(data), encoding="utf-8")
    return output


def serve_file(output: Path, port: int) -> None:
    output = output.resolve()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(output.parent))
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        url = f"http://127.0.0.1:{port}/{urllib.parse.quote(output.name)}"
        console.print(f"[green]Serving ADK token dashboard:[/] {url}")
        httpd.serve_forever()


@app.command()
def main(
    db: Annotated[
        list[Path] | None,
        typer.Option("--db", help="ADK session.db file. Repeatable. Defaults to current-checkout discovery."),
    ] = None,
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="HTML dashboard output path."),
    ] = DEFAULT_OUTPUT,
    serve: Annotated[
        bool,
        typer.Option("--serve", help="Serve the generated dashboard after writing it."),
    ] = False,
    port: Annotated[int, typer.Option("--port", help="Port used with --serve.")] = 8046,
    no_live_processes: Annotated[
        bool,
        typer.Option("--no-live-processes", help="Skip ps-based live ADK Web process detection."),
    ] = False,
    refresh_pricing: Annotated[
        bool,
        typer.Option(
            "--refresh-pricing/--no-refresh-pricing",
            help="Fetch the latest LiteLLM model pricing map before generating the dashboard.",
        ),
    ] = True,
    pricing_cache: Annotated[
        Path,
        typer.Option("--pricing-cache", help="Local normalized model-pricing cache path."),
    ] = DEFAULT_PRICING_CACHE,
    pricing_url: Annotated[
        str,
        typer.Option("--pricing-url", help="Machine-readable LiteLLM pricing JSON URL."),
    ] = LITELLM_PRICING_URL,
) -> None:
    """Build an HTML dashboard from ADK session SQLite stores."""
    pricing_catalog = load_pricing_catalog(
        pricing_cache,
        source_url=pricing_url,
        refresh=refresh_pricing,
    )
    data = build_dashboard_data(
        db,
        include_live_processes=not no_live_processes,
        pricing_catalog=pricing_catalog,
    )
    written = write_dashboard(output, data)
    console.print(
        f"[green]Wrote[/] {written} from {len(data['source_dbs'])} DB(s), "
        f"{data['session_count']} session(s), {data['totals']['llm_events']} LLM event(s)."
    )
    console.print(
        f"[green]Pricing:[/] {data['cost_pricing_source']} "
        f"({len(data['cost_rates_usd_per_1m'])} observed priced model(s), "
        f"{len(data['cost_unpriced_models'])} unpriced)."
    )
    if serve:
        serve_file(written, port)


if __name__ == "__main__":
    try:
        app()
    except KeyboardInterrupt:
        console.print("[yellow]Stopped dashboard server.[/]")
        sys.exit(130)

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "adk_token_dashboard.py"
SPEC = importlib.util.spec_from_file_location("adk_token_dashboard", MODULE_PATH)
assert SPEC is not None
adk_token_dashboard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(adk_token_dashboard)


def _make_session_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            create table sessions (
                app_name text not null,
                user_id text not null,
                id text not null,
                state text not null,
                create_time real not null,
                update_time real not null,
                primary key (app_name, user_id, id)
            );
            create table events (
                id text not null,
                app_name text not null,
                user_id text not null,
                session_id text not null,
                invocation_id text not null,
                timestamp real not null,
                event_data text not null,
                primary key (app_name, user_id, session_id, id)
            );
            """
        )
        conn.execute(
            "insert into sessions values (?, ?, ?, ?, ?, ?)",
            ("job_scraper", "user", "session-1", "{}", 100.0, 101.0),
        )
        conn.execute(
            "insert into events values (?, ?, ?, ?, ?, ?, ?)",
            (
                "event-1",
                "job_scraper",
                "user",
                "session-1",
                "invocation-1",
                100.0,
                json.dumps(
                    {
                        "author": "job_listing_scout",
                        "model_version": "gpt-test",
                        "usage_metadata": {
                            "prompt_token_count": 100,
                            "cached_content_token_count": 40,
                            "candidates_token_count": 9,
                            "thoughts_token_count": 3,
                            "total_token_count": 109,
                        },
                    }
                ),
            ),
        )
        conn.execute(
            "insert into events values (?, ?, ?, ?, ?, ?, ?)",
            (
                "event-2",
                "job_scraper",
                "user",
                "session-1",
                "invocation-1",
                101.0,
                json.dumps(
                    {
                        "author": "job_listing_scout",
                        "modelVersion": "gemini-test",
                        "usageMetadata": {
                            "promptTokenCount": 50,
                            "cachedContentTokenCount": 10,
                            "candidatesTokenCount": 7,
                            "totalTokenCount": 57,
                        },
                    }
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_build_dashboard_data_aggregates_runtime_model_and_session_tokens(tmp_path: Path) -> None:
    db_path = tmp_path / "src" / "job_scraper" / ".adk" / "session.db"
    _make_session_db(db_path)
    pricing_catalog = {
        "source": "test",
        "source_url": "memory://pricing",
        "fetched_at": "2026-05-22T00:00:00+00:00",
        "rates_usd_per_1m": {
            "gpt-test": {"input": 1.0, "cached_input": 0.1, "output": 2.0, "reasoning_output": 2.0},
            "gemini-test": {"input": 3.0, "cached_input": 0.3, "output": 4.0, "reasoning_output": 4.0},
        },
    }

    data = adk_token_dashboard.build_dashboard_data(
        [db_path],
        project_root=tmp_path,
        include_live_processes=False,
        pricing_catalog=pricing_catalog,
    )

    assert data["runtime_count"] == 1
    assert data["session_count"] == 1
    assert data["totals"]["llm_events"] == 2
    assert data["totals"]["prompt_tokens"] == 150
    assert data["totals"]["cached_tokens"] == 50
    assert data["totals"]["noncached_prompt_tokens"] == 100
    assert data["totals"]["output_tokens"] == 16
    assert data["totals"]["thoughts_tokens"] == 3
    assert data["totals"]["total_tokens"] == 166
    assert data["runtimes"][0]["runtime"] == "current:src/job_scraper"
    assert data["runtime_quantiles"][0]["label"] == "Total tokens"
    assert data["runtime_quantiles"][0]["q1"] == 166
    assert data["runtime_quantiles"][0]["median"] == 166
    assert data["runtime_quantiles"][0]["q3"] == 166
    assert {row["model"] for row in data["models"]} == {"gpt-test", "gemini-test"}
    assert data["sessions"][0]["models"] == ["gemini-test", "gpt-test"]
    assert data["totals"]["total_cost"] == pytest.approx(0.000239)
    assert data["cost_pricing_source"] == "test"
    assert data["cost_unpriced_models"] == []


def test_build_dashboard_data_skips_sqlite_files_without_adk_events(tmp_path: Path) -> None:
    valid_db_path = tmp_path / "src" / "job_scraper" / ".adk" / "session.db"
    invalid_db_path = tmp_path / "src" / ".adk" / "session.db"
    _make_session_db(valid_db_path)
    invalid_db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(invalid_db_path)
    try:
        conn.execute("create table not_adk (id text)")
        conn.commit()
    finally:
        conn.close()

    data = adk_token_dashboard.build_dashboard_data(
        [valid_db_path, invalid_db_path],
        project_root=tmp_path,
        include_live_processes=False,
    )

    assert data["source_dbs"] == ["src/job_scraper/.adk/session.db"]
    assert data["skipped_dbs"] == ["src/.adk/session.db"]
    assert data["totals"]["llm_events"] == 2


def test_default_db_discovery_stays_inside_current_checkout(tmp_path: Path) -> None:
    current_checkout = tmp_path / "worktree"
    sibling_checkout = tmp_path / "sibling"
    current_db = current_checkout / "src" / "job_scraper" / ".adk" / "session.db"
    sibling_db = sibling_checkout / "src" / ".adk" / "session.db"
    for path in (current_db, sibling_db):
        _make_session_db(path)

    discovered = adk_token_dashboard.iter_default_db_paths(current_checkout)

    assert discovered == [current_db.resolve()]
    assert adk_token_dashboard.runtime_label(current_db, current_checkout) == "current:src/job_scraper"


def test_quantile_uses_linear_interpolation() -> None:
    values = [0, 100, 200, 300]

    assert adk_token_dashboard.quantile(values, 0.25) == 75
    assert adk_token_dashboard.quantile(values, 0.5) == 150
    assert adk_token_dashboard.quantile(values, 0.75) == 225


def test_usage_costs_use_local_model_rate_table() -> None:
    usage = {
        "prompt_token_count": 100,
        "cached_content_token_count": 40,
        "candidates_token_count": 9,
        "thoughts_token_count": 3,
    }

    pricing_rates = {
        "gpt-5.4-mini": {
            "input": 0.75,
            "cached_input": 0.075,
            "output": 4.5,
            "reasoning_output": 4.5,
        }
    }

    costs = adk_token_dashboard.usage_costs(
        usage,
        "gpt-5.4-mini-2026-03-17",
        pricing_rates=pricing_rates,
    )

    assert costs["cached_cost"] == pytest.approx(0.000003)
    assert costs["noncached_prompt_cost"] == pytest.approx(0.000045)
    assert costs["prompt_cost"] == pytest.approx(0.000048)
    assert costs["output_cost"] == pytest.approx(0.0000405)
    assert costs["thoughts_cost"] == pytest.approx(0.0000135)
    assert costs["total_cost"] == pytest.approx(0.000102)


def test_normalize_litellm_pricing_converts_per_token_to_per_million() -> None:
    raw = {
        "gpt-5.4-mini-2026-03-17": {
            "input_cost_per_token": 0.00000075,
            "cache_read_input_token_cost": 0.000000075,
            "output_cost_per_token": 0.0000045,
        },
        "metadata": "ignored",
    }

    rates = adk_token_dashboard.normalize_litellm_pricing(raw)

    assert rates["gpt-5.4-mini-2026-03-17"] == {
        "input": 0.75,
        "cached_input": 0.075,
        "output": 4.5,
        "reasoning_output": 4.5,
    }


def test_render_dashboard_html_escapes_script_breakout() -> None:
    data = {
        "generated_at": "2026-05-20T00:00:00+00:00",
        "project_root": "</script><span>not markup</span>",
        "source_dbs": [],
        "skipped_dbs": [],
        "runtime_count": 0,
        "session_count": 0,
        "totals": adk_token_dashboard.empty_counter(),
        "runtimes": [],
        "runtime_quantiles": [],
        "models": [],
        "sessions": [],
        "timeline": [],
        "live_processes": [],
        "notes": [],
    }

    rendered = adk_token_dashboard.render_dashboard_html(
        data,
        template="<script>window.data=__TOKEN_DASHBOARD_DATA__;</script>",
    )

    assert "</script><span>not markup</span>" not in rendered
    assert "\\u003c/script>" in rendered


def test_template_keeps_quantile_tooltip_targets_hoverable() -> None:
    template = MODULE_PATH.with_suffix(".template.html").read_text()

    assert ".quantile-marker {\n        position: absolute;" in template
    assert "display: block;" in template
    assert ".chart-track.hover-host {\n        position: relative;" in template
    assert ".quantile-marker.hover-host {\n        position: absolute;" in template


def test_template_hides_redundant_model_cost_panel_for_single_model() -> None:
    template = MODULE_PATH.with_suffix(".template.html").read_text()

    assert "function renderModelCostPanel" in template
    assert "if ((dashboard.models || []).length <= 1) return \"\";" in template
    assert "Single model:" in template


def test_template_uses_plain_language_for_cost_breakdown() -> None:
    template = MODULE_PATH.with_suffix(".template.html").read_text()

    assert "input cost" in template
    assert "output cost" in template
    assert "Input cost is tokens sent to the model" in template
    assert "completion_cost" not in template


def test_template_keeps_session_scope_across_tabs() -> None:
    template = MODULE_PATH.with_suffix(".template.html").read_text()

    assert "function scopedDashboardData" in template
    assert '["sessions", "Sessions"],\n        ["overview", "Metrics"]' in template
    assert 'view: "sessions"' in template
    assert "renderOverview(dashboard)" in template
    assert "renderRuntimes(dashboard)" in template
    assert "renderTimeline(dashboard)" in template
    assert "data-clear-session-scope" in template
    assert "Metrics, Runtimes, and Timeline are filtered to this session" in template
    assert 'state.view = "overview";' in template


def test_template_exposes_expandable_chatcompletion_details_in_metrics() -> None:
    template = MODULE_PATH.with_suffix(".template.html").read_text()

    assert "ChatCompletion Details" in template
    assert "Click a ChatCompletion row to expand" in template
    assert "${spanDetailsTable(dashboard.runtimes)}" in template
    assert 'state.view = "sessions";' not in template


def test_template_makes_detail_tables_collapsible() -> None:
    template = MODULE_PATH.with_suffix(".template.html").read_text()

    assert 'function collapsibleTable' in template
    assert 'class="table-details"' in template
    assert 'title: "Session table"' in template
    assert 'title: "ChatCompletion table"' in template
    assert 'title: "Process table"' in template
    assert 'open: Boolean(state.selectedSpanId)' in template


def test_template_makes_cost_and_row_panels_collapsible() -> None:
    template = MODULE_PATH.with_suffix(".template.html").read_text()

    assert 'function collapsiblePanel' in template
    assert 'title: "Cost"' in template
    assert 'title: "Top models by cost"' in template
    assert 'title: "Token usage"' in template
    assert 'title: "Top models by tokens"' in template
    assert 'title: dashboard.scope_active ? "Selected Session Timeline" : "Daily Token Timeline"' in template


def test_template_uses_responsive_dashboard_sizing() -> None:
    template = MODULE_PATH.with_suffix(".template.html").read_text()

    assert "repeat(auto-fit, minmax(min(180px, 100%), 1fr))" in template
    assert "repeat(auto-fit, minmax(min(430px, 100%), 1fr))" in template
    assert "@media (max-width: 900px)" in template
    assert ".chart-label strong,\n        .chart-note" in template
    assert ".collapsible-content {\n          max-height: none;" in template
    assert ".shell > * {\n        min-width: 0;" in template
    assert "max-width: 100%;" in template
    assert "overflow-wrap: anywhere;" in template
    assert ".table-details {\n        min-width: 0;\n        overflow: hidden;" in template
    assert "overflow-x: hidden;" in template
    assert "max-width: min(280px, calc(100vw - 32px));" in template


def test_template_keeps_quantile_explanations_in_tooltips_only() -> None:
    template = MODULE_PATH.with_suffix(".template.html").read_text()

    assert "shared token scale max" in template
    assert "Hover markers for Q1, median, and Q3 values." not in template
    assert "row max" not in template


def test_template_annotates_session_mix_colors() -> None:
    template = MODULE_PATH.with_suffix(".template.html").read_text()

    assert "Mix colors" in template
    assert "cached input" in template
    assert "non-cached input" in template
    assert "reasoning" in template
    assert "cached input / non-cached input / output / reasoning" in template


def test_template_uses_adk_observability_branding() -> None:
    template = MODULE_PATH.with_suffix(".template.html").read_text()

    assert "ADK Observability" in template
    assert "<h3>Local OSS</h3>" not in template

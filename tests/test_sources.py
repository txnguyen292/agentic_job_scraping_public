from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from job_scraper import sources


class FakeFetcher:
    last_kwargs = {}

    @classmethod
    def get(cls, url: str, **kwargs):
        cls.last_kwargs = {"url": url, **kwargs}
        return SimpleNamespace(status=200, reason="OK", body=b"<html>static</html>", encoding="utf-8")


class FakeDynamicFetcher:
    last_kwargs = {}
    caller_thread_id = None

    @classmethod
    def fetch(cls, url: str, **kwargs):
        cls.last_kwargs = {"url": url, **kwargs}
        cls.caller_thread_id = threading.get_ident()
        return SimpleNamespace(status=200, reason="OK", body=b"<html>dynamic</html>", encoding="utf-8")


def test_fetch_page_uses_scrapling_fetcher(monkeypatch) -> None:
    monkeypatch.setattr(sources, "_load_scrapling_fetchers", lambda: (FakeFetcher, FakeDynamicFetcher))

    content = sources.fetch_page("https://example.com/jobs", timeout=7)

    assert content == "<html>static</html>"
    assert FakeFetcher.last_kwargs["url"] == "https://example.com/jobs"
    assert FakeFetcher.last_kwargs["timeout"] == 7
    assert FakeFetcher.last_kwargs["headers"]["User-Agent"] == sources.USER_AGENT


def test_render_page_uses_scrapling_dynamic_fetcher(monkeypatch) -> None:
    monkeypatch.setattr(sources, "_load_scrapling_fetchers", lambda: (FakeFetcher, FakeDynamicFetcher))
    current_thread_id = threading.get_ident()

    content = sources.render_page("https://example.com/jobs", timeout=7)

    assert content == "<html>dynamic</html>"
    assert FakeDynamicFetcher.caller_thread_id != current_thread_id
    assert FakeDynamicFetcher.last_kwargs["url"] == "https://example.com/jobs"
    assert FakeDynamicFetcher.last_kwargs["timeout"] == 7000
    assert FakeDynamicFetcher.last_kwargs["headless"] is True
    assert FakeDynamicFetcher.last_kwargs["network_idle"] is True


def test_scrapling_response_text_rejects_http_errors() -> None:
    response = SimpleNamespace(status=404, reason="Not Found", body=b"missing", encoding="utf-8")

    try:
        sources._scrapling_response_text(response)
    except RuntimeError as exc:
        assert "HTTP 404" in str(exc)
    else:
        raise AssertionError("Expected HTTP errors to raise RuntimeError")


def test_fixture_crawl_serializes_metadata_json_deterministically() -> None:
    greenhouse_source, lever_source = sources.load_sources("seeds/demo_sources.json")

    greenhouse_job = next(iter(sources.crawl_source(greenhouse_source)))
    lever_job = next(iter(sources.crawl_source(lever_source)))

    assert greenhouse_job.metadata_json == json.dumps(
        {
            "raw_id": 1001,
            "departments": ["Applied AI"],
            "offices": ["Remote"],
        },
        sort_keys=True,
    )
    assert lever_job.metadata_json == json.dumps(
        {
            "raw_id": "lv-2001",
            "workplaceType": "",
            "categories": {
                "team": "Autonomy",
                "location": "Taipei, Taiwan",
                "commitment": "Full-time",
            },
        },
        sort_keys=True,
    )


def test_metadata_json_accepts_json_dump_customization() -> None:
    rendered = sources._metadata_json({"b": 1, "a": 2}, sort_keys=False, separators=(",", ":"))

    assert rendered == '{"b":1,"a":2}'

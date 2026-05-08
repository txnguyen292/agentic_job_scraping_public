from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from lxml import html


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
HTML_FIXTURE = FIXTURE_DIR / "itviec_ai_engineer_ha_noi.html"
EXPECTED_FIXTURE = FIXTURE_DIR / "itviec_ai_engineer_ha_noi.expected.json"


def canonical_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def load_expected_itviec_output() -> dict:
    return json.loads(EXPECTED_FIXTURE.read_text(encoding="utf-8"))


def assert_matches_expected_itviec_output(actual: dict) -> None:
    """Reusable assertion for the future sandbox workflow output."""
    expected = load_expected_itviec_output()

    actual_jobs = actual["jobs"]
    expected_jobs = expected["jobs"]
    assert len(actual_jobs) == len(expected_jobs)
    assert actual["crawl"]["discovered_count"] == expected["crawl"]["discovered_count"]
    assert actual["crawl"]["candidate_count"] == expected["crawl"]["candidate_count"]

    actual_by_url = {canonical_url(job["job_url"]): job for job in actual_jobs}
    expected_by_url = {job["job_url"]: job for job in expected_jobs}
    assert set(actual_by_url) == set(expected_by_url)

    for job_url, expected_job in expected_by_url.items():
        actual_job = actual_by_url[job_url]
        assert actual_job["title"] == expected_job["title"]
        assert actual_job["company_name"] == expected_job["company_name"]
        assert actual_job["location_raw"] == expected_job["location_raw"]
        assert actual_job["salary_raw"] == expected_job["salary_raw"]
        assert set(actual_job["tags"]) == set(expected_job["tags"])


def test_itviec_expected_output_matches_frozen_html_job_cards() -> None:
    fixture_html = HTML_FIXTURE.read_text(encoding="utf-8")
    expected = load_expected_itviec_output()
    doc = html.fromstring(fixture_html)

    cards = doc.cssselect("div.job-card")
    assert len(cards) == 20
    assert len(expected["jobs"]) == 20
    assert expected["crawl"] == {
        "discovered_count": 20,
        "candidate_count": 20,
        "relevant_count": 20,
        "blocked": False,
        "blocker": "",
    }

    card_slugs = {
        card.get("data-search--job-selection-job-slug-value")
        for card in cards
    }
    expected_slugs = {
        Path(job["job_url"]).name
        for job in expected["jobs"]
    }
    assert card_slugs == expected_slugs


def test_itviec_expected_output_matches_itemlist_json_ld() -> None:
    fixture_html = HTML_FIXTURE.read_text(encoding="utf-8")
    expected = load_expected_itviec_output()
    doc = html.fromstring(fixture_html)

    itemlist_urls: list[str] = []
    for script in doc.xpath('//script[@type="application/ld+json"]/text()'):
        if '"@type":"ItemList"' not in script:
            continue
        payload = json.loads(script)
        itemlist_urls = [
            item["url"]
            for item in payload["itemListElement"]
        ]
        break

    assert itemlist_urls
    assert itemlist_urls == [job["job_url"] for job in expected["jobs"]]
    assert expected["metadata"]["itemlist_urls_match_cards"] is True


def test_itviec_expected_output_has_required_fields_and_bounded_evidence() -> None:
    expected = load_expected_itviec_output()
    required = {"title", "company_name", "job_url", "location_raw", "salary_raw", "tags", "evidence"}

    for job in expected["jobs"]:
        assert required <= set(job)
        assert job["title"]
        assert job["company_name"]
        assert job["job_url"].startswith("https://itviec.com/it-jobs/")
        assert job["location_raw"]
        assert job["tags"]
        assert job["evidence"]
        assert job["evidence"][0]["file"] == "page.html"
        assert len(job["evidence"][0]["text"]) <= 300


def test_itviec_future_workflow_output_assertion_accepts_expected_fixture() -> None:
    assert_matches_expected_itviec_output(load_expected_itviec_output())

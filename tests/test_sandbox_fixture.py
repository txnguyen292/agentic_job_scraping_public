from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
HTML_FIXTURE = FIXTURE_DIR / "static_job_board.html"
EXPECTED_FIXTURE = FIXTURE_DIR / "static_job_board.expected.json"


class JobCardCounter(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.job_cards = 0
        self.next_links = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        classes = set((attr_map.get("class") or "").split())
        if tag == "article" and "job-card" in classes:
            self.job_cards += 1
        if tag == "a" and attr_map.get("rel") == "next":
            self.next_links += 1


def test_static_job_board_fixture_matches_expected_extraction() -> None:
    html = HTML_FIXTURE.read_text(encoding="utf-8")
    expected = json.loads(EXPECTED_FIXTURE.read_text(encoding="utf-8"))

    parser = JobCardCounter()
    parser.feed(html)

    assert parser.job_cards == expected["crawl"]["discovered_count"]
    assert len(expected["jobs"]) == expected["crawl"]["candidate_count"]
    assert expected["crawl"]["relevant_count"] == 2
    assert parser.next_links == 1

    required_fields = {"title", "company_name", "job_url", "location_raw", "description_text"}
    for job in expected["jobs"]:
        assert required_fields <= job.keys()
        assert job["title"]
        assert job["job_url"].startswith("https://example.com/jobs/")
        assert job["evidence"][0]["file"] == "page.html"
        assert len(job["evidence"][0]["text"]) <= 300


def test_static_job_board_expected_output_has_reference_selectors() -> None:
    expected = json.loads(EXPECTED_FIXTURE.read_text(encoding="utf-8"))

    assert expected["selectors"] == {
        "job_card": "article.job-card",
        "title": "a.job-title",
        "company": ".company",
        "location": ".location",
        "salary": ".salary",
        "tags": ".tags li",
        "description": ".summary",
        "next_page": "a[rel='next']",
    }

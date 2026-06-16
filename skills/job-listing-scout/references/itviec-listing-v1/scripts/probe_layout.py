from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup


def clean(text: str) -> str:
    return " ".join((text or "").split())


def text_or_empty(node: Any) -> str:
    return clean(node.get_text(" ", strip=True)) if node else ""


def probe(html_path: Path) -> dict[str, Any]:
    html = html_path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(".job-card")
    url_attrs = soup.select("[data-search--job-selection-job-url-value]")
    title_targets = soup.select('[data-search--job-selection-target="jobTitle"]')
    marker_counts = {
        "job_card": len(cards),
        "job_url_attr": len(url_attrs),
        "job_title_target": len(title_targets),
        "pagination_target_job_card": html.count('data-search--pagination-target="jobCard"'),
    }
    samples = []
    for index, card in enumerate(cards[:3]):
        samples.append(
            {
                "index": index,
                "title": text_or_empty(card.select_one('[data-search--job-selection-target="jobTitle"]')),
                "company": text_or_empty(card.select_one("a.text-rich-grey[href^='/companies/']")),
                "job_url_value": card.get("data-search--job-selection-job-url-value", ""),
                "skill_tag_count": len(card.select('a[href*="click_source=Skill+tag"]')),
            }
        )
    can_reuse = (
        len(cards) > 0
        and len(url_attrs) == len(cards)
        and len(title_targets) == len(cards)
        and all(sample["title"] and sample["company"] and sample["job_url_value"] for sample in samples)
    )
    return {
        "status": "match" if can_reuse else "no_match",
        "package_id": "itviec-listing-v1",
        "html_path": str(html_path),
        "card_count": len(cards),
        "can_reuse": can_reuse,
        "marker_counts": marker_counts,
        "samples": samples,
        "required_next": (
            "Run references/job-listing-scout/itviec-listing-v1/scripts/extractor.py"
            if can_reuse
            else "Inspect the page and create or patch a run-scoped extractor."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe whether an HTML page matches ITviec listing v1.")
    parser.add_argument("--html", default="page.html", help="HTML file to probe. Defaults to page.html.")
    args = parser.parse_args()
    print(json.dumps(probe(Path(args.html)), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

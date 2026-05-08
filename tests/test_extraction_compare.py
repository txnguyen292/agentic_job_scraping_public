from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from job_scraper.cli import app
from job_scraper.utils.extraction_compare import compare_job_extraction, load_json_file


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
EXPECTED = FIXTURE_DIR / "itviec_ai_engineer_ha_noi.expected.json"
RUNNER = CliRunner()


def test_compare_job_extraction_passes_verified_itviec_expected_output() -> None:
    expected = load_json_file(EXPECTED)

    result = compare_job_extraction(actual=expected, expected=expected)

    assert result["status"] == "pass"
    assert result["expected_job_count"] == 20
    assert result["actual_job_count"] == 20
    assert result["missing_urls"] == []
    assert result["field_mismatches"] == []


def test_compare_job_extraction_reports_field_mismatch() -> None:
    expected = load_json_file(EXPECTED)
    actual = json.loads(json.dumps(expected))
    actual["jobs"][0]["company_name"] = "Wrong Company"

    result = compare_job_extraction(actual=actual, expected=expected)

    assert result["status"] == "fail"
    assert result["field_mismatches"] == [
        {
            "job_url": expected["jobs"][0]["job_url"],
            "field": "company_name",
            "expected": expected["jobs"][0]["company_name"],
            "actual": "Wrong Company",
        }
    ]


def test_compare_extraction_cli_accepts_expected_fixture(tmp_path: Path) -> None:
    actual = tmp_path / "actual.json"
    actual.write_text(EXPECTED.read_text(encoding="utf-8"), encoding="utf-8")

    result = RUNNER.invoke(app, ["compare-extraction", str(actual), str(EXPECTED), "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"

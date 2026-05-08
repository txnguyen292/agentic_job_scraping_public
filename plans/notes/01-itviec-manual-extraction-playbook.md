# ITviec Manual Extraction Playbook

This note documents how the frozen ITviec expected output was created manually. It is the target behavior the future sandbox-page-analyst worker should reproduce from `page.html`.

Source URL:

```text
https://itviec.com/it-jobs/ai-engineer/ha-noi?job_selected=ai-developer-engineer-consultant-python-llm-nlp-switch-supply-pty-ltd-2549
```

Frozen fixture:

```text
tests/fixtures/itviec_ai_engineer_ha_noi.html
```

Expected output:

```text
tests/fixtures/itviec_ai_engineer_ha_noi.expected.json
```

## Manual Extraction Steps

1. Fetch the live ITviec page once with the existing Scrapling fetcher.
2. Save the full HTML as a frozen fixture.
3. Inspect the fixture locally, not through an LLM context.
4. Identify repeated job cards with `div.job-card`.
5. Count the cards. The frozen page has 20 page-1 job cards.
6. Extract the job title from `[data-search--job-selection-target="jobTitle"]`.
7. Extract the job URL from the title node's `data-url`.
8. Canonicalize job URLs by removing query parameters such as `lab_feature=preview_jd_page`.
9. Extract company name from the employer link matching `a[href^="/companies/"]`.
10. Extract salary text from `.salary`.
11. Extract skill tags from `[data-responsive-tag-list-target="tag"]` and skill-tag links.
12. Extract location/work-mode text from the card text, especially patterns such as `Remote ... Ha Noi` and `At office Ha Noi`.
13. Extract relative posted age from card text, e.g. `Posted 5 days ago`.
14. Build one candidate job object per card.
15. Add short evidence with `file`, `locator`, and a bounded text summary.
16. Extract selectors that made the extraction repeatable.
17. Cross-check extracted job URLs against the page's `ItemList` JSON-LD.
18. Add warnings for known limitations:
    - salary may be sign-in gated
    - descriptions are not available in list cards
    - page 1 has pagination
    - the fixture is frozen and should not be treated as a live-site assertion
19. Write the expected output as `job_extraction` JSON.
20. Add tests that validate the expected output against the frozen HTML.

## Expected Signals

- Page title: `ai engineer Jobs in Ha Noi | ITviec`
- Job card selector: `div.job-card`
- Card count: 20
- Pagination: present
- JSON-LD `ItemList`: present
- JSON-LD URL count: 20
- JSON-LD URLs match the extracted card URLs after canonicalization.

## Expected Selectors

```json
{
  "job_card": "div.job-card[data-search--pagination-target=\"jobCard\"]",
  "title": "[data-search--job-selection-target=\"jobTitle\"]",
  "company": "a[href^=\"/companies/\"]",
  "salary": ".salary",
  "tags": "[data-responsive-tag-list-target=\"tag\"]",
  "next_page": "a[rel=\"next\"]"
}
```

## Sandbox Protocol Check

The proposed sandbox workflow covers the manual extraction, but should make these details explicit:

- `page_profile.json` should include card count, JSON-LD ItemList detection, pagination detection, and blocked-page signals.
- `extraction_strategy.json` should choose `static-html-job-board.md` plus `json-ld-job-postings.md` and `paginated-listing-pages.md`.
- `extraction_strategy.json` should include the concrete selectors listed above.
- `extraction_playbook.md` should describe the step-by-step extraction recipe before candidates are extracted.
- `candidates.json` should include all 20 page-1 cards.
- `validation.json` should cross-check candidate URLs against JSON-LD ItemList URLs.
- `validation.json` should confirm evidence snippets are bounded.
- `validation.json` should warn that descriptions are unavailable in list cards.
- `validation.json` should warn that pagination exists and page 1 is not a full crawl.
- final `job_extraction` output should pass `assert_matches_expected_itviec_output(actual)`.

## Future Test Use

When the sandbox workflow exists, feed the frozen fixture to the sandbox as `page.html` and assert:

```python
from tests.test_itviec_expected_fixture import assert_matches_expected_itviec_output

assert_matches_expected_itviec_output(actual)
```

This test proves the workflow can reproduce the known-good extraction from a large real website fixture without sending full HTML through the main ADK context.

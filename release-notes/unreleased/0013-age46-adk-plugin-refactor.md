## Changed

- Split the ADK plugin implementation into focused modules while preserving the `job_scraper.adk_plugins` compatibility facade and sandbox guard behavior.
- Centralize ADK runtime payload keys, statuses, guardrail codes, and session-context key groups in `job_scraper.runtime_payload` so compaction and output-gate code share one typed contract.
- Include the ADK eval extra in project dependencies so live ADK eval checks can run from a fresh checkout.

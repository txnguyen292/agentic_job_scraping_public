from __future__ import annotations

import os


PROJECT_SANDBOX_IMAGE = "job-scraper-sandbox:py313"
DEFAULT_SANDBOX_IMAGE = os.getenv("JOB_SCRAPER_SANDBOX_IMAGE", PROJECT_SANDBOX_IMAGE)

APPROVED_SANDBOX_PARSER_IMPORTS = (
    "bs4",
    "lxml",
    "parsel",
    "typer",
    "rich",
    "loguru",
)

APPROVED_SANDBOX_TOOLS = (
    "jq",
    "rg",
)

from __future__ import annotations

from typing import Any

from sandbox_page_analyst.runtime import run_generic_sandbox_agent


def run_page_analysis(
    task: str,
    variables: dict[str, Any] | None = None,
    workspace_files: list[dict[str, Any]] | None = None,
    output_schema: str = "job_extraction",
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the standalone sandbox page analyst against mounted workspace files."""
    return run_generic_sandbox_agent(
        task=task,
        variables=variables or {},
        workspace_files=workspace_files or [],
        output_schema=output_schema,
        policy=policy or {},
    )

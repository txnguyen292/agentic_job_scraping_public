from __future__ import annotations

from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SANDBOX_SKILL_DIR = PROJECT_ROOT / "skills" / "sandbox-page-analyst"


def load_sandbox_skill_metadata(skill_dir: Path = SANDBOX_SKILL_DIR) -> dict[str, str]:
    skill_path = skill_dir / "SKILL.md"
    if not skill_path.exists():
        raise FileNotFoundError(f"Sandbox skill is missing: {skill_path}")

    skill_text = skill_path.read_text(encoding="utf-8")
    if not skill_text.startswith("---"):
        raise ValueError(f"Sandbox skill has no frontmatter: {skill_path}")
    frontmatter = skill_text.split("---", 2)[1]
    metadata: dict[str, str] = {}
    for line in frontmatter.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"')

    if not metadata.get("name") or not metadata.get("description"):
        raise ValueError(f"Sandbox skill frontmatter must include name and description: {skill_path}")
    return metadata


def build_sandbox_capabilities(policy: Any) -> list[Any]:
    try:
        from agents.sandbox.capabilities.compaction import Compaction
        from agents.sandbox.capabilities.filesystem import Filesystem
        from agents.sandbox.capabilities.shell import Shell
        from agents.sandbox.capabilities.skills import LocalDirLazySkillSource, Skills
        from agents.sandbox.entries import LocalDir
    except ImportError as exc:
        raise RuntimeError(
            "OpenAI Agents SDK sandbox dependencies are not installed. Run `uv sync` after "
            "adding openai-agents and docker dependencies."
        ) from exc

    capabilities: list[Any] = [Shell(), Filesystem(), Compaction()]
    if policy.use_sandbox_skill:
        load_sandbox_skill_metadata()
        capabilities.append(
            Skills(
                lazy_from=LocalDirLazySkillSource(source=LocalDir(src=Path("skills"))),
                skills_path=policy.skills_path,
            )
        )
    return capabilities


def build_sandbox_page_analyst_agent(*, output_schema: str, policy: Any, manifest: Any) -> Any:
    try:
        from agents import ModelSettings
        from agents.model_settings import Reasoning
        from agents.sandbox.sandbox_agent import SandboxAgent
    except ImportError as exc:
        raise RuntimeError(
            "OpenAI Agents SDK sandbox dependencies are not installed. Run `uv sync` after "
            "adding openai-agents and docker dependencies."
        ) from exc

    return SandboxAgent(
        name="sandbox_page_analyst",
        model=policy.model,
        model_settings=ModelSettings(
            reasoning=Reasoning(effort=policy.reasoning_effort),
            include_usage=True,
        ),
        instructions=sandbox_instructions(output_schema, policy),
        default_manifest=manifest,
        capabilities=build_sandbox_capabilities(policy),
    )


def sandbox_instructions(output_schema: str, policy: Any) -> str:
    skill_name = policy.skill_name
    return (
        "You are a generic sandbox worker. Use only the mounted workspace files, variables.json, "
        "the filesystem, and shell. Do not browse the web or fetch remote URLs. "
        "Host-mediated LLM calls are allowed and required for this workflow. "
        "You may use normal model reasoning across turns; the no-network restriction applies to "
        "sandbox terminal/container egress, not host-mediated LLM calls. "
        f"First call load_skill for {skill_name!r}, then follow that skill's SKILL.md and references/protocol.md. "
        "Write required protocol files under output/: page_profile.json, extraction_strategy.json, "
        "candidates.json, and validation.json. Return only compact final JSON with status, output_schema, "
        "summary, protocol, result, artifacts, and error. The protocol object must contain the output file "
        "paths and SHA-256 hashes. Do not include raw HTML, terminal transcripts, full stdout/stderr, or "
        f"large debug dumps. The requested output_schema is {output_schema!r}."
    )

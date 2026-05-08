from __future__ import annotations

import fnmatch
import json
import re
import shutil
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import typer
from loguru import logger
from rich.console import Console
from rich.table import Table


DEFAULT_CONFIG_PATH = "public_export.toml"
app = typer.Typer(add_completion=False, no_args_is_help=True, help="Prepare sanitized public repo snapshots.")
console = Console()


@dataclass(frozen=True)
class PublicExportConfig:
    name: str
    description: str
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    secret_patterns: tuple[str, ...]
    secret_scan_exclude: tuple[str, ...]

    @classmethod
    def from_file(cls, path: str | Path) -> "PublicExportConfig":
        config_path = Path(path)
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        export = data.get("export", {})
        verification = data.get("verification", {})
        include = tuple(str(item) for item in export.get("include", []))
        exclude = tuple(str(item) for item in export.get("exclude", []))
        forbidden_paths = tuple(str(item) for item in verification.get("forbidden_paths", []))
        secret_patterns = tuple(str(item) for item in verification.get("secret_patterns", []))
        secret_scan_exclude = tuple(str(item) for item in verification.get("secret_scan_exclude", []))
        config = cls(
            name=str(export.get("name", "public_export")),
            description=str(export.get("description", "")),
            include=include,
            exclude=exclude,
            forbidden_paths=forbidden_paths,
            secret_patterns=secret_patterns,
            secret_scan_exclude=secret_scan_exclude,
        )
        config.validate()
        return config

    def validate(self) -> None:
        if not self.include:
            raise ValueError("public export config must include at least one path pattern")
        for pattern in (*self.include, *self.exclude, *self.forbidden_paths, *self.secret_scan_exclude):
            _validate_relative_pattern(pattern)
        for pattern in self.secret_patterns:
            re.compile(pattern)


@dataclass(frozen=True)
class ExportPlan:
    source_root: Path
    files: tuple[Path, ...]

    @property
    def total_bytes(self) -> int:
        return sum((self.source_root / path).stat().st_size for path in self.files)


def _validate_relative_pattern(pattern: str) -> None:
    path = PurePosixPath(pattern)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"public export patterns must be relative and stay inside the repo: {pattern}")


def _to_posix_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _matches_pattern(relative_path: str, pattern: str) -> bool:
    posix_path = PurePosixPath(relative_path)
    return (
        fnmatch.fnmatchcase(relative_path, pattern)
        or fnmatch.fnmatchcase(f"/{relative_path}", pattern)
        or posix_path.match(pattern)
    )


def _is_excluded(relative_path: str, patterns: tuple[str, ...]) -> bool:
    return any(_matches_pattern(relative_path, pattern) for pattern in patterns)


def _iter_included_files(source_root: Path, config: PublicExportConfig) -> list[Path]:
    files: set[Path] = set()
    for pattern in config.include:
        for match in source_root.glob(pattern):
            if match.is_file():
                rel = Path(_to_posix_relative(match, source_root))
                if not _is_excluded(rel.as_posix(), config.exclude):
                    files.add(rel)
    return sorted(files, key=lambda item: item.as_posix())


def build_export_plan(source_root: str | Path, config: PublicExportConfig) -> ExportPlan:
    root = Path(source_root).resolve()
    if not root.is_dir():
        raise ValueError(f"source root does not exist or is not a directory: {root}")
    return ExportPlan(source_root=root, files=tuple(_iter_included_files(root, config)))


def _remove_destination_contents(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for child in destination.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def sync_public_tree(
    source_root: str | Path,
    destination: str | Path,
    config: PublicExportConfig,
    *,
    apply: bool = False,
    clean: bool = True,
) -> dict[str, Any]:
    plan = build_export_plan(source_root, config)
    destination_path = Path(destination).resolve()
    source_path = Path(source_root).resolve()
    if destination_path == source_path or source_path in destination_path.parents:
        raise ValueError("destination must be outside the source repo")

    if apply:
        if clean:
            _remove_destination_contents(destination_path)
        else:
            destination_path.mkdir(parents=True, exist_ok=True)
        for rel_path in plan.files:
            source_file = plan.source_root / rel_path
            target_file = destination_path / rel_path
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target_file)
        verification = verify_public_tree(destination_path, config)
    else:
        verification = {"valid": True, "issues": []}

    return {
        "name": config.name,
        "description": config.description,
        "source_root": str(plan.source_root),
        "destination": str(destination_path),
        "apply": apply,
        "clean": clean,
        "file_count": len(plan.files),
        "total_bytes": plan.total_bytes,
        "files": [path.as_posix() for path in plan.files],
        "verification": verification,
    }


def _iter_tree_files(root: Path) -> list[Path]:
    return sorted(
        (path for path in root.rglob("*") if path.is_file() and ".git" not in path.relative_to(root).parts),
        key=lambda item: item.as_posix(),
    )


def verify_public_tree(root: str | Path, config: PublicExportConfig) -> dict[str, Any]:
    tree_root = Path(root).resolve()
    issues: list[dict[str, str]] = []
    if not tree_root.exists():
        return {"valid": False, "issues": [{"type": "missing_tree", "path": str(tree_root)}]}

    for forbidden in config.forbidden_paths:
        candidate = tree_root / forbidden
        if candidate.exists():
            issues.append({"type": "forbidden_path", "path": forbidden})

    compiled_secret_patterns = [re.compile(pattern) for pattern in config.secret_patterns]
    for file_path in _iter_tree_files(tree_root):
        rel = _to_posix_relative(file_path, tree_root)
        if _is_excluded(rel, config.exclude):
            issues.append({"type": "excluded_file_present", "path": rel})
            continue
        if _is_excluded(rel, config.secret_scan_exclude):
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in compiled_secret_patterns:
            if pattern.search(text):
                issues.append({"type": "secret_pattern", "path": rel, "pattern": pattern.pattern})
                break

    return {"valid": not issues, "issues": issues}


def _configure_logging(verbose: bool) -> None:
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if verbose else "INFO", format="<level>{level: <8}</level> | {message}")


def _emit_json(payload: Any) -> None:
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=True))


def _render_plan(payload: dict[str, Any]) -> None:
    table = Table(title="Public Export Plan")
    table.add_column("Field", style="cyan")
    table.add_column("Value", overflow="fold")
    table.add_row("Name", str(payload["name"]))
    table.add_row("Source", str(payload["source_root"]))
    table.add_row("Destination", str(payload.get("destination", "")))
    table.add_row("Apply", str(payload.get("apply", False)))
    table.add_row("Clean", str(payload.get("clean", "")))
    table.add_row("Files", str(payload["file_count"]))
    table.add_row("Bytes", str(payload["total_bytes"]))
    table.add_row("Verification", "valid" if payload["verification"]["valid"] else "failed")
    console.print(table)
    if payload["verification"]["issues"]:
        console.print("[red]Verification issues:[/red]")
        for issue in payload["verification"]["issues"]:
            console.print(f"- {issue}")


@app.command("plan")
def plan_command(
    config_path: str = typer.Option(DEFAULT_CONFIG_PATH, "--config", help="Public export TOML config."),
    source_root: str = typer.Option(".", "--source-root", help="Internal source repo root."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable debug logging."),
) -> None:
    """Show the files that would be included in the public snapshot."""
    _configure_logging(verbose)
    config = PublicExportConfig.from_file(config_path)
    plan = build_export_plan(source_root, config)
    payload = {
        "name": config.name,
        "description": config.description,
        "source_root": str(plan.source_root),
        "file_count": len(plan.files),
        "total_bytes": plan.total_bytes,
        "files": [path.as_posix() for path in plan.files],
        "verification": {"valid": True, "issues": []},
    }
    if json_output:
        _emit_json(payload)
        return
    _render_plan(payload)


@app.command("sync")
def sync_command(
    destination: str = typer.Argument(..., help="Destination public checkout or export directory."),
    config_path: str = typer.Option(DEFAULT_CONFIG_PATH, "--config", help="Public export TOML config."),
    source_root: str = typer.Option(".", "--source-root", help="Internal source repo root."),
    apply: bool = typer.Option(False, "--apply", help="Actually write files. Without this, only dry-run."),
    clean: bool = typer.Option(True, "--clean/--no-clean", help="Remove destination contents except .git before copying."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable debug logging."),
) -> None:
    """Sync the allowlisted public snapshot to a separate checkout."""
    _configure_logging(verbose)
    config = PublicExportConfig.from_file(config_path)
    payload = sync_public_tree(source_root, destination, config, apply=apply, clean=clean)
    if json_output:
        _emit_json(payload)
    else:
        _render_plan(payload)
    if not payload["verification"]["valid"]:
        raise typer.Exit(1)


@app.command("verify")
def verify_command(
    public_tree: str = typer.Argument(..., help="Public checkout/export directory to verify."),
    config_path: str = typer.Option(DEFAULT_CONFIG_PATH, "--config", help="Public export TOML config."),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON."),
    verbose: bool = typer.Option(False, "--verbose", help="Enable debug logging."),
) -> None:
    """Verify a public snapshot does not contain forbidden files or obvious secrets."""
    _configure_logging(verbose)
    config = PublicExportConfig.from_file(config_path)
    payload = verify_public_tree(public_tree, config)
    if json_output:
        _emit_json(payload)
    elif payload["valid"]:
        console.print("[green]Public tree verification passed.[/green]")
    else:
        console.print("[red]Public tree verification failed.[/red]")
        for issue in payload["issues"]:
            console.print(f"- {issue}")
    if not payload["valid"]:
        raise typer.Exit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()

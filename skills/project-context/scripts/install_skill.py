#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
from enum import Enum
from pathlib import Path

from _bootstrap import ensure_runtime

ensure_runtime()

import typer
from loguru import logger

from _support import abort, configure_logging, emit_path


app = typer.Typer(add_completion=False, help="Install the local project-context skill into Codex.")


def default_codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def install_target(codex_home: Path) -> Path:
    return codex_home / "skills" / skill_root().name


def remove_existing(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    shutil.rmtree(path)

class InstallMode(str, Enum):
    symlink = "symlink"
    copy = "copy"


@app.command()
def main(
    codex_home: Path = typer.Option(default_codex_home(), help="Path to the Codex home directory."),
    mode: InstallMode = typer.Option(InstallMode.symlink, help="Install mode."),
    overwrite: bool = typer.Option(False, help="Overwrite an existing installed skill."),
    debug: bool = typer.Option(False, help="Enable debug logging."),
) -> None:
    configure_logging(debug)

    codex_home = codex_home.expanduser().resolve()
    target = install_target(codex_home)
    source = skill_root()

    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() or target.is_symlink():
        if not overwrite:
            abort(f"Refusing to overwrite existing skill: {target}")
        remove_existing(target)

    if mode == InstallMode.symlink:
        target.symlink_to(source, target_is_directory=True)
    else:
        shutil.copytree(source, target)

    logger.info("Installed project-context skill to {}", target)
    emit_path(target)


if __name__ == "__main__":
    app()

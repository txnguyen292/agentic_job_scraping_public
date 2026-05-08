#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
import venv
import importlib.util
from pathlib import Path


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def venv_dir() -> Path:
    return skill_root() / ".venv"


def venv_python() -> Path:
    scripts_dir = "Scripts" if os.name == "nt" else "bin"
    python_name = "python.exe" if os.name == "nt" else "python"
    return venv_dir() / scripts_dir / python_name


def requirements_path() -> Path:
    return Path(__file__).resolve().with_name("requirements.txt")


def current_python_has_requirements() -> bool:
    modules = ("typer", "rich", "loguru", "yaml")
    return all(importlib.util.find_spec(module) is not None for module in modules)


def ensure_venv_exists() -> None:
    python_path = venv_python()
    if python_path.exists():
        return

    print(f"[project-context] creating local runtime: {venv_dir()}", file=sys.stderr)
    builder = venv.EnvBuilder(with_pip=True)
    builder.create(venv_dir())


def venv_has_requirements() -> bool:
    python_path = venv_python()
    check_code = (
        "import importlib.util, sys;"
        "mods=('typer','rich','loguru','yaml');"
        "missing=[m for m in mods if importlib.util.find_spec(m) is None];"
        "raise SystemExit(0 if not missing else 1)"
    )
    result = subprocess.run(
        [str(python_path), "-c", check_code],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def install_requirements() -> None:
    python_path = venv_python()
    print("[project-context] installing local runtime dependencies", file=sys.stderr)
    subprocess.run(
        [str(python_path), "-m", "pip", "install", "-r", str(requirements_path())],
        check=True,
    )


def running_inside_venv() -> bool:
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return True
    try:
        return Path(sys.executable).expanduser() == venv_python().expanduser()
    except FileNotFoundError:
        return False


def ensure_runtime() -> None:
    if current_python_has_requirements():
        return

    if running_inside_venv():
        return

    ensure_venv_exists()
    if not venv_has_requirements():
        install_requirements()

    os.execv(str(venv_python()), [str(venv_python()), *sys.argv])

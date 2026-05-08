from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def repo_root() -> Path:
    candidates: list[Path] = []
    if root := os.getenv("JOB_SCRAPER_PROJECT_ROOT"):
        candidates.append(Path(root).resolve())
    cwd = Path.cwd().resolve()
    candidates.extend([cwd, *cwd.parents])
    script_path = Path(__file__).resolve()
    candidates.extend([script_path.parent, *script_path.parents])

    for candidate in candidates:
        if (candidate / ".contexts").exists():
            return candidate
    return script_path.parents[3]


def run_context_command(args: list[str]) -> None:
    root = repo_root()
    result = subprocess.run(
        [str(root / ".contexts" / "bin" / args[0]), *args[1:]],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        print(
            json.dumps(
                {
                    "status": "error",
                    "returncode": result.returncode,
                    "stderr": result.stderr[-2000:],
                },
                ensure_ascii=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(result.returncode)
    print(result.stdout.strip())

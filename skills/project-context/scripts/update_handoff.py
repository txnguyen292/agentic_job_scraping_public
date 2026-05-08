#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
    for candidate in candidates:
        if (candidate / ".contexts").exists():
            return candidate
    print(json.dumps({"status": "error", "error": ".contexts not found"}), file=sys.stderr)
    raise SystemExit(1)


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
        print(json.dumps({"status": "error", "returncode": result.returncode, "stderr": result.stderr[-2000:]}), file=sys.stderr)
        raise SystemExit(result.returncode)
    print(result.stdout.strip())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--active-task", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--next-step", default="")
    parser.add_argument("--touched-file", action="append", default=[])
    parser.add_argument("--verification", default="")
    args = parser.parse_args()
    command = ["update_handoff", "--active-task", args.active_task, "--summary", args.summary]
    if args.next_step:
        command.extend(["--next-step", args.next_step])
    for touched_file in args.touched_file:
        command.extend(["--touched-file", touched_file])
    if args.verification:
        command.extend(["--verification", args.verification])
    run_context_command(command)


if __name__ == "__main__":
    main()

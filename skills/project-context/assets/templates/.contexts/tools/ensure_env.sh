#!/usr/bin/env sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
ROOT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"
LOCK_DIR="$ROOT_DIR/.contexts/.env.lock"
CLEANUP_LOCK=0

while ! mkdir "$LOCK_DIR" 2>/dev/null; do
  sleep 0.1
done

CLEANUP_LOCK=1
trap 'if [ "$CLEANUP_LOCK" -eq 1 ]; then rmdir "$LOCK_DIR" 2>/dev/null || true; fi' EXIT INT TERM

if [ ! -x "$PYTHON_BIN" ]; then
  python3 -m venv "$VENV_DIR"
fi

if ! "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import importlib.util
mods = ("typer", "rich", "loguru", "yaml")
missing = [name for name in mods if importlib.util.find_spec(name) is None]
raise SystemExit(0 if not missing else 1)
PY
then
  "$PYTHON_BIN" -m pip install -r "$SCRIPT_DIR/requirements.txt"
fi

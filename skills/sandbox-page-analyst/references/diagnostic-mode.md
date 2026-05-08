# Diagnostic Mode

Use this reference when the user asks for a simple sandbox probe, stdout/stderr contract test, dependency check, file inspection, or runtime debugging that does not require job extraction.

## Start

Start the sandbox with:

```text
scripts/sandbox_start.py --mode diagnostic
```

Use `--mode debug` only when the user explicitly asks for debug/audit-heavy inspection. Debug mode follows the same no-protocol rules unless the user asks for the full extraction workflow.

## Rules

1. Run only the requested bounded command or the smallest necessary inspection command.
2. Return the observed fields directly: `status`, `exit_code`, `stdout`, `stderr`, `stdout_preview`, `stderr_preview`, truncation flags, byte counts, `paths`, and `audit_id` when present.
3. Treat `stdout_truncated=true` or `stderr_truncated=true` as a normal bounded-output condition, not an error by itself. The preview in `stdout` or `stderr` is what the agent can reason over; the path points to the full persisted output.
4. If the user requests a preview size for one command, pass `--max-read-chars N` to `scripts/sandbox_exec.py`; do not repeat the command if the script accepts the argument and returns a truncated preview.
5. Do not write `output/extractor.py`, protocol files, reference proposals, or skill patches.
6. Do not run `scripts/validate_outputs.py`.
7. Do not call `scripts/sandbox_finalize.py` unless the user explicitly asks to close the sandbox.
8. If a command exits non-zero, report the bounded `stderr` preview and path. Do not retry unless the user asked you to fix the command or the next minimal repair is obvious.

## Stop Condition

Stop after the requested probe has returned enough information to answer the user. A diagnostic answer may be plain text or compact JSON; it does not need `output/final.json`.

## Output Shape

Prefer compact JSON for tool-contract tests:

```json
{
  "status": "success",
  "audit_id": "sandbox_run_...",
  "stdout": "first returned chars",
  "stdout_preview": "first returned chars",
  "stdout_truncated": true,
  "returned_stdout_chars": 40,
  "paths": {
    "stdout_path": "commands/001.stdout.txt"
  }
}
```

Never replace the preview with only a file path.

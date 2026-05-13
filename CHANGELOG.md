# Changelog

## Unreleased

### Added

- Surface OpenAI reasoning telemetry in ADK Web metadata without showing token-only telemetry as fake chat thoughts.
- Add LiteLLM/OpenAI reasoning effort configuration for mini/high workflow experiments.
- Add release-note automation that mirrors this changelog section into pull requests as a collapsible block.

### Changed

- Disable default wall-clock sandbox duration expiry; sandbox duration is now opt-in while command and resource limits remain active.
- Keep internal CI jobs rooted at the repository checkout while exporting the public snapshot to an outside temporary directory.

### Fixed

- Fix public export CI so the sanitized public repo is not checked out inside the internal source tree.
- Strengthen sandbox workflow contracts and validation/finalization guardrails for reasoning-heavy extraction runs.

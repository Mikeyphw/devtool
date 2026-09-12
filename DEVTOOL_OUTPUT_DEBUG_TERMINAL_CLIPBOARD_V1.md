# Devtool output-debug terminal + clipboard evidence v1

## Purpose

This overlay makes Devtool's existing output behavior directly inspectable when debugging. A single opt-in switch covers both the final terminal summary and any payload Devtool sends through its existing `--copy` clipboard path.

## Controls

- CLI enable: `--output-debug`
- CLI disable/override: `--no-output-debug`
- Environment enable: `DEVTOOL_OUTPUT_DEBUG=1` (`true`, `yes`, and `on` are also accepted)
- Environment disable: unset/empty, `0`, `false`, `no`, or `off`
- CLI takes precedence over the environment variable.
- The former proposed `--copy-debug` / `DEVTOOL_COPY_DEBUG` names are intentionally not retained as aliases.

## Terminal-summary evidence

For commands that use Devtool's normal final summary pipeline, output-debug records a plain-text rendering with the same detected terminal width. ANSI styling is excluded from durable evidence, but the human summary layout/content is retained. JSON summary mode records the emitted JSON summary instead.

Evidence is written beneath:

`<devtool-state>/evidence/<run-id>/output-debug/`

Each capture has:

- a private `.txt` payload,
- JSON metadata,
- UTF-8 byte count,
- SHA-256,
- run ID and timestamp,
- source/stream/rendering metadata,
- a private `timeline.jsonl` correlation stream.

## Clipboard evidence

The existing `--copy` behavior is unchanged. Devtool now prepares the sanitized clipboard payload once, sends that same string to the selected clipboard backend, and—when output-debug is enabled—persists/hash-correlates that exact attempted payload.

Metadata also records:

- requested clipboard backend family,
- resolved backend,
- copy success/failure and reason,
- source (`--copy` or interactive prompt),
- terminal summary SHA-256 when available,
- `matches_terminal_summary`.

Clipboard evidence is recorded even when the clipboard backend fails, which distinguishes "Devtool selected the wrong payload" from "Devtool selected the right payload but the clipboard transport failed".

## Security and privacy

Output-debug is off by default. Evidence stays behind Devtool's existing redaction boundary. Evidence directories are best-effort mode `0700` and payload/metadata/timeline files mode `0600`.

## Documentation correction

The complete human and AI guides now explicitly document that global `--copy` copies Devtool output to the system clipboard; it is not an artifact-staging flag.

## Validation

Focused regression gate on the reconstructed post-telemetry Devtool state:

- `395 passed`
- `8 subtests passed`
- complete feature guides current with `136 command parsers`

Coverage includes parser/env precedence, fail-closed invalid environment values, terminal summary persistence, exact clipboard payload persistence, terminal/clipboard hash correlation, clipboard-backend failure evidence, disabled-mode behavior, old-name rejection, existing clipboard behavior, CLI behavior, Gradle task telemetry, Android runner behavior, diagnostics, and guide freshness.

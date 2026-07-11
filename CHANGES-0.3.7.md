# Devtool 0.3.7

## Authoritative live HUD step state

- Stores the latest current, total, percent, status, detail, warning count, and error count for every operation directly in the active HUD state.
- Desktop and Android step bars no longer depend on observing every intermediate JSONL event.
- Keeps live progress isolated by workspace/target for scoped phases such as `build[s3atelier]`.
- Preserves completed step snapshots while later restore/build/test/validation phases run.
- Adds regression coverage for rendering live build progress when event history is unavailable or polling skips intermediate events.

## Versions

- Devtool: 0.3.7
- Android HUD: 1.5.5 (compatible; receives the corrected step snapshots through the bridge)

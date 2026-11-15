# Delivery S6 — unified structured evidence and Rich view

S6 is the sixth phase of the **original** S0–S7 Devtool modernization roadmap.
It consumes existing durable S1 delivery sessions, exact PR identity from S3,
S4 policy/CI evidence and S5 source-bound diagnostics; it does **not** create a
second event journal, mutation engine, GitHub client or release gate.

## Canonical commands

```sh
python3 -m devtool --repo "$HOME/Code/devtool" delivery session status --id SESSION_ID --json
python3 -m devtool --repo "$HOME/Code/devtool" delivery session report --id SESSION_ID
python3 -m devtool --repo "$HOME/Code/devtool" delivery session report --id SESSION_ID --json
python3 -m devtool --repo "$HOME/Code/devtool" delivery session resume --id SESSION_ID
```

`status` emits `event_stream` reconstructed from the integrity-checked S1
journal. `report` emits a stable `receipt` plus `human_report`. Rich consumes
the same receipt when a terminal is available. Optional Textual frontends can
read the exact same structure without inheriting delivery authority.

The stream contains bounded persisted stages, exact PR number, workflow run IDs,
attempts, saved outcomes, and canonical failed-job URLs, preserving source SHA.
An interrupted/restarted frontend must select an existing session ID; it must
never repeat an uncertain push, PR creation, dispatch or rerun.

All CI verdicts in an S6 report describe the **last persisted observation**, not
live GitHub status. A report reads no remote data. Only `delivery session ci`
may refresh provider evidence under S4/S5 policy and source checks. A successful
CI observation does not authorize release, merge, signing or device installation.

## Acceptance boundaries

- Rich and text renderers are presentations of one source-bound event stream.
- Persisted intents are never replayed and source drift blocks safe resume.
- Report output never contains provider raw logs, signed redirect URLs or tokens.
- Failed jobs retain exact run + attempt + job identity; URL mismatches refuse.
- Stale CI is explicitly historical and cannot become a fresh pass.
- Hosted S6 pytest workflow is additional evidence, not a replacement for the
  six existing required workflows listed in `config/delivery-required-ci.json`.
- S7 (scaffolding and consumer migration) is still pending.

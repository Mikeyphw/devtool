# RMP final consolidated source hardening — H1 + H2

This installation is **not** a formal release seal. It applies the final
source-level corrections and preserves all original audit obligations.

## Final implementation deltas

- N02: recursively redact nested presentation event metadata, including list
  elements, before machine-readable serialization.
- C05: a passed projection with a failed/blocked/unknown child event cannot be
  rendered as a green dashboard, including when the event is beyond the visible
  event limit. The projection remains read-only and does not own execution truth.
- C06: exclude credential values from nested array entries written in
  `KEY=value` / `export KEY=value` form before environment identity hashing.
- C04: preserve Phase 2/3 shard/case bindings; independent TEST-A11 stays OPEN.
- N01: retain the exact 308-finding anchor without claiming a signed source.
- Q01: retain canonical cleanup ownership protection and real-life C02 closure;
  run fresh disposable cleanup negatives without modifying deletion authority.
- Q02: retain the historical 20 probes (14 controls/matches, 6 historical
  mismatches) and run current source contracts; 11 legacy contracts have not
  been independently mapped and thus Q02 stays OPEN.
- U01: native/remote substrate qualifications are explicitly non-transferable;
  ENV-A06, ANDROID-A16, DIST-A01, DIST-A12, DIST-A13 and TEST-A11 stay OPEN.

## Local acceptance

`python3 -m devtool.rmp_final_hardening --repo .`

The source-bound JSON receipt verifies the 206+ Phase 4 native acceptance
against its original Git parent, the current source hashes, 308-row retained
crosswalk, 20-probe historical accounting, and focused live consumer tests.
It does not repeat CI24, G3R, RS13, external worker, or Android device tests.

Status after a positive result: `PASS_RMP_FINAL_SOURCE_HARDENING`;
`formal_h1_closed=false`, `formal_h2_closed=false`, `release_qualified=false`.
The separate original six real-substrate obligations remain open.

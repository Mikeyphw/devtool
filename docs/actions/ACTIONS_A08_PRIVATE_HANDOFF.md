# ACTIONS-A08 — Free/private LIFE release handoff (source stage)

A07 real Cinderbox GitHub debug DAG passed on run `39336899057`, attempt 1,
commit `cb218cbae3e5eed5769ba784dd5d1fc747f40dd1`. A07 does not
establish an installable production release or Android device qualification.

A08 extends the existing G6 **read-only** registry lineage/certificate verifier,
not the independent ACTIONS-08 protected release qualification gate. The
user's GitHub Free/private protected-CI exclusion remains in force; no
protected rulesets, GitHub secrets or release publishing are changed.

## Read-only preview (always the default)

```sh
devtool --repo "$HOME/Code/devtool" actions release local-handoff \
  --registry-id 26529ad5f699 \
  --expected-sha f3185b6aac56d1ac29b5fac93bdda88fc4480842 \
  --operation export --destination "$HOME/Downloads/devtool-hud-signed.apk" --json
```

The example registry ID/source SHA refer to historical ACTIONS-10 private
local signing evidence; they are **not** a claim of fresh device release
qualification. The destination must be absent; exports never overwrite.

## Explicit mutation

To export after reviewing the preview, repeat with both `--yes` and
`--allow-unqualified`. For an APK installation instead, select an exact adb
serial and request `--operation install --serial SERIAL` with those same two
explicit consent options. AAB is **not** installable. The install attempts a
single ordinary `adb install`; it never replaces/uninstalls an existing app,
reboots, publishes or asserts a device qualification gate. A failed adb
command is not a successful install.

The mandatory `--expected-sha` is an independent known Git source commit.
The complete registered signed→unsigned→remote import chain must be internally
consistent; independent local apksigner/bundletool and registered LIFE signer
inspection must succeed. Before the mutation, Devtool copies the exact signed
bytes into a private frozen file and compares SHA-256 to the verified receipt.
An earlier bug in G6 final unsigned-parent checks is corrected: the parent
hash is recorded before external signer inspection and checked again afterward,
not compared to itself after the inspection.

A08 returns **`status: signed-but-unqualified`**, **`source_attested:false`**,
**`qualified_release:false`**, **`published:false`**, and
**`device_qualified:false`**, even when a locally signed package is exported
or adb reports install success. No release automation is triggered.

## Acceptance boundaries

The source tests exercise preview, consent negatives, unauthorized/unknown
device, symlinks, wrong SHA, changed signed and unsigned bytes, exact signer
identity, and no-overwrite. The real Termux LIFE-keystore and device user-flow
remain to be accepted separately by their operator. ACTIONS-08 cryptographic
GitHub protected-release qualification remains open (and is not falsely
satisfied by GitHub Free/private local signing).

The G8 original 88-item historical matrix remains immutable. A separately
pinned G6 source amendment validates the exact A08 G6 implementation file and
supporting doc/test. Neither amendment nor source pytest is live-release proof.

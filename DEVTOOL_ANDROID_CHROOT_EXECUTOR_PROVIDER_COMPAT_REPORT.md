# Devtool Android Chroot Executor Provider Compatibility Fix v5

## Context

This overlay is rebuilt against the post-final current snapshot `devtool-20260816-193438.tar.gz` after the previous v4 artifact correctly failed source-hash validation for `devtool/cli.py` and `tests/test_cli.py`.

## Fix

Long-lived Android chroots may keep a stale staged `build-apk` helper. Newer Devtool command generation was passing default provider-selection flags such as `--java-provider auto` and `--gradle-provider auto`. Those defaults are semantically equivalent to omitting the flags, but older staged helper parsers reject them before Gradle starts.

This patch omits only default `auto` provider flags from generated Android builder commands while preserving explicit non-default selections.

Omitted when set to `auto`:

- `--java-provider`
- `--ndk-host-provider`
- `--qemu-ld-prefix`
- `--qemu-install`
- `--native-provider`
- `--native-cache`
- `--gradle-provider`

Explicit non-default values are still passed through.

## Failure summary improvement

The copied summary now recognizes argparse-style command errors such as:

```text
build-apk: error: unrecognized arguments: --java-provider auto
```

When the command exits non-zero with an unknown failure kind, that concrete raw error line is used in the `Failure reason:` section instead of `unknown failure`.

## Verification

Focused suite run against the patched snapshot:

```text
304 passed, 8 subtests passed
```

Compile sanity:

```text
python3 -m compileall -q devtool tests
```

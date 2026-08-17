# Devtool Android AAPT2 auto root-cause fix

## Problem

Android validation with `--aapt2-provider auto` could fail before Gradle ran because the staged `build-apk` helper contacted GitHub to resolve the latest ReVanced AAPT2 release metadata. A transient GitHub API timeout therefore became a hard validation failure even though the Android project could use a local SDK AAPT2, a previously verified cached AAPT2, or Android Gradle Plugin's default AAPT2 resolution.

## Root fix

`auto` is now offline-first and deterministic:

1. explicit `--aapt2` still wins;
2. native Termux AAPT2 is used when Devtool is running under native Termux and it passes the platform capability probe;
3. installed SDK build-tools `aapt2` is used only when it is executable and passes the selected platform capability probe;
4. verified cached ReVanced AAPT2 may be used on ARM hosts without any network lookup;
5. otherwise Devtool lets Android Gradle Plugin choose its default AAPT2.

A live GitHub/ReVanced metadata query is now reserved for explicit `--aapt2-provider revanced`. Remote metadata failure is therefore no longer a default `auto` failure path.

## Tests added

- `test_auto_aapt2_does_not_query_remote_revanced_metadata`
- `test_explicit_revanced_aapt2_provider_still_allows_remote_provisioning`
- `test_auto_aapt2_can_use_verified_cached_revanced_without_remote_lookup`
- `test_sdk_build_tools_aapt2_requires_capability_probe`

## Validation

Focused suite used for this overlay:

```text
129 passed
```

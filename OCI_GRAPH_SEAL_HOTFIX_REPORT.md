# OCI composite graph seal hotfix

## Result

PASS. This hotfix fixes OCI commands that compose more than one lifecycle/probe operation in a single Devtool execution run.

## Root cause

The OCI lifecycle helper sealed the EXO execution graph every time it discovered a node set. That was correct for single-purpose commands such as `oci status`, but incorrect for composite commands:

- `devtool oci diagnostics tablet` runs `doctor` and then `image-status` in one run.
- `devtool oci update tablet --apply` runs `image-status`, then stages/builds/installs/validates the canonical image in one run.

After the first probe sealed the graph, the second phase tried to discover its own node and Devtool correctly rejected it with errors such as:

```text
execution graph '<run>' is sealed; cannot discover node 'oci:tablet:image-status-runtime-probe'
execution graph '<run>' is sealed; cannot discover node 'oci:tablet:stage-image-definition:<digest>'
```

## Fix

- Adds an explicit graph-seal helper for OCI lifecycle runs.
- Allows selected internal OCI payload functions to defer graph sealing when used by a composite command.
- Keeps standalone commands sealed immediately by default.
- Updates `oci diagnostics` so the doctor probe stays unsealed until the image-status probe is planned.
- Updates `oci update --apply` so the image-status probe stays unsealed until build/install/validate nodes are planned.
- Updates `oci prune --apply` similarly for remove-image sub-operations.

## Validation

Executed on a disposable post-hotfix Devtool tree:

- `tests/test_oci07_image_management_policy.py`: 7 passed
- `tests/test_oci09_diagnostics_visibility.py`: included in focused run
- `tests/test_oci10_final_seal_compatibility.py`: included in focused run
- Full OCI-01 through OCI-10 contract set: 126 passed
- Local OCI-10 final seal: ok, 0 failures


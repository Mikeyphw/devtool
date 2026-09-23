# Backup Hygiene Cleanup Overlay

This overlay removes the historical transient backup/reject debris that blocked
the FS03 repository backup-hygiene check during the first OCI-10 apply.

It intentionally targets only the exact backup paths reported by Devtool's
rollback log. Missing paths are treated as already clean.

## Removed path set

- `.connected-test-skip-backup-20260821-162010`
- `.devtool-python-autofix-backup-20260824-112933`
- `.diagnostic-surface-backup-20260821-125535`
- `.no-device-config-cache-fix-20260821-163601`
- `.transitive-connected-skip-backup-20260821-162845`
- `.warning-fix-backup-20260821-141443`
- `devtool/project_runners.py.pre-build-backend-wheel-fix.bak`
- `devtool/project_runners.py.pre-termux-pydantic-final.bak`
- `devtool/project_runners.py.pre-termux-pydantic-workaround-v2.bak`
- `devtool/python_support.py.pre-build-backend-wheel-fix.bak`
- `devtool/python_support.py.pre-termux-pydantic-final.bak`
- `devtool/python_support.py.pre-termux-pydantic-workaround-v2.bak`

## Safety model

- The destructive manifest is limited to the exact paths above.
- No source code path without a backup marker/suffix is targeted.
- Validation verifies the original paths are absent and that the repository
  cleanup scanner no longer reports backup candidates outside `.devtool/**`.

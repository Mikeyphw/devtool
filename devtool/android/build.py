"""Android build orchestration compatibility surface.

This is the canonical package import for Android build orchestration.  The heavy
builder remains in :mod:`devtool.android_build` because that file is also staged
as a standalone executable in chroot environments.
"""

from ..android_build import (
    BuildError,
    JavaRequirement,
    ProjectInfo,
    artifact_module,
    artifact_variant,
    copy_artifacts,
    default_tasks,
    discover_artifacts,
    inspect_project,
    main,
    parse_args,
    planned_artifact_names,
    resolve_project_root,
    slugify_name,
    standalone_main,
)

__all__ = [
    "BuildError", "JavaRequirement", "ProjectInfo", "artifact_module",
    "artifact_variant", "copy_artifacts", "default_tasks", "discover_artifacts",
    "inspect_project", "main", "parse_args", "planned_artifact_names",
    "resolve_project_root", "slugify_name", "standalone_main",
]

if __name__ == "__main__":
    raise SystemExit(standalone_main())

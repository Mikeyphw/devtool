"""Stable modular Android pipeline API.

The package provides domain-oriented import surfaces while ``devtool.android_build``
remains the self-contained staged builder used inside chroots.  Consumers should
prefer these modules so implementations can continue moving behind stable APIs.
"""

from .build import BuildError, JavaRequirement, ProjectInfo, inspect_project, main, standalone_main

__all__ = [
    "BuildError",
    "JavaRequirement",
    "ProjectInfo",
    "inspect_project",
    "main",
    "standalone_main",
]

"""Stable modular Android pipeline API.

The package provides domain-oriented import surfaces while ``devtool.android_build``
remains the self-contained staged builder used inside chroots.  Consumers should
prefer these modules so implementations can continue moving behind stable APIs.
"""

from .build import BuildError, JavaRequirement, ProjectInfo, inspect_project, main, standalone_main

from .capabilities import AndroidExecutionContext, AndroidToolResolution, resolve_android_execution_context
from .devices import AndroidDevice, AndroidDeviceManager, AndroidDeviceError
from .operations import AndroidOperations, resolve_android_operations
from .signing import AndroidSigningError, AndroidSigningManager, SigningIdentity, SigningProfile, SigningVerification
from .device_ops import AndroidDeviceOperationError, AndroidDeviceOperations, DeviceOperationResult, SmokeGateResult
from .instrumentation import AndroidInstrumentationOrchestrator, InstrumentationRequest, InstrumentationResult, resolve_instrumentation_policy

__all__ = [
    "BuildError",
    "JavaRequirement",
    "ProjectInfo",
    "inspect_project",
    "main",
    "AndroidExecutionContext", "AndroidToolResolution", "resolve_android_execution_context",
    "AndroidDevice", "AndroidDeviceManager", "AndroidDeviceError",
    "AndroidOperations", "resolve_android_operations",
    "AndroidSigningError", "AndroidSigningManager", "SigningIdentity", "SigningProfile", "SigningVerification",
    "AndroidDeviceOperationError", "AndroidDeviceOperations", "DeviceOperationResult", "SmokeGateResult",
    "AndroidInstrumentationOrchestrator", "InstrumentationRequest", "InstrumentationResult", "resolve_instrumentation_policy",
    "standalone_main",
]

from .release import AndroidReleaseOptions, AndroidReleaseOrchestrator, AndroidReleaseResult, release_options
from .evidence import AndroidReleaseEvidenceSession

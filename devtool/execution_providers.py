from __future__ import annotations

"""Execution-provider contracts shared by local and SSH execution.

OCI-01 deliberately separates *where a command is transported* from *which
runtime provider executes it*.  Existing SSH workers continue to use the
``ssh-native`` provider.  ``ssh-proot-oci`` is accepted as configuration and
reported through diagnostics, but is fail-closed until the later OCI execution
overlays implement image lifecycle and command wrapping.
"""

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

NATIVE = "native"
SSH_NATIVE = "ssh-native"
SSH_PROOT_OCI = "ssh-proot-oci"

EXECUTION_PROVIDERS = (NATIVE, SSH_NATIVE, SSH_PROOT_OCI)


@dataclass(frozen=True, slots=True)
class ExecutionProviderCapabilities:
    remote: bool
    requires_ssh: bool
    requires_tmux: bool
    managed_workspace: bool
    resumable: bool
    containerized: bool
    requires_image: bool

    def as_dict(self) -> dict[str, bool]:
        return {
            "remote": self.remote,
            "requires_ssh": self.requires_ssh,
            "requires_tmux": self.requires_tmux,
            "managed_workspace": self.managed_workspace,
            "resumable": self.resumable,
            "containerized": self.containerized,
            "requires_image": self.requires_image,
        }


@dataclass(frozen=True, slots=True)
class ExecutionProvider:
    name: str
    family: str
    description: str
    capabilities: ExecutionProviderCapabilities
    executable: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "family": self.family,
            "description": self.description,
            "executable": self.executable,
            "capabilities": self.capabilities.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class ExecutionProviderContext:
    """Stable lifecycle context passed to provider adapters.

    Later OCI overlays can add concrete adapter implementations without
    changing the call contract established by OCI-01.
    """

    provider: str
    repo_root: str
    working_root: str = ""
    profile: str = ""
    run_id: str = ""
    node_id: str = ""
    parent_node_id: str = ""

    def require_exo_identity(self) -> tuple[str, str]:
        """Return the canonical EXO identity required for executable provider work.

        Provider adapters may keep ephemeral runtime handles in their ``state``
        mapping, but durable lifecycle/progress belongs to EXO.  Concrete
        provider execution must therefore reuse the caller's canonical run/node
        identity rather than inventing provider-specific run state.
        """
        run_id = str(self.run_id or "").strip()
        node_id = str(self.node_id or "").strip()
        if not run_id or not node_id:
            raise ValueError(
                "Execution provider work requires canonical EXO run_id and node_id; "
                "do not create a provider-local execution state machine"
            )
        return run_id, node_id


@runtime_checkable
class ExecutionProviderAdapter(Protocol):
    """Lifecycle contract for execution-provider implementations.

    Implementations may manage ephemeral provider resources, but they must not
    become a second execution state machine.  Observable work is represented by
    canonical EXO events using ``ExecutionProviderContext.run_id`` and
    ``ExecutionProviderContext.node_id``.  Provider provenance is attached to
    the canonical process/node rather than persisted as independent lifecycle
    truth.
    """

    provider_name: str

    def prepare(self, context: ExecutionProviderContext) -> Mapping[str, Any]:
        ...

    def wrap_command(
        self,
        argv: Sequence[str],
        context: ExecutionProviderContext,
        state: Mapping[str, Any],
    ) -> tuple[str, ...]:
        ...

    def map_environment(
        self,
        environment: Mapping[str, str],
        context: ExecutionProviderContext,
        state: Mapping[str, Any],
    ) -> Mapping[str, str]:
        ...

    def cancel(
        self,
        context: ExecutionProviderContext,
        state: Mapping[str, Any],
    ) -> None:
        ...

    def provenance(
        self,
        context: ExecutionProviderContext,
        state: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...

    def cleanup(
        self,
        context: ExecutionProviderContext,
        state: Mapping[str, Any],
    ) -> None:
        ...


_PROVIDER_REGISTRY: dict[str, ExecutionProvider] = {
    NATIVE: ExecutionProvider(
        name=NATIVE,
        family="native",
        description="Execute directly in the current local execution environment.",
        capabilities=ExecutionProviderCapabilities(
            remote=False,
            requires_ssh=False,
            requires_tmux=False,
            managed_workspace=False,
            resumable=False,
            containerized=False,
            requires_image=False,
        ),
    ),
    SSH_NATIVE: ExecutionProvider(
        name=SSH_NATIVE,
        family="ssh",
        description="Execute directly on the SSH worker host using the existing tmux-owned worker runtime.",
        capabilities=ExecutionProviderCapabilities(
            remote=True,
            requires_ssh=True,
            requires_tmux=True,
            managed_workspace=True,
            resumable=True,
            containerized=False,
            requires_image=False,
        ),
    ),
    SSH_PROOT_OCI: ExecutionProvider(
        name=SSH_PROOT_OCI,
        family="ssh-proot-oci",
        description="Execute inside an OCI image through PRoot-Distro on an SSH worker.",
        capabilities=ExecutionProviderCapabilities(
            remote=True,
            requires_ssh=True,
            requires_tmux=True,
            managed_workspace=True,
            resumable=True,
            containerized=True,
            requires_image=True,
        ),
        executable=False,
    ),
}

_ALIASES = {
    "": "",
    "local": NATIVE,
    "host": NATIVE,
    "ssh": SSH_NATIVE,
    "remote": SSH_NATIVE,
    "ssh-native": SSH_NATIVE,
    "native-ssh": SSH_NATIVE,
    "proot": SSH_PROOT_OCI,
    "proot-oci": SSH_PROOT_OCI,
    "oci": SSH_PROOT_OCI,
    "ssh-proot": SSH_PROOT_OCI,
    "ssh-proot-oci": SSH_PROOT_OCI,
}


def normalize_execution_provider(value: str | None, *, remote: bool = False) -> str:
    raw = str(value or "").strip().lower().replace("_", "-")
    if not raw:
        return SSH_NATIVE if remote else NATIVE
    normalized = _ALIASES.get(raw, raw)
    # Within an SSH profile, `provider = "native"` means native execution on
    # the remote worker, not local execution on the controlling phone.
    if remote and normalized == NATIVE:
        normalized = SSH_NATIVE
    if normalized not in _PROVIDER_REGISTRY:
        expected = ", ".join(EXECUTION_PROVIDERS)
        raise ValueError(f"Unknown execution provider {value!r}. Expected one of: {expected}")
    provider = _PROVIDER_REGISTRY[normalized]
    if remote and not provider.capabilities.remote:
        raise ValueError(f"Execution provider {provider.name!r} is not valid for an SSH worker")
    return normalized


def execution_provider(value: str | None, *, remote: bool = False) -> ExecutionProvider:
    return _PROVIDER_REGISTRY[normalize_execution_provider(value, remote=remote)]


def execution_provider_registry() -> tuple[ExecutionProvider, ...]:
    return tuple(_PROVIDER_REGISTRY[name] for name in EXECUTION_PROVIDERS)


def provider_metadata(value: str | None, *, remote: bool = False) -> dict[str, Any]:
    return execution_provider(value, remote=remote).as_dict()


def ensure_execution_provider_ready(value: str | None, *, remote: bool = False) -> ExecutionProvider:
    provider = execution_provider(value, remote=remote)
    if not provider.executable:
        raise ValueError(
            f"Execution provider {provider.name!r} is configured but not executable in OCI-01. "
            "Keep provider='native'/'ssh-native' for current builds; PRoot-Distro OCI execution is activated in a later OCI roadmap overlay."
        )
    return provider


def provider_from_mapping(
    mapping: Mapping[str, Any] | None,
    *,
    remote: bool = False,
) -> ExecutionProvider:
    mapping = mapping or {}
    return execution_provider(str(mapping.get("provider", "") or ""), remote=remote)

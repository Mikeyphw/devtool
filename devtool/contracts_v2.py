"""RM-00: reviewed, non-executing contracts shared by hosted providers and evidence.

V2 is a *reader/plan schema*.  Existing V1 providers and evidence remain valid;
none of these helpers dispatch, load plugins, discover credentials, or promote trust.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Protocol
import re

CONTRACT_VERSION = 2
PROVIDER_API_V1 = 1
PROVIDER_API_V2 = 2
_ALLOWED_PROVIDER_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_ALLOWED_TOKEN = re.compile(r"[A-Za-z0-9_./:+@-]{1,128}\Z")
_OS = frozenset({'linux', 'windows', 'macos'})
_ARCH = frozenset({'x86_64', 'aarch64'})
_KNOWN_REQUIREMENTS = frozenset({'os', 'arch', 'toolchains', 'root', 'device', 'signing',
                                 'environment', 'capabilities'})
_DOMAINS = ('execution', 'verification', 'device', 'release')
_STATES = frozenset({'UNKNOWN', 'NOT_RUN', 'WAITING', 'BLOCKED', 'PASS', 'FAIL'})


class ContractError(ValueError):
    """A declaration cannot safely be interpreted; no execution is permitted."""


def _token(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _ALLOWED_TOKEN.fullmatch(value):
        raise ContractError(f'{name} must be a bounded literal token')
    return value


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    provider_id: str
    api_version: int
    capabilities: tuple[str, ...]
    host_profiles: tuple[str, ...]
    argv_grammar: str
    artifact_roles: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or not _ALLOWED_PROVIDER_ID.fullmatch(self.provider_id):
            raise ContractError('invalid provider ID')
        if type(self.api_version) is not int or self.api_version not in (1, 2):
            raise ContractError('unsupported provider API major version')
        for field in ('capabilities', 'host_profiles', 'artifact_roles'):
            values = getattr(self, field)
            if not isinstance(values, tuple) or not values or len(set(values)) != len(values):
                raise ContractError(f'{field} must be a nonempty duplicate-free tuple')
            for value in values:
                _token(value, field)
        _token(self.argv_grammar, 'argv_grammar')

    def describe(self) -> dict[str, Any]:
        return {'provider_id': self.provider_id, 'api_version': self.api_version,
                'capabilities': list(self.capabilities), 'host_profiles': list(self.host_profiles),
                'argv_grammar': self.argv_grammar, 'artifact_roles': list(self.artifact_roles)}


class LanguageProviderV2(Protocol):
    """Forward contract for later hosted providers; no V2 adapter is registered in RM-00."""
    descriptor: ProviderDescriptor
    def inspect(self, root: Any, job: Any, argv: list[str]) -> Mapping[str, Any]: ...
    def plan(self, declaration: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def emit(self, plan: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def verify(self, evidence: Mapping[str, Any]) -> Mapping[str, Any]: ...


# Reviewed built-ins only; extending this mapping requires a source change and review.
_REVIEWED: Mapping[str, ProviderDescriptor] = MappingProxyType({
    'go': ProviderDescriptor('go', 1, ('test', 'vet', 'build'), ('ubuntu-24.04-x86_64',),
                             'bounded-direct-go-argv-v1', ('source-only-unverified',)),
    'python-shell': ProviderDescriptor('python-shell', 1,
                             ('pytest', 'unittest', 'compileall', 'ruff', 'build'),
                             ('ubuntu-24.04-x86_64',), 'bounded-python-module-argv-v1',
                             ('source-only-unverified',)),
})


def reviewed_providers() -> Mapping[str, ProviderDescriptor]:
    return _REVIEWED


def resolve_provider(provider_id: str, *, api_version: int | None = None) -> ProviderDescriptor:
    if not isinstance(provider_id, str) or provider_id not in _REVIEWED:
        raise ContractError('unknown or unreviewed provider ID')
    descriptor = _REVIEWED[provider_id]
    if api_version is not None and (type(api_version) is not int or api_version != descriptor.api_version):
        raise ContractError('provider API major version mismatch')
    return descriptor


def negotiate_contract(kind: str, offered_versions: Any, *, required_features: Any = ()) -> dict[str, Any]:
    """Read-only feature negotiation; rejects unsupported versions and feature claims."""
    supported = {
        'provider-registry': {1: {'allowlisted', 'bounded-argv'},
                              2: {'allowlisted', 'bounded-argv', 'capability-descriptors', 'host-profiles'}},
        'host-eligibility': {1: {'legacy-host-eligibility'},
                             2: {'legacy-host-eligibility', 'capability-refusal', 'typed-requirements'}},
        'evidence': {1: {'legacy-evidence-read'},
                     2: {'legacy-evidence-read', 'domain-separation', 'no-trust-promotion'}},
    }
    if kind not in supported or not isinstance(offered_versions, (list, tuple)) or not offered_versions:
        raise ContractError('unknown kind or empty offered contract versions')
    if any(type(v) is not int for v in offered_versions):
        raise ContractError('contract versions must be exact integers')
    if not isinstance(required_features, (tuple, list, set, frozenset)):
        raise ContractError('required features must be a collection')
    required = {_token(v, 'required_features') for v in required_features}
    matches = [v for v in offered_versions if v in supported[kind] and required <= supported[kind][v]]
    if not matches:
        raise ContractError('unsupported contract major version or required features')
    selected = max(matches)
    return {'kind': kind, 'selected_version': selected,
            'features': sorted(supported[kind][selected]), 'execution_performed': False}


def host_eligibility_v2(requirements: Mapping[str, Any], *, host_os: str, host_arch: str,
                        toolchains: tuple[str, ...], capabilities: tuple[str, ...] = (),
                        environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Strict, declarative host admission. All unknown capabilities refuse by default.

    No command execution, environmental discovery, signing inference or credential lookup.
    Requirement `environment` checks *explicit caller-supplied* nonsecret values.
    """
    if not isinstance(requirements, Mapping) or any(not isinstance(k, str) for k in requirements) or set(requirements) - _KNOWN_REQUIREMENTS:
        raise ContractError('unknown host requirement keys')
    if not isinstance(toolchains, tuple) or not isinstance(capabilities, tuple):
        raise ContractError('host toolchains and capabilities must be tuples')
    if any(not isinstance(v, str) or not _ALLOWED_TOKEN.fullmatch(v) for v in (*toolchains, *capabilities)):
        raise ContractError('invalid host capability identity')
    reasons: list[str] = []
    if host_os not in _OS or host_arch not in _ARCH:
        reasons.append('unsupported host identity')
    for key, value, host, allowed in (('os', requirements.get('os'), host_os, _OS),
                                      ('arch', requirements.get('arch'), host_arch, _ARCH)):
        if value is not None:
            if not isinstance(value, str) or value not in allowed:
                raise ContractError(f'unsupported {key} requirement')
            if value != host:
                reasons.append(f'{key} requirement not met')
    for field in ('toolchains', 'capabilities'):
        values = requirements.get(field, ())
        if not isinstance(values, (list, tuple)) or len(values) > 32:
            raise ContractError(f'{field} must be a bounded list')
        if any(not isinstance(v, str) for v in values):
            raise ContractError(f'{field} must contain literal strings')
        if len(set(values)) != len(values):
            raise ContractError(f'{field} has duplicate declarations')
        available = toolchains if field == 'toolchains' else capabilities
        for value in values:
            _token(value, field)
            if value not in available:
                reasons.append(f'missing {field[:-1]}: {value}')
    for flag in ('root', 'device', 'signing'):
        value = requirements.get(flag, False)
        if type(value) is not bool:
            raise ContractError(f'{flag} requirement must be boolean')
        if value:
            # A generic hosted runner never attests root, device or signer authority.
            reasons.append(f'{flag} authority not available')
    env_req = requirements.get('environment', {})
    if not isinstance(env_req, Mapping) or len(env_req) > 16:
        raise ContractError('environment must be a bounded mapping')
    admitted_env = environment if environment is not None else {}
    if not isinstance(admitted_env, Mapping):
        raise ContractError('provided environment must be a mapping')
    for key, value in env_req.items():
        _token(key, 'environment key')
        _token(value, 'environment value')
        if key not in admitted_env or admitted_env[key] != value:
            reasons.append(f'environment requirement not met: {key}')
    return {'schema_version': 2, 'kind': 'devtool.host-eligibility',
            'eligible': not reasons, 'state': 'BLOCKED' if reasons else 'PASS',
            'reasons': reasons, 'required': dict(requirements),
            'host': {'os': host_os, 'arch': host_arch, 'toolchains': list(toolchains),
                     'capabilities': list(capabilities)},
            'execution_performed': False, 'trusted_host_verified': False,
            'device_qualified': False, 'release_qualified': False}


def read_evidence_v2(record: Mapping[str, Any], *, expected: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Versioned reader: V1 validated then wrapped; V2 envelopes cannot mint trust.

    A V2 envelope intentionally embeds the old strictly validated V1 payload.
    It is a migration reader, not an independent V2 evidence producer.
    """
    from .evidence_kernel import validate_evidence
    if not isinstance(record, Mapping):
        raise ContractError('evidence record must be an object')
    version = record.get('schema_version')
    if type(version) is not int or version not in (1, 2):
        raise ContractError('unsupported evidence schema major version')
    if version == 1:
        legacy = dict(record)
    else:
        if set(record) != {'schema_version', 'kind', 'legacy_evidence', 'domains'} or record.get('kind') != 'devtool.typed-evidence-v2':
            raise ContractError('unknown V2 evidence envelope fields/kind')
        domains = record['domains']
        if not isinstance(domains, Mapping) or set(domains) != set(_DOMAINS):
            raise ContractError('V2 evidence requires four distinct authority domains')
        for domain, value in domains.items():
            if not isinstance(value, Mapping) or set(value) != {'state', 'authority'}:
                raise ContractError('malformed authority domain: ' + domain)
            if value.get('state') not in _STATES or not isinstance(value.get('authority'), str):
                raise ContractError('invalid authority state: ' + domain)
            if len(value['authority']) > 256:
                raise ContractError('unbounded authority identifier')
        legacy = record['legacy_evidence']
    # Identity and status are normalized through the authoritative V1 shape.
    # A supplied V2 domain claim is never independent verification.
    validated = validate_evidence(legacy, expected=expected)
    return {'schema_version': 2, 'kind': 'devtool.evidence-reader-v2',
            'source_schema_version': version, 'identity_matched': validated['identity_matched'],
            'structure_valid': validated['structure_valid'],
            'reported_gates': validated['reported_gates'],
            'effective_domains': {name: {'state': 'UNKNOWN', 'authority': '',
                                         'reason': 'reader does not independently authenticate producer'}
                                  for name in _DOMAINS},
            'trusted_producer_verified': False, 'device_qualified': False,
            'release_qualified': False, 'legacy_projection': validated['evidence']}

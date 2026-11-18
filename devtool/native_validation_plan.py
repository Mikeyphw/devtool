"""Versioned, data-only projection of Devtool's existing validation authority.

The projection never chooses a runner or executes tests. It rejects an
executable overlay that promises validation without a verifiable executable
check, while retaining the old ZIP/launcher contracts unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

PLAN_SCHEMA = 'devtool.native-validation-plan.v1'


@dataclass(frozen=True, slots=True)
class NativeValidationPlan:
    schema: str
    mode: str
    required: bool
    phases: tuple[str, ...]
    task_count: int
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return dict(schema=self.schema, mode=self.mode, required=self.required,
                    phases=list(self.phases), task_count=self.task_count, evidence=self.evidence)


def normalize(manifest: Mapping[str, Any]) -> NativeValidationPlan:
    from .artifacts import (artifact_validation_policy, artifact_validation_tests,
                            artifact_validation_refs, artifact_validation_tasks,
                            artifact_validation_script)
    validation = manifest.get('validation')
    policy = artifact_validation_policy(dict(manifest))
    checks: list[str] = []
    if artifact_validation_script(dict(manifest)):
        checks.append('script')
    if artifact_validation_tasks(dict(manifest)):
        checks.append('tasks')
    if artifact_validation_tests(dict(manifest)):
        checks.append('tests')
    if artifact_validation_refs(dict(manifest)):
        checks.append('refs')
    if any(policy.get(key) for key in ('pytest_tests', 'pytest_markers', 'pytest_k')):
        checks.append('pytest')
    # Built-in phases execute only where explicitly enabled in a required
    # plan; there is no inference that a disabled phase somehow passed.
    for phase in ('restore', 'build', 'test'):
        # Declared tasks own the whole native execution plan even when legacy
        # manifest defaults leave restore/build/test implicitly true.
        if 'tasks' not in checks and policy.get(phase) and (validation is not None and policy.get('required')):
            checks.append(phase)
    executable = manifest.get('executable_overlay') is not None
    static = bool(validation.get('static_only') is True) if isinstance(validation, dict) else False
    if executable:
        if not isinstance(validation, dict):
            raise ValueError('Executable overlay requires validation declaration')
        # Native validation.tasks is an exclusive execution path. Do not
        # promise script, declared test/reference or builtin phase evidence
        # that the task runner would never execute.
        if 'tasks' in checks and (
            any(kind in checks for kind in ('script', 'tests', 'refs'))
            or any(validation.get(phase) is True for phase in ('restore', 'build', 'test'))
        ):
            raise ValueError('Exclusive validation.tasks cannot combine with other executable checks')
        if 'tests' in checks and not any(t.get('required', True) for t in artifact_validation_tests(dict(manifest))):
            checks.remove('tests')
        if static:
            if (any(validation.get(phase) is not False for phase in ('restore', 'build', 'test'))
                    or any(k in checks for k in ('script', 'tasks', 'tests', 'refs', 'pytest'))):
                raise ValueError('Static-only executable validation must explicitly disable all executable phases')
            if policy.get('required') is True:
                raise ValueError('Static-only validation cannot also be required')
            reason = validation.get('static_justification')
            if (not isinstance(reason, str) or not 12 <= len(reason.strip()) <= 320):
                raise ValueError('Static-only executable validation needs explicit bounded justification')
            return NativeValidationPlan(PLAN_SCHEMA, 'static-only', False, (), 0,
                                        'static-only-not-runtime-qualified')
        if policy.get('required') is not True or not checks:
            raise ValueError('Executable overlay requires a nonempty native validation plan')
    if static:
        raise ValueError('Static-only mode is reserved for explicit executable profile')
    required = bool(policy.get('required'))
    return NativeValidationPlan(PLAN_SCHEMA, 'required' if required else 'legacy',
                                required, tuple(dict.fromkeys(checks)), len(checks),
                                'pending-native-execution' if required else 'not-required')

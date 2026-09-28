"""Bridge first-class TestSpec declarations into the existing Devtool operation/progress pipeline."""
from __future__ import annotations
from .models import CommandSpec, ProjectConfig

def select_tests(config: ProjectConfig, names=(), *, all_tests=False):
    declared=dict(config.tests or {})
    wanted=list(dict.fromkeys(str(x) for x in names if str(x).strip()))
    if all_tests: wanted=list(declared)
    if not wanted: return []
    missing=[x for x in wanted if x not in declared]
    if missing: raise SystemExit("Unknown test declaration(s): "+", ".join(missing)+". Available: "+(", ".join(sorted(declared)) or "none"))
    return [declared[x] for x in wanted]

def command_specs(config: ProjectConfig, names=(), *, all_tests=False):
    specs=[]
    for t in select_tests(config,names,all_tests=all_tests):
        cwd=t.cwd or str(config.project_root or config.repo_root)
        env=dict(config.env); env.update(dict(t.env))
        specs.append(CommandSpec(
            name=f"test:{t.id}",
            argv=list(t.command),
            cwd=cwd,
            env=env,
            metadata={
                "test_spec_id": t.id,
                "test_runner": t.runner,
                "runner": t.runner,
                "canonical_test": True,
                "required": t.required,
                "timeout_seconds": t.timeout_seconds,
                "requires_device": t.requires_device,
                "requires_network": t.requires_network,
                "test_selector": t.selector,
                "test_aliases": list(t.aliases),
                "test_behavior": t.behavior,
                "test_lanes": list(t.lanes),
                "test_resources": list(t.resources),
                "test_parallel_safety": t.parallel_safety,
                "test_required_tools": list(t.required_tools),
                "test_required_capabilities": list(t.required_capabilities),
                "test_budget_class": t.budget_class,
                "test_contract_level": t.contract_level,
                "test_groups": list(t.groups),
                "test_dependencies": list(t.dependencies),
            },
        ))
    return specs

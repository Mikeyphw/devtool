from __future__ import annotations

from .artifacts import artifact_validation_refs, artifact_validation_tests
from .config import load_project_config
from .models import CommandSpec
from .runners import run_specs
from .test_declarations import command_specs


def _load_validation_config(repo_root, manifest):
    config = load_project_config(str(repo_root), None, allow_ambiguous_target=True)
    artifact_target = str((manifest or {}).get("target") or "").strip()
    if artifact_target and artifact_target in config.targets and config.selected_target != artifact_target:
        config = load_project_config(
            str(repo_root),
            None,
            target_name=artifact_target,
            allow_ambiguous_target=True,
        )
    return config


def _run_specs(config, specs, *, verbose, dry_run, log_path, tasker_progress):
    return run_specs(
        config,
        specs,
        verbose=verbose,
        dry_run=dry_run,
        continue_on_error=True,
        log_path=log_path,
        tasker_progress=tasker_progress,
    )


def _run_validation_ref(config, declaration, *, verbose, dry_run, log_path, tasker_progress):
    kind = str(declaration.get("kind") or "")
    name = str(declaration.get("name") or "")
    ref = str(declaration.get("ref") or f"{kind}:{name}")
    if kind == "test":
        specs = command_specs(config, [name])
        for spec in specs:
            spec.metadata = {**dict(spec.metadata or {}), "artifact_validation_ref": ref}
        return _run_specs(
            config, specs, verbose=verbose, dry_run=dry_run,
            log_path=log_path, tasker_progress=tasker_progress,
        )

    if kind == "workflow":
        from .workflow_runtime import build_workflow_specs
        from .workflow_scheduler import run_workflow_specs

        specs = build_workflow_specs(config, name)
        for spec in specs:
            spec.metadata = {
                **dict(spec.metadata or {}),
                "artifact_validation_ref": ref,
                "artifact_validation_workflow": name,
            }
        results, raw, plan = run_workflow_specs(
            config,
            specs,
            verbose=verbose,
            dry_run=dry_run,
            continue_on_error=True,
            log_path=log_path,
            tasker_progress=tasker_progress,
        )
        for result in results:
            result.metadata = {
                **dict(result.metadata or {}),
                "artifact_validation_ref": ref,
                "artifact_validation_workflow": name,
                "artifact_validation_workflow_parallel": bool(plan and plan.parallel_effective),
            }
        return results, raw

    raise ValueError(f"Unsupported artifact validation ref kind: {kind!r}")


def run_artifact_tests(repo_root, manifest, *, verbose=False, dry_run=False, log_path=None, tasker_progress=None):
    declarations = artifact_validation_tests(manifest)
    references = artifact_validation_refs(manifest)
    if not declarations and not references:
        return [], ""
    config = _load_validation_config(repo_root, manifest)
    results = []
    raw_sections = []

    specs = []
    for declaration in declarations:
        if "ref" in declaration:
            specs.extend(command_specs(config, [declaration["ref"]]))
            continue
        cwd = declaration.get("cwd") or str(config.project_root or config.repo_root)
        specs.append(CommandSpec(
            name=f"artifact-test:{declaration['id']}",
            argv=list(declaration["command"]),
            cwd=cwd,
            env=dict(config.env),
            metadata={
                "test_spec_id": declaration["id"],
                "test_runner": declaration["runner"],
                "runner": declaration["runner"],
                "canonical_test": True,
                "artifact_validation_test": True,
                "required": declaration["required"],
            },
        ))
    if specs:
        local_results, raw = _run_specs(
            config, specs, verbose=verbose, dry_run=dry_run,
            log_path=log_path, tasker_progress=tasker_progress,
        )
        results.extend(local_results)
        if raw:
            raw_sections.append(raw)

    for declaration in references:
        local_results, raw = _run_validation_ref(
            config, declaration, verbose=verbose, dry_run=dry_run,
            log_path=log_path, tasker_progress=tasker_progress,
        )
        results.extend(local_results)
        if raw:
            raw_sections.append(raw)

    return results, "\n\n".join(raw_sections)

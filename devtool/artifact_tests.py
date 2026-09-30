from __future__ import annotations

from .artifacts import artifact_validation_refs, artifact_validation_tests
from .config import SUPPORTED_RUNNERS, load_project_config
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
        runner = str(declaration.get("runner") or "").strip()
        if runner not in SUPPORTED_RUNNERS or runner in {"auto", "unknown"}:
            supported = ", ".join(sorted(item for item in SUPPORTED_RUNNERS if item not in {"auto", "unknown"}))
            raise ValueError(
                f"Artifact validation test {declaration.get('id')!r} declares unsupported runner "
                f"{runner!r}. Supported runners: {supported}"
            )
        cwd = declaration.get("cwd") or str(config.project_root or config.repo_root)
        test_metadata = {
            "test_spec_id": declaration["id"],
            "test_runner": declaration["runner"],
            "runner": declaration["runner"],
            "canonical_test": True,
            "artifact_validation_test": True,
            "required": declaration["required"],
        }
        task = str(declaration.get("task") or "").strip()
        if task:
            from .project_runners import build_gradle_task_specs
            planned = build_gradle_task_specs(
                config, declaration["runner"], [task],
                name=f"artifact-test:{declaration['id']}",
                action="test",
                env_override=dict(config.env),
                root_override=cwd,
            )
            for spec in planned:
                spec.metadata = {
                    **dict(spec.metadata or {}),
                    **test_metadata,
                    "gradle_semantic_request": True,
                    "artifact_gradle_task": task,
                }
                specs.append(spec)
            continue
        specs.append(CommandSpec(
            name=f"artifact-test:{declaration['id']}",
            argv=list(declaration["command"]),
            cwd=cwd,
            env=dict(config.env),
            metadata=test_metadata,
        ))
    if specs:
        from .gradle_ingress import converge_gradle_specs
        specs = converge_gradle_specs(config, specs, default_origin="artifact")
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

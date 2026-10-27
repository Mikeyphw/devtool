"""ACTIONS-A03: reviewed cross-repository GitHub Actions adoption over native Devtool DAG.

The generator does not create another scheduler. It validates target-local jobs,
uses the native preflight/invoke DAG, and pins every external action and the
Devtool runtime to immutable Git commits. No GitHub API mutation occurs here.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any

from .config import load_wrapper_project_config
from .target_local_adoption import target_local_adoption_report

_IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_CHECKOUT_SHA = '11bd71901bbe5b1630ceea73d27597364c9af683'  # actions/checkout 4.2.2
_UPLOAD_SHA = 'ea165f8d65b6e75b540449e92b4886f43607fa02'  # actions/upload-artifact 4.x
_SETUP_GO_SHA = 'd35c59abb061a4a6fb18e82ac0862c26744d6ab5'  # actions/setup-go v5.5.0


class AdoptionError(ValueError):
    pass


def _safe_slug(value: str, *, role: str) -> str:
    if not isinstance(value, str) or not _IDENT.fullmatch(value):
        raise AdoptionError(f'{role} must be a simple ASCII target/workflow identifier')
    return value


def _sha(value: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise AdoptionError('Devtool source must be a full lowercase 40-character Git SHA')
    return value


def _git(root: Path, *args: str) -> str:
    p = subprocess.run(['git', '-C', str(root), *args], text=True, capture_output=True, timeout=12)
    if p.returncode:
        raise AdoptionError('Repository requires an accessible Git worktree')
    return p.stdout.strip()


def _inside_repo(root: Path, rel: str) -> Path:
    # Never follow an existing symlink in any path component.
    here = root
    for part in Path(rel).parts:
        here = here / part
        if here.is_symlink():
            raise AdoptionError('Adoption destination cannot contain symlinks')
    return here


def _selection(root: Path, target: str, workflow: str) -> dict[str, Any]:
    # Reuse Devtool's actual TOML parser and its target-local identity model.
    try:
        cfg = load_wrapper_project_config(str(root), None)
        report = target_local_adoption_report(cfg, selected_target=target)
    except (ValueError, SystemExit) as e:
        raise AdoptionError(f'Invalid Devtool project contract: {e}') from None
    if target not in cfg.targets:
        raise AdoptionError('Selected target is not configured')
    t = cfg.targets[target]
    if workflow not in t.workflows:
        raise AdoptionError('Selected workflow is not configured under the target')
    wf = next((w for w in report['targets'][target]['workflows'] if w['name'] == workflow), None)
    if not wf or wf['target_local_job_count'] < 1:
        raise AdoptionError('Workflow must reference at least one real target-local job')
    if not t.jobs:
        raise AdoptionError('Target-local jobs are absent')
    # A09: admit only workflows for which the generated Ubuntu host can
    # provision every selected target-local job. Never infer an Android device,
    # root, SSH worker, container engine or release signer from the CI runner.
    use_go = False
    selected_jobs: list[dict[str, Any]] = []
    for step in t.workflows[workflow].steps:
        if step.kind != 'job' or step.resolution_scope != 'target':
            raise AdoptionError('A09 CI requires direct target-local jobs; nested/external workflows need review')
        key = step.resolution_local_name or step.name
        job = t.jobs.get(key)
        if job is None:
            raise AdoptionError('A09 CI references an unknown local job')
        meta = job.metadata or {}
        if (job.runner not in ('command', 'python', 'go') or
                meta.get('device_required') is True or
                meta.get('root_required') is True or
                meta.get('release_signed') is True or
                meta.get('device_qualification') is True):
            raise AdoptionError('A09 CI refuses device, root, signer or unsupported runner jobs')
        # NEXT-02: shared explicit OS/architecture/device/signing admission.
        # Host declaration does not override A09's existing argv/toolchain checks.
        from .evidence_kernel import host_eligibility
        eligibility = host_eligibility(meta, runner=job.runner, host_os='linux',
            host_arch='x86_64', toolchains=('go', 'python-shell'))
        if not eligibility['eligible']:
            raise AdoptionError('NEXT-02 host admission blocked '+key+': '+ '; '.join(eligibility['reasons']))
        args = list(job.command or [])
        if not args:
            raise AdoptionError('A09 CI requires an argv-based job, not an opaque shell/action')
        program = Path(args[0]).name
        if program in ('su', 'sudo', 'adb', 'ksud', 'magisk', 'mount', 'insmod',
                       'gradle', 'gradlew', 'docker', 'podman'):
            raise AdoptionError('A09 CI refuses privileged or device-build command: '+program)
        if program not in ('bash', 'sh', 'python', 'python3', 'go'):
            raise AdoptionError('A09 CI cannot prove host tool availability: '+program)
        # Toolchain flags are declarations, not a general-purpose host installer.
        # In particular, never silently admit a JDK/Android/NDK profile through
        # the Go or Python-shell fallback.
        toolchain = meta.get('ci_toolchain')
        if toolchain not in (None, 'go', 'python-shell'):
            raise AdoptionError('A09 CI host toolchain is unsupported: '+str(toolchain)[:64])
        if toolchain == 'python-shell' and (program == 'go' or job.runner == 'go'):
            raise AdoptionError('A09 CI host toolchain declaration conflicts with Go runner')
        # A shell script can call Go internally; a declared go.mod input or an
        # explicit ci_toolchain metadata flag carries the contract, not a guess.
        go_job = (program == 'go' or job.runner == 'go' or
                  meta.get('ci_toolchain') == 'go' or 'go.mod' in job.inputs)
        if go_job and toolchain == 'python-shell':
            raise AdoptionError('A09 CI host toolchain declaration conflicts with required Go SDK')
        if go_job:
            if meta.get('read_only') is not True:
                raise AdoptionError('A09 Go adoption requires explicit read_only job metadata')
            mod = root / 'go.mod'
            if not mod.is_file() or mod.is_symlink() or mod.stat().st_size > 65536:
                raise AdoptionError('A09 Go adoption requires a regular, bounded go.mod')
            if not re.search(r'(?m)^go\s+[0-9]+\.[0-9]+(?:\.[0-9]+)?\s*$', mod.read_text('utf-8')):
                raise AdoptionError('A09 Go adoption requires a valid go directive')
        selected_jobs.append({
            'name': key,
            'runner': job.runner,
            'program': program,
            'declared_read_only': meta.get('read_only') is True,
            'host_toolchain': 'go' if go_job else 'python-shell',
            'argv_sha256': hashlib.sha256(json.dumps(args, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest(),
            'inputs_sha256': hashlib.sha256(json.dumps(list(job.inputs or []), ensure_ascii=True, separators=(',', ':')).encode()).hexdigest(),
        })
        use_go |= go_job
    return {'report': report, 'workflow': wf,
            'selected_jobs': selected_jobs,
            'target_local_job_count': len(t.jobs),
            'ci_host_profile': 'go' if use_go else 'python-shell'}


def _yaml(target: str, workflow: str, source_sha: str, ci_host_profile: str = 'python-shell') -> str:
    # Inputs are strictly bounded ASCII identifiers and SHA, not arbitrary YAML.
    name = f'devtool-{target}-{workflow}'
    if ci_host_profile not in ('python-shell', 'go'):
        raise AdoptionError('A09 CI host profile is not supported')
    go_setup = (f'''      - name: Provision pinned Go SDK for source-only jobs
        uses: actions/setup-go@{_SETUP_GO_SHA}
        with:
          go-version-file: go.mod
          cache: false
      - name: Verify Go host toolchain
        shell: bash
        run: |
          set -euo pipefail
          command -v go
          go version
''' if ci_host_profile == 'go' else '')
    header = 'ACTIONS-A03' if ci_host_profile == 'python-shell' else 'ACTIONS-A03/A09'
    return f'''# Generated by Devtool {header}. Do not change pinned source without re-review.
name: {name}
on:
  workflow_dispatch:
permissions:
  contents: read
concurrency:
  group: {name}-${{{{ github.ref }}}}
  cancel-in-progress: false
jobs:
  run-dag:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    env:
      DEVTOOL_SOURCE_SHA: {source_sha}
      PYTHONDONTWRITEBYTECODE: "1"
    steps:
      - name: Checkout exact consumer source
        uses: actions/checkout@{_CHECKOUT_SHA}
        with:
          persist-credentials: false
      - name: Verify consumer source identity
        shell: bash
        run: |
          set -euo pipefail
          test "$(git rev-parse HEAD)" = "$GITHUB_SHA"
          test -f .devtool.toml
{go_setup}      - name: Acquire frozen Devtool runtime
        shell: bash
        run: |
          set -euo pipefail
          git clone --no-checkout https://github.com/Mikeyphw/devtool.git "$RUNNER_TEMP/devtool-source"
          git -C "$RUNNER_TEMP/devtool-source" checkout --detach "$DEVTOOL_SOURCE_SHA"
          test "$(git -C "$RUNNER_TEMP/devtool-source" rev-parse HEAD)" = "$DEVTOOL_SOURCE_SHA"
          python3 -m venv "$RUNNER_TEMP/devtool-venv"
          "$RUNNER_TEMP/devtool-venv/bin/python" -m pip install --disable-pip-version-check "$RUNNER_TEMP/devtool-source"
      - name: Verify target-local job plan
        shell: bash
        run: |
          set -euo pipefail
          "$RUNNER_TEMP/devtool-venv/bin/python" -m devtool --repo "$GITHUB_WORKSPACE" plan --target {target} {workflow} --json > "$RUNNER_TEMP/devtool-plan.json"
          "$RUNNER_TEMP/devtool-venv/bin/python" -m devtool --repo "$GITHUB_WORKSPACE" preflight --target {target} --workflow {workflow} --json > "$RUNNER_TEMP/devtool-preflight.json"
      - name: Run native Devtool DAG
        shell: bash
        run: |
          set -euo pipefail
          # invoke has no --json subcommand option. Its global --machine-file
          # produces the canonical EXO-aware execution envelope even on failure.
          "$RUNNER_TEMP/devtool-venv/bin/python" -m devtool --repo "$GITHUB_WORKSPACE" --machine-file "$RUNNER_TEMP/devtool-invoke.json" invoke --target {target} {workflow}
      - name: Record pinned plan checksum
        if: always()
        shell: bash
        run: |
          set -euo pipefail
          mkdir -p "$RUNNER_TEMP/devtool-adoption-evidence"
          printf '%s\\n' "$GITHUB_SHA" > "$RUNNER_TEMP/devtool-adoption-evidence/consumer-source.txt"
          printf '%s\\n' "$DEVTOOL_SOURCE_SHA" > "$RUNNER_TEMP/devtool-adoption-evidence/devtool-source.txt"
          for f in devtool-plan.json devtool-preflight.json devtool-invoke.json; do
            if test -f "$RUNNER_TEMP/$f"; then
              cp "$RUNNER_TEMP/$f" "$RUNNER_TEMP/devtool-adoption-evidence/$f"
            fi
          done
          (cd "$RUNNER_TEMP/devtool-adoption-evidence" && sha256sum ./* > SHA256SUMS)
      - name: Upload evidence (not a release artifact)
        if: always()
        uses: actions/upload-artifact@{_UPLOAD_SHA}
        with:
          name: {name}-evidence
          path: ${{{{ runner.temp }}}}/devtool-adoption-evidence/
          if-no-files-found: error
          retention-days: 14
'''


def _wrapper_name(target: str, workflow: str) -> str:
    name = f'ci-{target}-{workflow}'
    if len(name) > 100:
        raise AdoptionError('Generated wrapper command name exceeds 100 characters')
    return name


def _wrapper_entry(target: str, workflow: str) -> str:
    name = _wrapper_name(target, workflow)
    return (f'\n# ACTIONS-A03: generated native DAG wrapper alias.\n'
            f'[wrapper.commands.{name}]\n'
            f'description = "Run reviewed {target}/{workflow} job graph locally"\n'
            f'workflow = "{workflow}"\n'
            f'target = "{target}"\n')


def _atomic(path: Path, content: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.adopt-')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def adopt(root: Path, *, target: str, workflow: str, devtool_sha: str,
          write: bool = False, verify: bool = False) -> dict[str, Any]:
    root = root.resolve()
    if write and verify:
        raise AdoptionError('Choose write or verification; not both')
    target, workflow, devtool_sha = (_safe_slug(target, role='Target'),
                                     _safe_slug(workflow, role='Workflow'), _sha(devtool_sha))
    if not root.is_dir() or _git(root, 'rev-parse', '--is-inside-work-tree') != 'true':
        raise AdoptionError('Adoption requires a Git repository')
    selected = _selection(root, target, workflow)
    name = f'devtool-{target}-{workflow}'
    relative = f'.github/workflows/{name}.yml'
    file = _inside_repo(root, relative)
    config = _inside_repo(root, '.devtool.toml')
    wrapper = _inside_repo(root, 'devtoolw')
    if not config.is_file():
        raise AdoptionError('Repository .devtool.toml is required')
    if not wrapper.is_file() or not os.access(wrapper, os.X_OK):
        raise AdoptionError('Repository devtoolw must be installed and executable (devtool wrapper init)')
    workflow_bytes = _yaml(target, workflow, devtool_sha, selected['ci_host_profile']).encode('utf-8')
    config_bytes = config.read_bytes()
    consumer_head = _git(root, 'rev-parse', 'HEAD')
    consumer_tree = _git(root, 'rev-parse', 'HEAD^{tree}')
    source_clean = not bool(_git(root, 'status', '--porcelain', '--untracked-files=normal'))
    # Bind an operator's plan to actual consumer source rather than only a
    # reviewed Devtool runtime SHA. A report is not remote attestation.
    reviewed_source = {
        'consumer_commit': consumer_head,
        'consumer_tree': consumer_tree,
        'config_sha256': hashlib.sha256(config_bytes).hexdigest(),
        'wrapper_sha256': hashlib.sha256(wrapper.read_bytes()).hexdigest(),
        'workflow_sha256': hashlib.sha256(workflow_bytes).hexdigest(),
        'selected_jobs': selected['selected_jobs'],
        'ci_host_profile': selected['ci_host_profile'],
        'devtool_source_sha': devtool_sha,
    }
    review_sha = hashlib.sha256(json.dumps(reviewed_source, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if b'\x00' in config_bytes:
        raise AdoptionError('Repository configuration is invalid')
    name = _wrapper_name(target, workflow)
    marker = f'[wrapper.commands.{name}]'.encode()
    exists = marker in config_bytes
    # A generated alias never overrides a pre-existing, possibly hand-authored alias.
    if exists and not verify:
        raise AdoptionError('Wrapper alias already exists; do not overwrite repository config')
    updated_config = config_bytes + (b'' if config_bytes.endswith(b'\n') else b'\n') + _wrapper_entry(target, workflow).encode()
    digest = hashlib.sha256(workflow_bytes).hexdigest()
    result: dict[str, Any] = {
        'schema_version': 1, 'producer': 'devtool-actions-a03-adoption',
        'target': target, 'workflow': workflow, 'source_sha': devtool_sha,
        'workflow_path': relative, 'workflow_sha256': digest,
        'target_local_jobs': selected['workflow']['target_local_job_count'],
        'ci_host_profile': selected['ci_host_profile'], 'device_qualified': False,
        'source_attested': False,
        'consumer_commit': consumer_head, 'consumer_tree': consumer_tree,
        'consumer_worktree_clean': source_clean,
        'review_sha256': review_sha,
        'selected_jobs': selected['selected_jobs'],
        'host_prerequisites': ['python3', 'bash', 'sh'] + (['go', 'go.mod'] if selected['ci_host_profile'] == 'go' else []),
        'host_admission': 'SOURCE_DECLARATION_ONLY_NOT_TOOLCHAIN_EXECUTION',
        'wrapper_command': name, 'wrapper_command_example': f'./devtoolw {name}',
        'writes_performed': False, 'remote_dispatch': False, 'release_qualified': False,
    }
    if verify:
        # Verification must check both workflow SHA and matching committed alias.
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != digest:
            raise AdoptionError('Generated workflow absent, changed or mismatched')
        if not exists or _wrapper_entry(target, workflow).encode('utf-8') not in config_bytes:
            raise AdoptionError('Generated wrapper alias missing or changed')
        result['overall'] = 'PASS_ACTIONS_A03_ADOPTION_VERIFIED'
        return result
    if not write:
        if file.exists() or file.is_symlink():
            raise AdoptionError('Refusing to suggest overwriting existing workflow')
        result['overall'] = 'PASS_ACTIONS_A03_REVIEW_ONLY'
        result['pending_files'] = [relative, '.devtool.toml']
        return result
    if file.exists() or file.is_symlink():
        raise AdoptionError('Workflow already exists; no overwrite')
    if not source_clean:
        raise AdoptionError('Adoption writes require a clean Git worktree')
    if _git(root, 'rev-parse', 'HEAD') != consumer_head or config.read_bytes() != config_bytes or hashlib.sha256(wrapper.read_bytes()).hexdigest() != reviewed_source['wrapper_sha256']:
        raise AdoptionError('Adoption source changed after review')
    # Re-validate appended configuration before writing; no second parser authority.
    import tomllib
    try:
        tomllib.loads(updated_config.decode('utf-8'))
    except (ValueError, UnicodeDecodeError) as e:
        raise AdoptionError(f'Generated wrapper entry would invalidate TOML: {e}') from None
    if _selection(root, target, workflow)['selected_jobs'] != selected['selected_jobs']:
        raise AdoptionError('Adoption job selection changed after review')
    # Write workflow first and config second; rollback our own workflow on config failure.
    try:
        _atomic(file, workflow_bytes)
        _atomic(config, updated_config)
    except Exception:
        if file.exists() and file.read_bytes() == workflow_bytes:
            file.unlink()
        raise
    result['overall'] = 'PASS_ACTIONS_A03_ADOPTION_WRITTEN'
    result['writes_performed'] = True
    return result



def adoption_doctor(root: Path, *, target: str = "") -> dict[str, Any]:
    """Read-only A09 host-admission inventory; never generate or verify remote evidence.

    A ready entry means only that existing A03/A09 *local eligibility* is
    satisfied.  It is not a statement about installed CI, hosting, or release.
    """
    root = root.resolve()
    if target:
        _safe_slug(target, role='Target')
    if not root.is_dir() or _git(root, 'rev-parse', '--is-inside-work-tree') != 'true':
        raise AdoptionError('Adoption doctor requires a Git repository')
    config = _inside_repo(root, '.devtool.toml')
    wrapper = _inside_repo(root, 'devtoolw')
    if not config.is_file() or not wrapper.is_file() or not os.access(wrapper, os.X_OK):
        raise AdoptionError('Adoption doctor requires .devtool.toml and an executable devtoolw')
    try:
        cfg = load_wrapper_project_config(str(root), None)
    except (ValueError, SystemExit) as exc:
        raise AdoptionError(f'Cannot read Devtool project contract: {str(exc)[:160]}') from None
    if target and target not in cfg.targets:
        raise AdoptionError('Selected target is not configured')
    clean = not bool(_git(root, 'status', '--porcelain', '--untracked-files=normal'))
    observations: list[dict[str, Any]] = []
    for target_name, t in sorted(cfg.targets.items()):
        if target and target != target_name:
            continue
        for workflow_name in sorted(t.workflows):
            entry: dict[str, Any] = {
                'target': target_name, 'workflow': workflow_name,
                'status': 'blocked', 'blockers': [],
                'host_profile': None, 'selected_jobs': [],
                'workflow_path': f'.github/workflows/devtool-{target_name}-{workflow_name}.yml',
            }
            try:
                _safe_slug(target_name, role='Target')
                _safe_slug(workflow_name, role='Workflow')
                selection = _selection(root, target_name, workflow_name)
                entry['host_profile'] = selection['ci_host_profile']
                entry['selected_jobs'] = selection['selected_jobs']
                entry['status'] = 'locally_eligible'
            except (AdoptionError, ValueError) as exc:
                entry['blockers'].append(str(exc)[:180])
            # Never construct an unchecked path from an invalid workflow name.
            if not _IDENT.fullmatch(target_name) or not _IDENT.fullmatch(workflow_name):
                observations.append(entry)
                continue
            # Existing files must be reviewed, never overwritten by adoption.
            try:
                path = _inside_repo(root, entry['workflow_path'])
            except AdoptionError as exc:
                entry['status'] = 'review_required'
                entry['blockers'].append(str(exc)[:180])
                observations.append(entry)
                continue
            alias = _wrapper_name(target_name, workflow_name) if entry['status'] == 'locally_eligible' else ''
            if path.exists() or path.is_symlink():
                entry['status'] = 'review_required'
                entry['blockers'].append('Workflow destination exists; use explicit adoption verification')
            if alias and f'[wrapper.commands.{alias}]'.encode() in config.read_bytes():
                entry['status'] = 'review_required'
                entry['blockers'].append('Wrapper alias exists; use explicit adoption verification')
            if not clean and entry['status'] == 'locally_eligible':
                entry['status'] = 'review_required'
                entry['blockers'].append('Git worktree is not clean; adoption writes are forbidden')
            observations.append(entry)
    counts = {status: sum(item['status'] == status for item in observations)
              for status in ('locally_eligible', 'review_required', 'blocked')}
    return {
        'schema_version': 1, 'producer': 'devtool-actions-r06b-adoption-doctor',
        'read_only': True, 'remote_checked': False, 'source_attested': False,
        'hosted_qualified': False, 'device_qualified': False,
        'release_qualified': False,
        'consumer_commit': _git(root, 'rev-parse', 'HEAD'),
        'consumer_tree': _git(root, 'rev-parse', 'HEAD^{tree}'),
        'consumer_worktree_clean': clean,
        'selected_target': target, 'counts': counts, 'workflows': observations,
        'qualification': 'LOCAL_REVIEW_ONLY',
        'next_step': 'Use devtool actions adopt --target TARGET --workflow WORKFLOW --devtool-sha FULL_SHA for exact source-bound review.',
    }

def adoption_command(root: Path, args: Any) -> dict[str, Any]:
    return adopt(root,
                 target=str(getattr(args, 'adopt_target', '') or ''),
                 workflow=str(getattr(args, 'adopt_workflow', '') or ''),
                 devtool_sha=str(getattr(args, 'devtool_sha', '') or ''),
                 write=bool(getattr(args, 'yes', False)),
                 verify=bool(getattr(args, 'verify', False)))


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, default=Path.cwd())
    p.add_argument('--target', required=True)
    p.add_argument('--workflow', required=True)
    p.add_argument('--devtool-sha', required=True)
    p.add_argument('--yes', action='store_true')
    p.add_argument('--verify', action='store_true')
    args = p.parse_args(argv)
    try:
        result = adopt(args.repo, target=args.target, workflow=args.workflow,
                       devtool_sha=args.devtool_sha, write=args.yes, verify=args.verify)
    except AdoptionError as exc:
        p.exit(1, f'ACTIONS_A03_REFUSED: {exc}\n')
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

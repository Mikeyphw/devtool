"""DN03-R3: native .NET workflow export with post-execution node re-verification.

Extends DN03-R2's compiler, not the native RM-06 scheduler. Generated workflows
fail closed if typed node evidence, source graph, output bytes or host receipt
differ after the actual native command. Does not authenticate GitHub artifacts.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any

from . import actions_dn03_dotnet as dn03


class R3ExportError(ValueError):
    pass


def _verification_step(job: dict[str, Any], *, target: str, workflow: str,
                       devtool_sha: str, graph_sha: str) -> str:
    for value in (target, workflow, job.get("id", "")):
        if not isinstance(value, str) or not dn03.SLUG.fullmatch(value):
            raise R3ExportError("unreviewed target/workflow/native node")
    if not dn03.SHA.fullmatch(devtool_sha) or not dn03.DIGEST.fullmatch(graph_sha):
        raise R3ExportError("R3 requires immutable Devtool source and graph pins")
    return (
        "      - name: DN03-R3 reverify native .NET node, host and output bytes\n"
        "        shell: bash\n"
        "        run: |\n"
        "          set -euo pipefail\n"
        '          test "${GITHUB_ACTIONS:-}" = true\n'
        '          test "${RUNNER_OS:-}" = Linux\n'
        '          test "${RUNNER_ARCH:-}" = X64\n'
        f'          test "$DEVTOOL_SOURCE_SHA" = "{devtool_sha}"\n'
        '          "$RUNNER_TEMP/devtool-venv/bin/python" -m devtool.actions_dn03_r3_verify verify-node \\\n'
        '            --repo "$GITHUB_WORKSPACE" \\\n'
        f'            --target {target} --workflow {workflow} \\\n'
        f'            --devtool-sha {devtool_sha} --graph-sha {graph_sha} \\\n'
        f'            --job-id {job["id"]} \\\n'
        '            --outputs "$RUNNER_TEMP/rm06-output"\n'
    )


def _enhance_native_workflow(source: str, jobs: list[dict[str, Any]], *,
                             target: str, workflow: str, devtool_sha: str,
                             graph_sha: str) -> str:
    if not jobs or len(jobs) > 24:
        raise R3ExportError("R3 requires 1–24 native nodes")
    positions = []
    for job in jobs:
        marker = f"  {job['id']}:\n"
        if source.count(marker) != 1:
            raise R3ExportError("R3 native job header missing or duplicated")
        positions.append((source.index(marker), job))
    if positions != sorted(positions, key=lambda row: row[0]):
        raise R3ExportError("R3 job order differs from frozen native graph")
    out = [source[:positions[0][0]]]
    for idx, (start, job) in enumerate(positions):
        end = positions[idx+1][0] if idx+1 < len(positions) else len(source)
        section = source[start:end]
        if section.count('      - name: Verify dependencies and run native target-local node\n') != 1:
            raise R3ExportError("R3 cannot identify the native RM-06 execution step")
        if section.count('      - name: DN03 verify .NET host and record local source-bound evidence\n') != 1:
            raise R3ExportError("R3 missing source-bound DN03 host receipt")
        if section.count('      - name: Record local run attempt (not authentication)\n') != 1:
            raise R3ExportError("R3 missing original RM-06 evidence upload anchor")
        if '      - name: DN03-R3 reverify native .NET node' in section:
            raise R3ExportError("R3 verifier insertion must be create-only")
        # Strictly before any output bytes are exported; a failed verification
        # therefore blocks downstream handoff artifacts from this job.
        upload_anchor = f'      - name: Upload {job["id"]} declared bytes (not authenticated)\n'
        anchor = upload_anchor if job.get("outputs") else '      - name: Record local run attempt (not authentication)\n'
        if section.count(anchor) != 1:
            raise R3ExportError("R3 output/attempt boundary missing or duplicated")
        run_pos = section.index('      - name: Verify dependencies and run native target-local node\n')
        position = section.index(anchor)
        if position <= run_pos:
            raise R3ExportError("R3 native run/output ordering invalid")
        enhanced = section.replace(anchor, _verification_step(job, target=target,
                                   workflow=workflow, devtool_sha=devtool_sha,
                                   graph_sha=graph_sha) + anchor, 1)
        out.append(enhanced)
    return ''.join(out)


def compile_r3(root: Path, *, target: str, workflow: str,
               devtool_sha: str) -> tuple[str, dict[str, Any]]:
    source, previous = dn03.compile_dn03(root, target=target,
                                         workflow=workflow, devtool_sha=devtool_sha)
    # Independent, read-only recompile: R2 already refuses unsupported .NET
    # providers/hosts. Any drift between compilers fails before producing bytes.
    graph = dn03.compile_graph(root.resolve(), target=target, workflow=workflow,
                               devtool_sha=devtool_sha,
                               host_profiles="ubuntu-24.04-x64", cache_locks=False)
    if (graph.get("graph_sha256") != previous["graph_sha256"] or
            graph.get("provider_plan_sha256") != previous["provider_plan_sha256"] or
            graph.get("runtime_sha") != devtool_sha or
            [job.get("node") for job in graph.get("jobs", [])] != previous["native_nodes"]):
        raise R3ExportError("DN03 graph changed between source review and R3 export")
    enhanced = _enhance_native_workflow(
        source, graph["jobs"], target=target, workflow=workflow,
        devtool_sha=devtool_sha, graph_sha=previous["graph_sha256"],
    )
    receipt = {**previous,
               "schema_version": 1,
               "kind": "devtool.dn03-r3-verified-native-export",
               "workflow_sha256": sha256(enhanced.encode("utf-8")).hexdigest(),
               "post_native_node_evidence_required": True,
               "native_output_sha256_reverification": True,
               "real_generated_workflow_executed": False,
               "test_reports_independently_verified": False,
               "remote_authenticated": False,
               "release_qualified": False,
               "generator": "DN03-R3 hardening of DN03-R2/RM-06 native .NET exporter"}
    return enhanced, receipt


def _output_paths(root: Path, target: str, workflow: str) -> tuple[Path, Path]:
    stem = f"devtool-dn03r3-{target}-{workflow}"
    return (dn03._path(root, '.github/workflows/' + stem + '.yml'),
            dn03._path(root, 'docs/ci/' + stem + '.json'))


def export(root: Path, *, target: str, workflow: str, devtool_sha: str,
           mode: str, confirmed: bool = False) -> dict[str, Any]:
    if mode not in ('plan', 'write', 'verify'):
        raise R3ExportError("R3 requires plan/write/verify")
    root = root.resolve()
    result, record = compile_r3(root, target=target,
                                workflow=workflow, devtool_sha=devtool_sha)
    paths = _output_paths(root, target, workflow)
    expected = ((paths[0], result.encode('utf-8')),
                (paths[1], (json.dumps(record, sort_keys=True, indent=2) + '\n').encode('utf-8')))
    if mode == 'write':
        if not confirmed:
            raise R3ExportError('R3 source export requires explicit --yes')
        if dn03._git(root, 'status', '--porcelain', '--untracked-files=all'):
            raise R3ExportError('R3 refuses dirty consumer Git worktree')
        if any(p.exists() or p.is_symlink() for p, _ in expected):
            raise R3ExportError('R3 refuses to overwrite generated workflow/receipt')
        before = dn03._git(root, 'rev-parse', 'HEAD')
        created: list[Path] = []
        try:
            for path, data in expected:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as stream:
                    stream.write(data)
                created.append(path)
            if dn03._git(root, 'rev-parse', 'HEAD') != before:
                raise R3ExportError('R3 consumer Git HEAD changed during export')
        except BaseException:
            for path in reversed(created):
                path.unlink(missing_ok=True)
            raise
    if mode == 'verify':
        if any(not p.is_file() or p.is_symlink() or p.read_bytes() != data
               for p, data in expected):
            raise R3ExportError('R3 generated workflow/receipt differs from source graph')
    return {**record, 'mode': mode,
            'wrote_files': mode == 'write',
            'verified_files': mode == 'verify',
            'workflow_path': str(paths[0].relative_to(root)),
            'receipt_path': str(paths[1].relative_to(root))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('plan', 'write', 'verify'))
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--workflow', required=True)
    parser.add_argument('--devtool-sha', required=True)
    parser.add_argument('--yes', action='store_true')
    ns = parser.parse_args(argv)
    try:
        result = export(ns.repo, target=ns.target, workflow=ns.workflow,
                        devtool_sha=ns.devtool_sha, mode=ns.mode,
                        confirmed=ns.yes)
    except (R3ExportError, dn03.DN03Error, ValueError, OSError, KeyError, TypeError) as exc:
        print('DN03_R3_REFUSED: ' + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

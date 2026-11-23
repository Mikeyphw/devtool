"""Source/attempt-bound Android G1 evidence producer on GitHub-hosted Linux only.

Never invokes Gradle on native Termux or a developer's workstation. This module
runs only in a GitHub Actions workflow_dispatch job with matching checkout HEAD.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def produce(output: Path) -> dict:
    env = os.environ
    if (env.get('GITHUB_ACTIONS') != 'true' or env.get('RUNNER_OS') != 'Linux'
        or env.get('GITHUB_EVENT_NAME') != 'workflow_dispatch'
        or env.get('GITHUB_JOB') != 'android-g1-hosted'
        or str(env.get('PREFIX', '')).startswith('/data/data/com.termux/')):
        raise RuntimeError('HOSTED_ANDROID_G1_ONLY; no local Gradle')
    repo = Path(env['GITHUB_WORKSPACE']).resolve(strict=True)
    head = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True,
                          text=True, timeout=15, check=True).stdout.strip()
    tree = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD^{tree}'], capture_output=True,
                          text=True, timeout=15, check=True).stdout.strip()
    if (head != env.get('GITHUB_SHA') or len(head) != 40
        or subprocess.run(['git', '-C', str(repo), 'status', '--porcelain=v1',
                           '--untracked-files=all'], capture_output=True,
                          text=True, timeout=15, check=True).stdout.strip()):
        raise RuntimeError('HOSTED_ANDROID_G1_SOURCE_UNCLEAN_OR_MISMATCHED')
    run_id = int(env['GITHUB_RUN_ID'])
    attempt = int(env['GITHUB_RUN_ATTEMPT'])
    if run_id <= 0 or attempt != 1:
        raise RuntimeError('HOSTED_ANDROID_G1_UNSUPPORTED_ATTEMPT')
    output = output.resolve()
    if output.is_relative_to(repo):
        raise RuntimeError('HOSTED_ANDROID_G1_OUTPUT_INSIDE_REPO')
    output.mkdir(parents=True, exist_ok=False)
    # Real ANDROID-G1 executes the Gradle fixture through the canonical runner.
    process = subprocess.run([sys.executable, '-B', '-m', 'devtool.android_g1_gate'],
                             cwd=repo, capture_output=True, timeout=900, check=False)
    log = process.stdout + b'\nSTDERR:\n' + process.stderr
    (output / 'android_g1.log').write_bytes(log)
    if process.returncode != 0 or not process.stdout.strip():
        raise RuntimeError(f'HOSTED_ANDROID_G1_FAILED rc={process.returncode}')
    report = json.loads(process.stdout)
    if (not isinstance(report, dict) or report.get('gate') != 'ANDROID-G1'
        or report.get('status') != 'passed' or not report.get('gradle_receipt_id')
        or not report.get('artifact_sha256') or not report.get('standalone_runtime_sealed')
        or report.get('host', {}).get('termux') is not False):
        raise RuntimeError('HOSTED_ANDROID_G1_UNQUALIFIED_REPORT')
    receipt = {
        'schema': 'devtool.g3r.hosted-g1.v1', 'source_commit': head,
        'source_tree': tree, 'run_id': run_id, 'run_attempt': attempt,
        'job': 'android-g1-hosted', 'runner_os': 'Linux',
        'status': 'PASS', 'exit_code': 0, 'formal_release_qualified': False,
        'output_sha256': hashlib.sha256(log).hexdigest(),
        'gradle_receipt_id': report['gradle_receipt_id'],
        'artifact_sha256': report['artifact_sha256'],
    }
    (output / 'android_g1_receipt.json').write_text(json.dumps(receipt, sort_keys=True,
                                                               indent=2) + '\n')
    return receipt


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(produce(args.output), sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'HOSTED_ANDROID_G1_REFUSED {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

"""Read-only reuse of source-only PR validation for an identical merged tree.

A failed, unavailable or ambiguous proof causes fresh tests, never skipped CI.
Native, device, release, deployment and exact-commit acceptance are excluded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from zipfile import BadZipFile, ZipFile

from .actions_artifacts import _digest, _download_blob, _metadata, check_zip
from .ci_tree_evidence import git, tree
from .github_actions import GitHubActionsClient, GitHubActionsError

REPO = 'Mikeyphw/devtool'
SCHEMA = 'devtool.postmerge-source-proof.v1'
# Exact workflows and actual named test steps, not generic successful runs.
WORKFLOWS = {
    'devtool-dn03-source-qualification.yml': ('Native .NET DAG export and negative source tests (non-release)', 'DN03 source and .NET compatibility tests'),
    'devtool-dn03-r3-source-qualification.yml': ('DN03-R3 source and adversarial native receipt tests (non-release)', 'Verify R3 exporter, node verifier and prior DN gates'),
    'devtool-dn03-r4-source-qualification.yml': ('DN03-R4 source, generated YAML and adversarial evidence (non-release)', 'Test DN03-R4 with earlier RM-09 source regressions'),
    'devtool-dn03-r6-trx-source-qualification.yml': ('Native TRX parsing, digest and cross-node regression (non-release)', 'Verify TRX and prior native source contracts'),
    'devtool-w02-portable-dotnet-source.yml': ('native-graph-source', 'W02 .NET mixed-host source and compatibility regression'),
    'devtool-portable-ci-qualification.yml': ('Devtool portable CI verifier / {scope}', 'Run positive and negative CI evidence source regressions'),
}
SHA = re.compile(r'[a-f0-9]{40}\Z')
HEX = re.compile(r'[a-f0-9]{64}\Z')


def need(value, why):
    if not value:
        raise GitHubActionsError(why)


def repo_name(obj):
    return isinstance(obj, dict) and str(obj.get('full_name', '')).casefold() == REPO.casefold()


def sha(value):
    need(isinstance(value, str) and SHA.fullmatch(value), 'Invalid source identity')
    return value


def positive(value):
    need(type(value) is int and value > 0, 'Invalid run or PR identity')
    return value


def artifact_name(run, attempt, scope):
    return f'devtool-tree-proof-{run}-{attempt}-{scope}'


def context(root, workflow, scope, env):
    need(workflow in WORKFLOWS and scope in ('ubuntu-24.04', 'windows-2022'), 'Unreviewed workflow scope')
    need(workflow == 'devtool-portable-ci-qualification.yml' or scope == 'ubuntu-24.04', 'Unexpected runner scope')
    need(env.get('GITHUB_REPOSITORY', '').casefold() == REPO.casefold(), 'Unexpected repository')
    commit = sha(env.get('GITHUB_SHA'))
    need(git(root, 'rev-parse', 'HEAD') == commit and not git(root, 'status', '--porcelain', '--untracked-files=no'), 'Source checkout mismatch')
    path = '.github/workflows/' + workflow
    raw = (root / path).read_bytes()
    return {'schema': SCHEMA, 'repository': REPO, 'workflow': path, 'workflow_sha256': hashlib.sha256(raw).hexdigest(),
            'scope': scope, 'source_sha': commit, 'source_tree': tree(root, commit),
            'run_id': positive(int(env['GITHUB_RUN_ID'])), 'run_attempt': positive(int(env['GITHUB_RUN_ATTEMPT'])), 'event': env.get('GITHUB_EVENT_NAME')}


def record(root, workflow, scope, env, output, reused=None):
    row = context(root, workflow, scope, env)
    row['mode'] = 'executed' if reused is None else 'reused'
    if row['event'] == 'pull_request':
        event = json.loads(Path(env['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
        pr = event['pull_request']
        need(repo_name(pr['head']['repo']) and repo_name(pr['base']['repo']) and pr['base']['ref'] == 'main', 'Foreign PR cannot seed reuse')
        row.update(pr=positive(event['number']), pr_head=sha(pr['head']['sha']), pr_base=sha(pr['base']['sha']))
    if reused is not None:
        row['reused_from'] = reused
    need(not output.is_symlink() and output.parent.is_dir() and not output.parent.is_symlink(), 'Unsafe proof output')
    output.write_text(json.dumps(row, sort_keys=True), encoding='utf-8')
    return row


def _proof(client, run, scope, folder):
    rid = positive(run['id'])
    name = artifact_name(rid, 1, scope)
    rows = [a for a in client.run_artifacts(REPO, rid) if a.get('name') == name]
    need(len(rows) == 1, 'Original tree proof unavailable or ambiguous')
    raw = client.get_artifact(REPO, positive(rows[0]['id']))
    meta = _metadata(raw, REPO, run, exact_id=rows[0]['id'])
    need(meta['name'] == name and 0 < meta['size_in_bytes'] <= 65536 and raw.get('expired') is False, 'Invalid original tree proof metadata')
    expected = _digest(meta['digest'])
    path = folder / 'proof.zip'
    actual, size = _download_blob(client, client.artifact_download_location(REPO, meta['id']), path)
    need(actual == expected and size == meta['size_in_bytes'], 'Original tree proof byte identity mismatch')
    # Bounds precede expanded-content checks.
    with ZipFile(path) as z:
        infos = z.infolist()
        need(len(infos) == 1 and infos[0].filename == 'tree-proof.json' and 0 < infos[0].file_size <= 16384, 'Invalid tree proof archive')
    check_zip(path)
    with ZipFile(path) as z:
        row = json.loads(z.read('tree-proof.json'))
    need(isinstance(row, dict), 'Invalid tree proof record')
    return row, expected


def verify_candidate(client, current, candidate, folder, workflow_id):
    rid = positive(candidate['id'])
    run = client.get_run(REPO, rid)
    need(run.get('id') == rid and run.get('workflow_id') == workflow_id and run.get('path') == current['workflow']
         and run.get('event') == 'pull_request' and run.get('run_attempt') == 1 and run.get('status') == 'completed'
         and run.get('conclusion') == 'success' and repo_name(run.get('repository')) and repo_name(run.get('head_repository')), 'Run is not an original successful same-repository PR run')
    associations = run.get('pull_requests')
    need(isinstance(associations, list) and 0 < len(associations) <= 10, 'Missing or ambiguous run PR association')
    pr_rows = [client._get(f'/repos/{REPO}/pulls/{positive(x["number"])}') for x in associations if (x.get('base') or {}).get('ref') == 'main']
    eligible = [x for x in pr_rows if x.get('merged') is True and x.get('merge_commit_sha') == current['source_sha']]
    need(len(eligible) == 1, 'Run not associated with current merge')
    row, digest = _proof(client, run, current['scope'], folder)
    expected = {'schema', 'repository', 'workflow', 'workflow_sha256', 'scope', 'source_sha', 'source_tree', 'run_id', 'run_attempt', 'event', 'mode', 'pr', 'pr_head', 'pr_base'}
    need(set(row) == expected and row['mode'] == 'executed' and row['event'] == 'pull_request' and row['run_id'] == rid and row['run_attempt'] == 1, 'Proof not from executed PR tests')
    for field in ('schema', 'repository', 'workflow', 'workflow_sha256', 'scope', 'source_tree'):
        need(row[field] == current[field], 'Tree or workflow scope differs')
    number = positive(row['pr']); old = sha(row['source_sha']); head = sha(row['pr_head']); base = sha(row['pr_base'])
    need(run.get('head_sha') == head and any(x.get('number') == number and (x.get('head') or {}).get('sha') == head and (x.get('base') or {}).get('ref') == 'main' for x in run.get('pull_requests', [])), 'PR run association not proven')
    pr = eligible[0]
    need(pr.get('number') == number and pr.get('merged') is True and pr.get('merge_commit_sha') == current['source_sha'] and pr.get('head', {}).get('sha') == head and pr.get('base', {}).get('ref') == 'main' and repo_name(pr['head'].get('repo')) and repo_name(pr['base'].get('repo')), 'Current push is not the actual merged PR')
    obj = client._get(f'/repos/{REPO}/git/commits/{old}')
    need(obj.get('sha') == old and (obj.get('tree') or {}).get('sha') == current['source_tree'] and [x.get('sha') for x in obj.get('parents', [])] == [base, head], 'PR tested merge tree not proven')
    job_name, step_name = WORKFLOWS[current['workflow'].split('/')[-1]]
    jobs = client.run_attempt_jobs(REPO, rid, 1)
    matches = [j for j in jobs if (not job_name or j.get('name') == job_name.format(scope=current['scope'])) and j.get('status') == 'completed' and j.get('conclusion') == 'success' and j.get('run_attempt', 1) == 1 and any(s.get('name') == step_name and s.get('status') == 'completed' and s.get('conclusion') == 'success' for s in j.get('steps', []))]
    need(len(matches) == 1, 'Actual original test step not proven')
    final = client.get_run(REPO, rid)
    for field in ('id', 'workflow_id', 'path', 'event', 'run_attempt', 'status', 'conclusion', 'head_sha'):
        need(final.get(field) == run.get(field), 'Original run changed during proof')
    return {'run_id': rid, 'run_attempt': 1, 'tested_source_sha': old, 'source_tree': row['source_tree'], 'proof_sha256': digest, 'pr': number}


def decide(root, workflow, scope, env, client=None):
    if env.get('GITHUB_EVENT_NAME') != 'push' or env.get('GITHUB_REF') != 'refs/heads/main' or not env.get('GH_TOKEN'):
        return None
    try:
        current = context(root, workflow, scope, env)
        client = client or GitHubActionsClient(env['GH_TOKEN'], timeout=10)
        wf = client.workflow(REPO, workflow)
        wid = positive(wf['id'])
        need(wf.get('path') == current['workflow'], 'Workflow identity differs')
        rows = client.workflow_runs(REPO, wid, limit=20)
        for candidate in rows:
            if candidate.get('event') != 'pull_request' or candidate.get('conclusion') != 'success':
                continue
            # A missing, expired or mismatched proof never authorizes reuse.
            try:
                with tempfile.TemporaryDirectory(prefix='devtool-tree-proof-') as tmp:
                    proof = verify_candidate(client, current, candidate, Path(tmp), wid)
                need(context(root, workflow, scope, env) == current, 'Current source changed during proof')
                return proof
            except (GitHubActionsError, OSError, ValueError, KeyError, TypeError, RuntimeError, AttributeError, BadZipFile):
                continue
    except (GitHubActionsError, OSError, ValueError, KeyError, TypeError, RuntimeError, AttributeError, BadZipFile):
        pass
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('decide', 'record'))
    parser.add_argument('--workflow', required=True, choices=WORKFLOWS)
    parser.add_argument('--scope', required=True, choices=('ubuntu-24.04', 'windows-2022'))
    parser.add_argument('--proof', required=True, type=Path)
    args = parser.parse_args()
    if args.action == 'decide':
        proof = decide(Path.cwd(), args.workflow, args.scope, os.environ)
        if proof is not None:
            record(Path.cwd(), args.workflow, args.scope, os.environ, args.proof, proof)
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as f:
            f.write('reuse=' + ('true' if proof is not None else 'false') + '\n')
        print('POSTMERGE_SOURCE_TREE_REUSED' if proof else 'POSTMERGE_FRESH_SOURCE_TESTS_REQUIRED')
    else:
        record(Path.cwd(), args.workflow, args.scope, os.environ, args.proof)
    return 0


if __name__ == '__main__':
    sys.exit(main())

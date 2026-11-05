"""DN03-R8: independent GitHub API correlation of R7 run and artifact bytes.

This is an authenticated GitHub-API observation and byte identity check, NOT
cryptographic authentication of R7 producers or release/signing qualification.
A separate trusted GitHub Actions job can attest the resulting JSON report.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable, Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener, urlopen
from zipfile import ZipFile

_SHA40 = re.compile(r"[a-f0-9]{40}\Z")
_SHA64 = re.compile(r"[a-f0-9]{64}\Z")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_WORKFLOW = '.github/workflows/devtool-dn03-r7-native-ci.yml'
_EXPECTED_JOBS = {'DN03-R7 / source-admitted graph (non-release)',
                  'DN03-R7 / verify 29 TRX tests (non-release)',
                  *{f'node-{i:02}' for i in range(1, 7)}}
_MAX_ARCHIVE = 16 * 1024 * 1024
_MAX_ENTRY = 8 * 1024 * 1024


class R8Refused(ValueError):
    """GitHub's independent run/artifact evidence is missing or inconsistent."""


def _must(test: bool, reason: str) -> None:
    if not test:
        raise R8Refused(reason)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _github_json(route: str) -> dict:
    _must(route.startswith('/repos/') and '?' not in route, 'unsafe GitHub API route')
    proc = subprocess.run(['gh', 'api', '--method', 'GET', '-H',
                           'Accept: application/vnd.github+json', route],
                          capture_output=True, timeout=35)
    if proc.returncode:
        raise R8Refused('authenticated GitHub API request failed: ' + route)
    _must(len(proc.stdout) <= 4 * 1024 * 1024, 'oversized GitHub API response')
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise R8Refused('invalid GitHub API JSON') from exc


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _zip_by_id(repo: str, artifact_id: int) -> bytes:
    """Use authenticated API redirect but never send the GitHub token to blob hosts."""
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    _must(bool(token), 'GitHub API token unavailable')
    url = f'https://api.github.com/repos/{repo}/actions/artifacts/{artifact_id}/zip'
    req = Request(url, headers={'Authorization': 'Bearer ' + token,
                                'Accept': 'application/vnd.github+json',
                                'X-GitHub-Api-Version': '2022-11-28'})
    try:
        with build_opener(_NoRedirect()).open(req, timeout=40) as response:
            data = response.read(_MAX_ARCHIVE + 1)
    except HTTPError as exc:
        if exc.code not in (301, 302, 303, 307, 308):
            raise R8Refused('GitHub archive API download rejected') from exc
        redirect = exc.headers.get('Location', '')
        loc = urlsplit(redirect)
        _must(loc.scheme == 'https' and bool(loc.hostname) and
              (loc.hostname.endswith('.blob.core.windows.net') or
               loc.hostname.endswith('.actions.githubusercontent.com')) and
              not loc.username and not loc.password, 'unsafe artifact redirect host')
        # Signed redirect URL; never forward the GitHub Authorization header.
        try:
            with urlopen(Request(redirect), timeout=45) as response:
                data = response.read(_MAX_ARCHIVE + 1)
        except (OSError, HTTPError) as err:
            raise R8Refused('artifact blob download failed') from err
    except OSError as exc:
        raise R8Refused('GitHub archive transport failure') from exc
    _must(0 < len(data) <= _MAX_ARCHIVE, 'archive missing or oversized')
    return data


def _entries(blob: bytes) -> dict[str, bytes]:
    try:
        with ZipFile(io.BytesIO(blob)) as archive:
            members = archive.infolist()
            _must(0 < len(members) <= 120, 'invalid archive member count')
            result: dict[str, bytes] = {}
            total = 0
            for m in members:
                if m.is_dir():
                    continue
                name = m.filename
                _must(name and '\\' not in name and not name.startswith('/') and
                      all(x not in ('', '..', '.') for x in name.split('/')) and
                      (m.external_attr >> 16) & 0o170000 != 0o120000,
                      'unsafe artifact archive path or symlink')
                _must(name not in result and m.file_size <= _MAX_ENTRY,
                      'duplicate or oversized archive member')
                total += m.file_size
                _must(total <= _MAX_ARCHIVE, 'archive uncompressed size excessive')
                result[name] = archive.read(m)
            return result
    except (OSError, ValueError, EOFError, RuntimeError) as exc:
        raise R8Refused('invalid ZIP artifact') from exc
    except Exception as exc:
        # zipfile.BadZipFile inherits ValueError in some Python versions only.
        raise R8Refused('unreadable ZIP artifact') from exc


def _one(entries: dict[str, bytes], name: str) -> bytes:
    choices = [data for path, data in entries.items()
               if path == name or path.endswith('/' + name)]
    _must(len(choices) == 1, 'required artifact member absent or ambiguous: ' + name)
    return choices[0]


def _id(v: Any) -> bool:
    return type(v) is int and v > 0


def verify(repo: str, run_id: int, source_sha: str, *,
           api: Callable[[str], Any] = _github_json,
           archive: Callable[[str, int], bytes] = _zip_by_id) -> dict:
    _must(bool(_REPO.fullmatch(repo)), 'unsafe repository')
    _must(_id(run_id) and bool(_SHA40.fullmatch(source_sha)), 'invalid run or source input')
    prefix = f'/repos/{repo}/actions/runs/{run_id}'
    run = api(prefix)
    _must(isinstance(run, dict), 'invalid GitHub run metadata')
    repository = run.get('repository') or {}
    head_repo = run.get('head_repository') or {}
    repo_id = repository.get('id')
    _must(_id(repo_id) and repo == repository.get('full_name') and
          head_repo.get('id') == repo_id and
          head_repo.get('full_name') == repo,
          'cross-repository run identity mismatch')
    _must(run.get('id') == run_id and run.get('head_sha') == source_sha and
          run.get('head_branch') == 'main' and run.get('event') == 'push' and
          run.get('path') == _WORKFLOW and
          run.get('status') == 'completed' and run.get('conclusion') == 'success' and
          _id(run.get('run_attempt')),
          'workflow run source, event, path, attempt or completion invalid')
    attempt = run['run_attempt']
    job_response = api(prefix + '/jobs?per_page=100')
    _must(isinstance(job_response, dict) and _id(job_response.get('total_count')) and
          type(job_response.get('jobs')) is list and
          job_response['total_count'] == len(job_response['jobs']) == 8,
          'GitHub run job listing missing or incomplete')
    jobs = job_response['jobs']
    _must({j.get('name') for j in jobs} == _EXPECTED_JOBS and
          len({j.get('name') for j in jobs}) == 8 and
          len({j.get('id') for j in jobs}) == 8 and
          all(j.get('run_id') == run_id and j.get('status') == 'completed' and
              j.get('conclusion') == 'success' for j in jobs),
          'GitHub native job graph incomplete or failed')
    artifact_response = api(prefix + '/artifacts?per_page=100')
    expected = {f'devtool-dn03_r5-native_r6-multi-node-{i:02}-evidence' for i in range(1,7)}
    expected |= {f'dn03-host-node-{i:02}-{run_id}-{attempt}' for i in range(1,7)}
    expected |= {'devtool-dn03_r5-native_r6-multi-node-03-bytes',
                 'devtool-dn03_r5-native_r6-multi-node-06-bytes',
                 f'dn03-r4-acceptance-{run_id}-{attempt}'}
    _must(isinstance(artifact_response, dict) and
          artifact_response.get('total_count') == 15 and
          type(artifact_response.get('artifacts')) is list and
          len(artifact_response['artifacts']) == 15,
          'GitHub run artifact listing missing or truncated')
    artifacts = artifact_response['artifacts']
    _must({a.get('name') for a in artifacts} == expected and
          len({a.get('id') for a in artifacts}) == 15,
          'GitHub artifact names or identities drifted')
    collected = {}
    for item in artifacts:
        flow = item.get('workflow_run') or {}
        digest = item.get('digest')
        _must(_id(item.get('id')) and not item.get('expired') and
              type(digest) is str and digest.startswith('sha256:') and
              bool(_SHA64.fullmatch(digest[7:])) and
              flow.get('id') == run_id and flow.get('repository_id') == repo_id and
              flow.get('head_repository_id') == repo_id and
              flow.get('head_sha') == source_sha and flow.get('head_branch') == 'main',
              'GitHub artifact identity/digest invalid: ' + str(item.get('name')))
        data = archive(repo, item['id'])
        _must(_sha(data) == digest[7:], 'GitHub ZIP archive digest mismatch: ' + item['name'])
        collected[item['name']] = {'id': item['id'], 'sha256': digest[7:],
                                   'archive': _entries(data)}
    r4 = collected[f'dn03-r4-acceptance-{run_id}-{attempt}']['archive']
    receipt_raw = _one(r4, 'acceptance.json')
    source_run = _one(r4, 'source-run.txt').decode('utf-8')
    sums = _one(r4, 'SHA256SUMS').decode('utf-8')
    _must(source_run.strip() == f'{source_sha}/{run_id}/{attempt}',
          'R4 run identity file does not match authenticated run')
    _must(any(line.strip() == f'{_sha(receipt_raw)}  ./acceptance.json'
              for line in sums.splitlines()), 'R4 receipt SHA256SUMS mismatch')
    try:
        r = json.loads(receipt_raw)
    except (ValueError, UnicodeError) as exc:
        raise R8Refused('R4 acceptance receipt invalid') from exc
    _must(isinstance(r, dict) and r.get('kind') == 'devtool.dn03-r4-cross-node-review' and
          r.get('consumer_repository') == repo and
          r.get('consumer_source_sha') == source_sha and
          r.get('run_id') == run_id and r.get('run_attempt') == attempt and
          bool(_SHA40.fullmatch(str(r.get('consumer_tree', '')))) and
          bool(_SHA40.fullmatch(str(r.get('devtool_sha', '')))) and
          bool(_SHA64.fullmatch(str(r.get('graph_sha256', '')))) and
          r.get('node_count') == 6 and r.get('native_dag_passed') is True and
          r.get('test_reports_independently_verified') is True and
          r.get('all_declared_outputs_verified') is True and
          r.get('verified_test_node_count') == 2 and r.get('verified_test_case_count') == 29 and
          r.get('remote_authenticated') is False and r.get('release_qualified') is False,
          'R4 source, trust or verified test count mismatch')
    nodes = r.get('nodes')
    _must(type(nodes) is list and len(nodes) == 6 and
          {n.get('job_id') for n in nodes} == {f'node-{i:02}' for i in range(1,7)},
          'R4 native node inventory mismatch')
    for n in nodes:
        node = n['job_id']
        evidence = collected[f'devtool-dn03_r5-native_r6-multi-{node}-evidence']['archive']
        host = collected[f'dn03-host-{node}-{run_id}-{attempt}']['archive']
        _must(_sha(_one(evidence, 'node-evidence.json')) == n.get('native_evidence_sha256') and
              _sha(_one(evidence, 'dn03-r3-node-verification.json')) == n.get('r3_verification_sha256') and
              _sha(_one(host, 'host.json')) == n.get('host_sha256'),
              'R4 node receipt bytes mismatch: ' + node)
        trx = n.get('verified_trx')
        if node in ('node-03', 'node-06'):
            path, count = (('dist/pure/results.trx', 21) if node == 'node-03'
                           else ('dist/animal/results.trx', 8))
            _must(type(trx) is dict and trx.get('path') == path and
                  trx.get('passed') == count and
                  type(trx.get('tests')) is dict and
                  trx['tests'].get('total') == count and
                  trx['tests'].get('failed') == 0 and
                  trx['tests'].get('skipped') == 0 and
                  trx['tests'].get('format') == 'trx' and
                  trx['tests'].get('required') is True and
                  trx['tests'].get('report_sha256') == trx.get('sha256') and
                  bool(_SHA64.fullmatch(str(trx.get('sha256','')))) and
                  trx.get('remote_authenticated') is False and
                  trx.get('release_qualified') is False,
                  'R4 TRX identity/count/trust mismatch: ' + node)
        else:
            _must(trx is None, 'non-test node claims TRX evidence')
    _must(r.get('devtool_sha') == '0ebd22c17ac3058704012a1e03ee3265328b0659',
          'R7 Devtool R4 runtime pin drift')
    return {
        'schema_version': 1,
        'kind': 'devtool.dn03-r8-github-api-correlated-provenance',
        'repository': repo, 'repository_id': repo_id,
        'r7_workflow': _WORKFLOW, 'r7_workflow_run_id': run_id,
        'r7_workflow_run_attempt': attempt, 'r7_event': 'push',
        'r7_source_sha': source_sha,
        'r7_source_tree': r['consumer_tree'],
        'r7_devtool_sha': r['devtool_sha'],
        'r7_graph_sha256': r['graph_sha256'],
        'r7_native_jobs_authenticated_via_github_api': 8,
        'r7_artifacts_sha256_verified_via_github_api': len(collected),
        'r7_r4_receipt_sha256': _sha(receipt_raw),
        'r7_r4_independently_reparsed_trx_claim': True,
        'r7_verified_test_cases': 29,
        'github_api_metadata_observed_over_authenticated_transport': True,
        'original_r7_artifacts_individually_sigstore_attested': False,
        'r8_report_signed_in_this_step': False,
        'release_qualified': False,
        'release_signing_verified': False,
        'artifacts': [{'name': k, 'artifact_id': v['id'], 'archive_sha256': v['sha256']}
                      for k,v in sorted(collected.items())],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', required=True)
    p.add_argument('--run-id', required=True, type=int)
    p.add_argument('--source-sha', required=True)
    p.add_argument('--output', required=True)
    args = p.parse_args(argv)
    try:
        out = Path(args.output)
        if out.is_symlink() or out.exists() or not out.parent.is_dir() or out.parent.is_symlink():
            raise R8Refused('R8 output must be a new regular file in existing directory')
        result = verify(args.repo, args.run_id, args.source_sha)
        with out.open('x', encoding='utf-8') as f:
            json.dump(result, f, indent=2, sort_keys=True)
            f.write('\n')
    except (R8Refused, OSError, KeyError, TypeError, ValueError) as exc:
        print('DN03_R8_REFUSED: ' + str(exc), file=sys.stderr)
        return 2
    print('DN03_R8_GITHUB_API_PROVENANCE_PASS run=' + str(args.run_id))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())

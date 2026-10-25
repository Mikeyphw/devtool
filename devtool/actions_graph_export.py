"""RM-06: fail-closed native DAG -> multi-job Actions compilation and byte handoff.

The configured native DAG and the reviewed provider plan remain the authority.
GitHub `needs` is a projection, not a second mutable execution graph.
Downloaded artifact digests establish transfer integrity, not producer identity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
from typing import Any

from .actions_language_providers import provider_plan, ProviderError, SETUP_PYTHON_SHA
from .toolchain_profiles import (select_profiles, validate_versions, cache_policy, ProfileError, MAX_TOTAL_JOBS)
from .actions_adoption import _CHECKOUT_SHA, _SETUP_GO_SHA, _UPLOAD_SHA, _safe_slug, _sha
from .config import load_wrapper_project_config

# Official download-artifact v4 action pin; never float a tag in generated YAML.
_DOWNLOAD_SHA = 'd3f86a106a0bac45b974a628896c90dbdf5c8093'
_SETUP_BUN_SHA = '0c5077e51419868618aeaa5fe8019c62421857d6' # oven-sh/setup-bun v2
_SETUP_NODE_SHA = '49933ea5288caeca8642d1e84afbd3f7d6820020' # actions/setup-node v4.4.0
_SETUP_GRADLE_SHA = 'ed408507eac070d1f99cc633dbcf757c94c7933a'  # gradle/actions/setup-gradle v4
_SETUP_JAVA_SHA = 'cf277c60eb25467037889841efdb72551f06f6c3'  # actions/setup-java v4
_MSVC_ENV_SHA = '0b201ec74fa43914dc39ae48a89fd1d8cb592756' # ilammy/msvc-dev-cmd 1.13.0
_SETUP_DOTNET_SHA = '67a3573c9a986a3f9c594539f4ab511d57bb3ce9'  # actions/setup-dotnet v4.3.1
SCHEMA = 2
MAX_NODES = 24
MAX_TRANSFER_FILES = 64
MAX_TRANSFER_BYTES = 512 * 1024 * 1024


def _hash_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for piece in iter(lambda: f.read(1024*1024),b''):
            h.update(piece)
    return h.hexdigest()


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


def _path(rel: str) -> PurePosixPath:
    if (not isinstance(rel, str) or not rel or len(rel) > 240 or '\\' in rel
            or rel.startswith('/') or rel.endswith('/') or '//' in rel):
        raise ProviderError('RM-06 artifact path must be a bounded relative POSIX file path')
    p = PurePosixPath(rel)
    if any(piece in ('', '.', '..') for piece in rel.split('/')) or any(part.startswith('.') for part in p.parts):
        raise ProviderError('RM-06 artifact paths may not contain dot components')
    if any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-./' for c in rel):
        raise ProviderError('RM-06 artifact path contains unreviewed characters')
    return p


def _regular(root: Path, rel: str, *, must_exist: bool = True) -> Path:
    p = _path(rel)
    here = root
    for part in p.parts:
        here = here / part
        if here.is_symlink():
            raise ProviderError('RM-06 refuses symlink in artifact path')
    if must_exist:
        if not here.is_file() or not stat.S_ISREG(here.stat().st_mode):
            raise ProviderError('RM-06 declared output is missing or not a regular file: ' + rel)
    return here


def compile_graph(root: Path, *, target: str, workflow: str, devtool_sha: str,
                  host_profiles: str = '', cache_locks: bool = False) -> dict[str, Any]:
    """Deterministic compiler; each reviewed host cell gets isolated jobs and handoffs."""
    try:
        profiles=select_profiles(host_profiles)
        plan = provider_plan(root, target=target, workflow=workflow, devtool_sha=devtool_sha,
                             host_os=profiles[0].os, host_arch=profiles[0].arch)
        rows = plan['nodes']
        cpp_only=bool(rows) and all(row['provider']['provider']=='cpp' for row in rows)
        if not cpp_only and len({p.os for p in profiles})>1 and any(p.os=='windows' for p in profiles):
            raise ProfileError('RM-07 mixed Windows/POSIX native plans are not yet portable')
        if not cpp_only:
            validate_versions([row['provider'] for row in rows],profiles)
        else:
            variants=rows[0]['provider']['compiler_matrix']
            if any(row['provider']['compiler_matrix']!=variants for row in rows):
                raise ProfileError('RM-13 all native C/C++ DAG nodes require identical compiler matrices')
            for item in variants:
                compiler,version=item.split(':')
                eligible=[p for p in profiles if (p.os=='windows')==(compiler=='msvc') and
                         (compiler=='msvc' or p.os=='linux') ]
                if len(eligible)!=1:
                    raise ProfileError('RM-13 each requested compiler matrix cell requires exactly one eligible selected host')
                validate_versions([{**rows[0]['provider'],'compiler':compiler,'version':version}],tuple(eligible))
        if any(row['provider'].get('native_manager_version') and
               (row['provider'].get('project') or {}).get('manager') in ('poetry','pdm')
               for row in rows) and any(profile.os!='linux' for profile in profiles):
            raise ProfileError('RM-08 Poetry/PDM native offline execution requires Linux network namespace')
    except ProfileError as exc: raise ProviderError(str(exc)) from None
    versions=(rows[0]['provider']['compiler_matrix'] if all(r['provider']['provider']=='cpp' for r in rows) else rows[0]['provider']['matrix_versions'])
    # Do not infer a shared toolchain from a mixed set of node profiles.
    if not cpp_only and any(row['provider']['matrix_versions']!=versions for row in rows):
        if any(len(row['provider']['matrix_versions'])>1 for row in rows):
            raise ProviderError('RM-08 matrix versions differ across DAG nodes')
        versions=['']  # independent pinned node versions, no fanout
    elif len(versions)==1 and not all(r['provider']['provider']=='cpp' for r in rows):
        versions=['']  # retain original graph/job IDs exactly
    rust_rows=[row for row in rows if row['provider']['provider']=='rust-cargo']
    feature_sets=['none']
    if rust_rows:
        feature_sets=rust_rows[0]['provider']['feature_matrix']
        if any(row['provider']['feature_matrix']!=feature_sets for row in rust_rows):
            raise ProviderError('RM-10 feature matrix must match across reviewed Rust DAG nodes')
        if len(feature_sets)>1 and any(row['provider']['provider']!='rust-cargo' for row in rows):
            raise ProviderError('RM-10 Rust feature fanout requires Rust-only native DAG')
    if len(profiles)*len(rows)*len(versions)*len(feature_sets)>MAX_TOTAL_JOBS:
        raise ProviderError('RM-07 matrix fanout exceeds total job cap')
    if not 1 <= len(rows) <= MAX_NODES:
        raise ProviderError('RM-06 bounded multi-job export accepts 1..24 nodes; use single-job fallback')
    cfg = load_wrapper_project_config(str(root.resolve()), None)
    native_jobs = cfg.targets[plan['target']].jobs
    # RM-07 must re-admit every selected host. The RM-06 provider plan is
    # Ubuntu-based and cannot be used as evidence for the other matrix cells.
    from .workflow_graph import graph_from_workflow
    from .evidence_kernel import graph_host_admission
    graph=graph_from_workflow(plan['target'],cfg.targets[plan['target']].workflows[plan['workflow']])
    for profile in profiles:
        admission=graph_host_admission(cfg,graph,host_os=profile.os,
                                      host_arch=profile.arch,toolchains=('go','python-shell','dotnet','rust-cargo','node','jvm','shell-ci','cpp'))
        if not admission['all_eligible']:
            failures=[item['node']+':'+','.join(item['eligibility']['reasons'])
                      for item in admission['nodes'] if not item['eligibility']['eligible']]
            raise ProviderError('RM-07 host profile '+profile.identifier+' refuses node: '+'; '.join(failures)[:320])
    node_to_id: dict[tuple[str,str],str] = {}
    jobs: list[dict[str, Any]] = []
    for profile in profiles:
      for version in versions:
       for feature_index,feature_set in enumerate(feature_sets):
        if cpp_only and version:
            matrix_compiler,matrix_version=version.split(':')
            if (matrix_compiler=='msvc')!=(profile.os=='windows') or (matrix_compiler!='msvc' and profile.os!='linux'):
                continue
        cell=profile.identifier+(':'+version if version else '')+((':f'+str(feature_index)) if len(feature_sets)>1 else '')
        for index,row in enumerate(rows,1):
            jid=(f'node-{index:02d}' if len(profiles)==1 and profile.identifier=='ubuntu-24.04-x64' else f'node-{index:02d}-{profile.identifier.replace(".","-")}')
            if version:jid+='-v'+version.replace('.','-').replace(':','-')
            if len(feature_sets)>1:jid+='-f'+str(feature_index)
            node_to_id[(row['node'],cell)]=jid
            outs=list(row['outputs'])
            if len(outs)>MAX_TRANSFER_FILES or len(set(outs))!=len(outs):
                raise ProviderError('RM-07 node output count exceeds cap or has duplicates')
            for path in outs:
                _path(path)
                from .actions_language_providers import _tracked
                if _tracked(root,path):
                    raise ProviderError('RM-06 output must not overwrite tracked source: '+path)
            try:
                cache=cache_policy(root,{**row['provider'],'version':matrix_version if cpp_only else (version or row['provider']['version'])},profile,enabled=cache_locks)
            except ProfileError as exc: raise ProviderError(str(exc)) from None
            jobs.append({'id':jid,'node':row['node'],'job':row['job'],
                         'depends_on':list(row['depends_on']), 'profile_id':profile.identifier,
                         'matrix_cell':cell,'matrix_version':version,
                         'runner':profile.hosted_runner,'arch':profile.arch,
                         'timeout_minutes':profile.timeout_minutes, 'cache_policy':cache,
                         'outputs':sorted(outs),'inputs':list(native_jobs[row['job']].inputs),
                         'provider':row['provider']['provider'],
                         'cpp_compiler':matrix_compiler if cpp_only else row['provider'].get('compiler',''),
                         'cpp_version':matrix_version if cpp_only else row['provider'].get('compiler_version',''),
                         'cpp_manager_version':row['provider'].get('manager_version',''),
                         'cpp_sanitizer':row['provider'].get('sanitizer',''),
                         'cpp_manager':row['provider'].get('manager','') if row['provider']['provider']=='cpp' else '',
                         'cpp_project':row['provider'].get('project') if row['provider']['provider']=='cpp' else None,
                         'cpp_operation':row['provider'].get('command','') if row['provider']['provider']=='cpp' else '',
                         'cpp_abi':('pe-x86_64' if matrix_compiler=='msvc' else 'elf-x86_64') if cpp_only else row['provider'].get('abi',''),
                         'cpp_symbols':row['provider'].get('symbols',[]),
                         'cpp_binary_name':row['provider'].get('binary_name',''),
                         'cpp_base_argv':list(row['command']) if row['provider']['provider']=='cpp' else [],
                         'toolchain_version':matrix_version if cpp_only else (version or row['provider']['version']),
                         'go_manifest':row['provider'].get('version_source','go.mod'),
                         'race':('-race' in row['command']),
                         'python_package':row['provider']['provider']=='python-shell' and bool(row['provider'].get('project')),
                         'wheel_smoke':row['wheel_smoke'],'sdist_smoke':row['sdist_smoke'],
                         'reproducible_go':row['provider'].get('reproducible_go',False),
                         'go_command':list(row['command']) if row['provider']['provider']=='go' else [],
                         'go_env':dict(row['env']) if row['provider']['provider']=='go' else {},
                         'python_install_lock':(row['provider'].get('project') or {}).get('install_lock',''),
                         'python_project':row['provider'].get('project'),
                         'python_native_version':row['provider'].get('native_manager_version',''),
                         'auto_coverage':row['auto_coverage'],
                          'rust_command':row['provider'].get('command','') if row['provider']['provider']=='rust-cargo' else '',
                         'rust_target':row['provider'].get('target_triple',''),
                         'rust_workspace':row['provider'].get('workspace') if row['provider']['provider']=='rust-cargo' else None,
                         'rust_package':row['provider'].get('package_name',''),
                         'rust_base_argv':list(row['command']) if row['provider']['provider']=='rust-cargo' else [],
                         'rust_feature_cell':feature_set if row['provider']['provider']=='rust-cargo' else 'none',
                         'rust_json_capture':row['provider'].get('cargo_json_capture',False),
                         'jvm_tool_version':row['provider'].get('tool_version',''),
                         'jvm_manager':row['provider'].get('manager','') if row['provider']['provider']=='jvm' else '',
                         'jvm_project':row['provider'].get('project') if row['provider']['provider']=='jvm' else None,
                         'jvm_selected':row['provider'].get('selected',''),
                         'jvm_operation':row['provider'].get('command',''),
                         'jvm_artifact_name':row['provider'].get('artifact_name',''),
                         'jvm_desktop_bundle':row['provider'].get('desktop_bundle',False),
                         'jvm_base_argv':list(row['command']) if row['provider']['provider']=='jvm' else [],
                         'shell_review':row['provider'] if row['provider']['provider']=='shell-ci' else None,
                         'node_manager':row['provider'].get('manager',''),
                         'node_manager_version':row['provider'].get('manager_version',''),
                         'node_kind':row['provider'].get('kind',''),
                         'node_script':row['provider'].get('script',''),
                         'node_shard':row['provider'].get('shard',''),
                         'node_package_path':row['provider'].get('package_path',''),
                         'node_workspace':row['provider'].get('workspace') if row['provider']['provider']=='node' else None,
                         'node_base_argv':list(row['command']) if row['provider']['provider']=='node' else [],
                         'dotnet_command':row['provider'].get('command',''),
                         'dotnet_selected':row['provider'].get('selected',''),
                         'dotnet_package':row['provider'].get('package'),
                         'dotnet_coverage':row['provider'].get('coverage',False),
                         'dotnet_graph':row['provider'].get('graph'),
                         'dotnet_shared_restore':row['provider'].get('shared_restore'),
                         'coverage_report':row['auto_coverage'] or (row['provider']['provider']=='python-shell' and row['command'][2:]==['coverage','xml','-o','dist/coverage.xml']),
                         'needs':[],'handoffs':[]})
    owners: dict[tuple[str,str],str] = {}
    for job in jobs:
        for output in job['outputs']:
            key=(job['matrix_cell'],output)
            if key in owners:
                raise ProviderError('RM-06 multiple DAG nodes produce one output: '+output)
            owners[key]=job['node']
    for job in jobs:
        profile=job['matrix_cell'];deps=list(job['depends_on'])
        if len(set(deps))!=len(deps) or any((dep,profile) not in node_to_id for dep in deps):
            raise ProviderError('RM-07 invalid native dependency projection')
        job['needs']=[node_to_id[(dep,profile)] for dep in deps]
        by_id={item['id']:item for item in jobs}
        job['handoffs']=[dep for dep in job['needs'] if by_id[dep]['outputs']]
        if len(job['handoffs'])>MAX_NODES:
            raise ProviderError('RM-06 handoff fanin exceeds cap')
        for inp in job['inputs']:
            if not isinstance(inp,str):raise ProviderError('RM-06 nonliteral node input')
            if any(c in inp for c in '*?[]') and owners:
                raise ProviderError('RM-06 cannot prove generated-input ownership through globs')
            key=(profile,inp)
            if key in owners and owners[key]!=job['node'] and owners[key] not in deps:
                raise ProviderError('RM-06 generated input requires direct producer dependency: '+inp)
        if job['race'] and job['runner']!='ubuntu-24.04':
            raise ProviderError('RM-08 Go race profile requires reviewed Linux amd64 runner')
        if job['provider'] not in ('go','python-shell','dotnet','rust-cargo','node','jvm','shell-ci','cpp'):
            raise ProviderError('RM-06 refuses unreviewed hosted provider')
    immutable = {'schema_version': SCHEMA, 'kind': 'devtool.actions.export-graph',
                 'provider_plan_sha256':plan['plan_sha256'], 'target':plan['target'],
                 'workflow':plan['workflow'], 'runtime_sha':plan['runtime_sha'],
                 'runner': profiles[0].hosted_runner if len(profiles)==1 else 'per-cell',
                 'matrix': [{'os':p.hosted_runner,'arch':p.arch} for p in profiles],
                 'toolchain_profiles':[p.identifier for p in profiles],
                 'language_versions':versions,'rust_feature_sets':feature_sets,
                 'cache': 'enabled-reviewed-stores' if cache_locks else 'disabled', 'changed_targets':'full-dag', 'jobs':jobs}
    return {**immutable, 'graph_sha256':_digest(immutable), 'native_source_sha256':plan['plan_sha256'],
            'fallback':'single-job', 'read_only':True, 'authenticated_remote':False,
            'release_qualified':False, 'consumer_commit':plan['consumer_commit'],
            'consumer_tree':plan['consumer_tree'], 'provider_plan':plan}


def compile_affected_graphs(root: Path, *, workflow: str, devtool_sha: str,
                            changed_paths: list[str] | tuple[str, ...] | None = None,
                            host_profiles: str = '', cache_locks: bool = False) -> dict[str, Any]:
    """Fail-conservative source-ownership planning over already configured targets.

    A changed path must have exactly one configured owner for reduced selection.
    Paths with zero/multiple owners or no changes force full candidate builds.
    The existing affected subsystem supplies reverse target dependency closure.
    The native DAG inside every selected target is always preserved unchanged.
    """
    from .affected import analyze_affected_targets, _target_match_reasons
    cfg=load_wrapper_project_config(str(root.resolve()),None)
    candidates=tuple(sorted(name for name,t in cfg.targets.items() if workflow in t.workflows))
    if not candidates:
        raise ProviderError('RM-06 affected plan has no configured workflow candidates')
    if changed_paths is None:
        # An absent explicit Git range is not evidence that nothing changed.
        changed=()
    else:
        if not isinstance(changed_paths,(tuple,list)) or len(changed_paths)>4096:
            raise ProviderError('RM-06 affected path list is unbounded')
        changed=tuple(sorted(set(changed_paths)))
    for path in changed:
        if (not isinstance(path,str) or not path or len(path)>240 or
                path.startswith('/') or '\\' in path or any(x in ('','.','..') for x in path.split('/'))):
            raise ProviderError('RM-06 unsafe changed source path')
    uncertain=[]
    for path in changed:
        owners=[name for name in cfg.targets if _target_match_reasons(cfg,name,path)]
        if len(owners)!=1:
            uncertain.append(path)
    affected=analyze_affected_targets(cfg,changed_paths=changed,candidate_targets=candidates)
    fallback=bool(uncertain) or not changed or not affected.targets
    targets=candidates if fallback else tuple(sorted(affected.targets))
    if not targets: raise ProviderError('RM-06 refuses optimistic empty hosted selection')
    graphs={name:compile_graph(root,target=name,workflow=workflow,devtool_sha=devtool_sha,
             host_profiles=host_profiles,cache_locks=cache_locks) for name in targets}
    record={'schema_version':1,'kind':'devtool.actions.affected-multi-job',
            'workflow':workflow,'selected_targets':list(targets),'changed_paths':list(changed),
            'ambiguous_paths':uncertain,'fallback_full':fallback,
            'reason':'unowned/shared/empty changes' if fallback else 'exclusive ownership + dependent closure',
            'graphs':{key:graphs[key]['graph_sha256'] for key in sorted(graphs)}}
    return {**record,'selection_sha256':_digest(record),'compiled_graphs':graphs}


def _artifact_name(namespace: str, jid: str) -> str:
    return f'{namespace}-{jid}-bytes'


def workflow_yaml(graph: dict[str, Any]) -> str:
    namespace=f"devtool-{graph['target']}-{graph['workflow']}-multi"
    target, workflow, pin, digest=graph['target'],graph['workflow'],graph['runtime_sha'],graph['graph_sha256']
    preamble = (f'# Generated by Devtool RM-06: multi-job projection of the native DAG.\n'
                f'# ExportGraphV2 SHA256: {digest}\n'
                f'name: {namespace}\n'
                'on:\n  workflow_dispatch:\n'
                'permissions:\n  contents: read\n'
                'concurrency:\n'
                f'  group: {namespace}-${{{{ github.ref }}}}\n'
                '  cancel-in-progress: false\n'
                'jobs:\n')
    entries=[]
    for job in graph['jobs']:
        jid=job['id']
        dep_str=('    needs: ['+', '.join(job['needs'])+']\n') if job['needs'] else ''
        go_selector=("go-version: '"+job["toolchain_version"]+"'" if job["matrix_version"] else f'go-version-file: {job["go_manifest"]}')
        setup_go=(f'      - name: Pinned Go toolchain\n'
                  f'        uses: actions/setup-go@{_SETUP_GO_SHA}\n'
                  f'        with:\n          {go_selector}\n          cache: false\n') if job['provider']=='go' else ''
        dotnet_setup=(f'      - name: Pinned .NET SDK\n'
                      f'        uses: actions/setup-dotnet@{_SETUP_DOTNET_SHA}\n'
                      '        with:\n'
                      f"          dotnet-version: '{job['toolchain_version']}'\n"
                      '          cache: false\n') if job['provider']=='dotnet' else ''
        node_setup=(f'      - name: Pinned Node runtime\n'
                    f'        uses: actions/setup-node@{_SETUP_NODE_SHA}\n'
                    '        with:\n'
                    f"          node-version: '{job['toolchain_version']}'\n"
                    '          check-latest: false\n') if job['provider']=='node' else ''
        bun_setup=(f'      - name: Pinned Bun runtime\n'
                   f'        uses: oven-sh/setup-bun@{_SETUP_BUN_SHA}\n'
                   '        with:\n'
                   f"          bun-version: '{job['node_manager_version']}'\n") if job['provider']=='node' and job['node_manager']=='bun' else ''
        node_manager_setup=('      - name: Verify or activate reviewed package manager\n'
                            '        shell: bash\n        run: |\n'
                            '          set -euo pipefail\n'
                            + (f'          npm install --global npm@{job["node_manager_version"]} --ignore-scripts --no-audit --no-fund\n'
                               if job['node_manager']=='npm' else
                               '          npm install --global corepack@0.34.7 --ignore-scripts --no-audit --no-fund\n'
                               + f'          corepack enable\n          corepack prepare {job["node_manager"]}@{job["node_manager_version"]} --activate\n'
                               if job['node_manager'] in ('pnpm','yarn') else '')
                            + f'          test "$( {job["node_manager"]} --version )" = "{job["node_manager_version"]}"\n') if job['provider']=='node' else ''
        jvm_setup=(
            '      - name: Pinned Java SDK\n'
            f'        uses: actions/setup-java@{_SETUP_JAVA_SHA}\n'
            '        with:\n          distribution: temurin\n'
            f'          java-version: "{job["toolchain_version"]}"\n'
            '          check-latest: false\n'
            '      - name: Check Maven or Gradle installation\n'
            '        shell: bash\n        run: |\n'
            '          set -euo pipefail\n'
            '          java -version\n'
            f'          {"mvn" if job["jvm_manager"]=="maven" else "gradle"} {"-version" if job["jvm_manager"]=="maven" else "--version"}\n'
        ) if job['provider']=='jvm' else ''
        if job['provider']=='jvm' and job['jvm_manager']=='gradle':
            gradle_step=(f'      - name: Pinned Gradle distribution\n'
                         f'        uses: gradle/actions/setup-gradle@{_SETUP_GRADLE_SHA}\n'
                         f'        with:\n          gradle-version: "{job["jvm_tool_version"]}"\n'
                         '          cache-disabled: true\n')
            jvm_setup=gradle_step+jvm_setup
        shell_setup=''
        if job['provider']=='shell-ci':
            operation=job['shell_review']['operation']
            tool={'syntax':{'bash':'bash','posix':'sh','zsh':'zsh'}[job['shell_review']['dialect']],
                  'lint':'shellcheck','format':'shfmt','test':'bats'}[operation]
            shell_setup=('      - name: Verify preinstalled reviewed shell toolchain\n'
                         '        shell: bash\n        run: |\n'
                         '          set -euo pipefail\n'
                         f'          command -v {tool} >/dev/null\n')
            # Ubuntu 24.04 has Bash and ShellCheck, but not guaranteed Bats,
            # shfmt or Zsh. Provision only reviewed Ubuntu noble .deb pins:
            # never execute downloaded scripts, npm install or floating tags.
            deb={'bats':('bats','1.10.0-1'),
                 'shfmt':('shfmt','3.8.0-1'),
                 'zsh':('zsh','5.9-6ubuntu2')}
            if tool in deb:
                name,pin=deb[tool]
                shell_setup += ('          sudo apt-get update -qq\n'
                                '          sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends '
                                f'{name}={pin}\n'
                                f'          test "$(dpkg-query -W -f=\'${{Version}}\' {name})" = "{pin}"\n')
            if job['shell_review']['dialect']=='bash':
                shell_setup += '          bash --version | head -1 | grep -E "^GNU bash, version 5\\."\n'
            elif job['shell_review']['dialect']=='zsh':
                shell_setup += '          zsh --version | grep -E "^zsh 5\\."\n'
            else:
                shell_setup += '          sh -n /dev/null\n'
            if tool=='shellcheck':
                shell_setup += '          shellcheck --version | grep -E "version: 0\\.9\\.0"\n'
            if tool=='shfmt':
                shell_setup += '          shfmt --version | grep -F "v3.8.0"\n'
            if tool=='bats':
                shell_setup += '          bats --version | grep -F "Bats 1.10.0"\n'
        cpp_setup=''
        if job['provider']=='cpp':
            if job['cpp_compiler']=='msvc':
                cpp_setup=(f'      - name: Load pinned MSVC developer environment\n'
                           f'        uses: ilammy/msvc-dev-cmd@{_MSVC_ENV_SHA}\n'
                           '        with:\n          arch: amd64\n')
            else:
                cc='gcc' if job['cpp_compiler']=='gcc' else 'clang'
                build_manager=job['cpp_manager']
                version_probe=('cmake --version' if build_manager=='cmake' else 'meson --version')
                cpp_setup=('      - name: Verify native compiler tools\n'
                           '        shell: bash\n        run: |\n'
                           '          set -euo pipefail\n'
                           f'          command -v {cc}-{job["cpp_version"]}\n'
                           f'          {version_probe} | grep -F {job["cpp_manager_version"]}\n'
                           '          ninja --version\n')
        rust_setup=('      - name: Verify preinstalled pinned Rust toolchain (no network installation)\n'
                    '        shell: bash\n        run: |\n'
                    '          set -euo pipefail\n'
                    '          export RUSTUP_AUTO_INSTALL=0\n'
                    '          export CARGO_NET_OFFLINE=true\n'
                    f'          rustup run {job["toolchain_version"]} rustc --version | grep -F "rustc {job["toolchain_version"]} "\n'
                    f'          rustup run {job["toolchain_version"]} cargo --version\n'
                    f'          rustup target list --installed --toolchain {job["toolchain_version"]} | grep -Fx {job["rust_target"]}\n'
                    + ('          command -v aarch64-linux-gnu-gcc >/dev/null\n' if job['rust_target']=='aarch64-unknown-linux-gnu' else '')) if job['provider']=='rust-cargo' else ''
        downloads=""
        for dep in job['handoffs']:
            downloads += (f'      - name: Download {dep} byte handoff\n'
                          f'        uses: actions/download-artifact@{_DOWNLOAD_SHA}\n'
                          '        with:\n'
                          f'          name: {_artifact_name(namespace,dep)}\n'
                          f'          path: ${{{{ runner.temp }}}}/rm06-inputs/{dep}\n')
        cache=job['cache_policy']
        cache_step=(f'      - name: Opt-in pinned provider content cache\n'
                    f'        uses: actions/cache@{cache["action_sha"]}\n'
                    '        with:\n'
                    f'          path: {cache["path"]}\n'
                    f'          key: {cache["key"]}\n') if cache['enabled'] else ''
        py_native=bool(job['python_native_version'])
        py_command={'uv':'uv','poetry':'poetry','pdm':'pdm'}.get((job['python_project'] or {}).get('manager',''))
        py_verify=(('      - name: Verify preinstalled pinned Python manager (no automatic bootstrap)\n'
                   '        shell: bash\n        run: |\n'
                   '          set -euo pipefail\n'
                   f'          test "$({py_command} --version)" = "{("uv " if py_command=="uv" else "Poetry (version " if py_command=="poetry" else "PDM, version ")}{job["python_native_version"]}{")" if py_command=="poetry" else ""}"\n')
                   if py_native else '')
        py_requirements=(('      - name: Install tracked hash-locked Python build requirements\n'
                          '        shell: bash\n        run: |\n'
                          '          set -euo pipefail\n'
                          f'          python3 -m pip install --disable-pip-version-check --require-hashes -r {job["python_install_lock"] or "requirements.txt"}\n')
                         if job['python_package'] and not py_native else '')
        upload=''
        if job['outputs']:
            upload=(f'      - name: Upload {jid} declared bytes (not authenticated)\n'
                    '        if: success()\n'
                    f'        uses: actions/upload-artifact@{_UPLOAD_SHA}\n'
                    '        with:\n'
                    f'          name: {_artifact_name(namespace,jid)}\n'
                    f'          path: ${{{{ runner.temp }}}}/rm06-output/{jid}/\n'
                    '          include-hidden-files: false\n'
                    '          if-no-files-found: error\n'
                    '          retention-days: 7\n')
        is_windows=job['profile_id']=='windows-2022-x64'
        python_bootstrap='python' if is_windows else 'python3'
        venv_python='"$RUNNER_TEMP/devtool-venv/Scripts/python.exe"' if is_windows else '"$RUNNER_TEMP/devtool-venv/bin/python"'
        # Runtime downloads pinned commit before any handoff touches the workspace.
        py_exec=('Scripts/python.exe' if job['profile_id']=='windows-2022-x64' else 'bin/python')
        entry=(f'  {jid}:\n{dep_str}'
               f'    runs-on: {job["runner"]}\n    timeout-minutes: {job["timeout_minutes"]}\n'
               '    env:\n'
               f'      DEVTOOL_SOURCE_SHA: {pin}\n'
               '      PYTHONDONTWRITEBYTECODE: "1"\n'
               '      GOTOOLCHAIN: local\n'
               '      RUSTUP_AUTO_INSTALL: "0"\n'
               '      NUGET_PACKAGES: ${{ runner.temp }}/devtool-nuget-store\n'
               '      PIP_CACHE_DIR: ${{ runner.temp }}/devtool-python-wheel-store\n'
               '      CARGO_NET_OFFLINE: "true"\n'
               '    steps:\n'
               '      - name: Checkout consumer commit\n'
               f'        uses: actions/checkout@{_CHECKOUT_SHA}\n'
               '        with:\n          persist-credentials: false\n'
               '      - name: Pinned Python interpreter\n'
               f'        uses: actions/setup-python@{SETUP_PYTHON_SHA}\n'
               f"        with:\n          python-version: '{job["toolchain_version"] if job["provider"]=="python-shell" else "3.12"}'\n"
               f'{setup_go}{dotnet_setup}{node_setup}{bun_setup}{node_manager_setup}{rust_setup}{jvm_setup}{shell_setup}{cpp_setup}{cache_step}{py_verify}'
               '      - name: Acquire immutable Devtool runtime\n'
               '        shell: bash\n        run: |\n'
               '          set -euo pipefail\n'
               '          test "$(git rev-parse HEAD)" = "$GITHUB_SHA"\n'
               '          git clone --no-checkout https://github.com/Mikeyphw/devtool.git "$RUNNER_TEMP/devtool-source"\n'
               '          git -C "$RUNNER_TEMP/devtool-source" checkout --detach "$DEVTOOL_SOURCE_SHA"\n'
               '          test "$(git -C "$RUNNER_TEMP/devtool-source" rev-parse HEAD)" = "$DEVTOOL_SOURCE_SHA"\n'
               f'          {python_bootstrap} -m venv "$RUNNER_TEMP/devtool-venv"\n'
               f'          {venv_python} -m pip install --disable-pip-version-check "$RUNNER_TEMP/devtool-source"\n'
               f'{downloads}{py_requirements}'
               '      - name: Verify dependencies and run native target-local node\n'
               '        shell: bash\n        run: |\n'
               '          set -euo pipefail\n'
               f'          {venv_python} -m devtool.actions_graph_export run-node '
               f'--root "$GITHUB_WORKSPACE" --target {target} --workflow {workflow} '
               f'--devtool-sha {pin} --graph-sha {digest} --job-id {jid} '
               f' --host-profiles {",".join(graph.get("toolchain_profiles",[]))}'
               + (' --cache-locks' if graph['cache']!='disabled' else '')
               + ' --inputs "$RUNNER_TEMP/rm06-inputs" --outputs "$RUNNER_TEMP/rm06-output"\n'
               f'{upload}'
               '      - name: Record local run attempt (not authentication)\n'
               '        if: always()\n        shell: bash\n        run: |\n'
               '          set -euo pipefail\n'
               f'          mkdir -p "$RUNNER_TEMP/rm06-evidence/{jid}"\n'
               f'          printf "%s\\n" "$GITHUB_SHA/$GITHUB_RUN_ID/$GITHUB_RUN_ATTEMPT/{jid}" > "$RUNNER_TEMP/rm06-evidence/{jid}/source-run.txt"\n'
               '      - name: Upload review evidence\n'
               '        if: always()\n'
               f'        uses: actions/upload-artifact@{_UPLOAD_SHA}\n'
               '        with:\n'
               f'          name: {namespace}-{jid}-evidence\n'
               f'          path: ${{{{ runner.temp }}}}/rm06-evidence/{jid}/\n'
               '          if-no-files-found: error\n          retention-days: 7\n')
        entries.append(entry)
    return preamble+''.join(entries)


def _gha_context(root: Path) -> tuple[str, str, int]:
    from .actions_adoption import _git
    commit=_git(root, 'rev-parse', 'HEAD')
    requested=os.environ.get('GITHUB_SHA','')
    run=os.environ.get('GITHUB_RUN_ID','')
    attempt=os.environ.get('GITHUB_RUN_ATTEMPT','')
    if requested!=commit or not run.isdecimal() or not attempt.isdecimal() or int(run)<1 or int(attempt)<1:
        raise ProviderError('RM-06 GitHub source/run/attempt identity unavailable or wrong')
    return requested,run,int(attempt)


def _load_manifest(directory: Path, *, graph: dict[str, Any], parent: dict[str, Any], source: str, run: str, attempt: int) -> list[tuple[str,Path]]:
    if directory.is_symlink() or (directory/'payload').is_symlink():
        raise ProviderError('RM-06 symlink artifact handoff directory')
    manifest_path=directory/'receipt.json'
    if manifest_path.is_symlink() or not manifest_path.is_file() or manifest_path.stat().st_size>32768:
        raise ProviderError('RM-06 missing/unsafe handoff receipt')
    try: receipt=json.loads(manifest_path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError) as exc: raise ProviderError('RM-06 malformed receipt') from exc
    exact={'schema_version':SCHEMA, 'kind':'devtool.actions.node-handoff',
           'graph_sha256':graph['graph_sha256'], 'workflow_sha256':hashlib.sha256(workflow_yaml(graph).encode()).hexdigest(), 'source_commit':source,
           'run_id':run, 'attempt':attempt, 'producer_node':parent['node'],
           'producer_job':parent['id']}
    if not isinstance(receipt,dict) or any(receipt.get(k)!=v for k,v in exact.items()):
        raise ProviderError('RM-06 artifact source/run/attempt/producer mismatch')
    files=receipt.get('files')
    if not isinstance(files,list) or len(files)!=len(parent['outputs']):
        raise ProviderError('RM-06 handoff files do not match declared outputs')
    checked=[]
    if [r.get('path') for r in files if isinstance(r,dict)]!=parent['outputs']:
        raise ProviderError('RM-06 handoff output list differs from frozen graph')
    for row in files:
        if not isinstance(row,dict) or set(row)!={'path','sha256','size'}:
            raise ProviderError('RM-06 malformed handoff file entry')
        path=row['path'];file=_regular(directory/'payload',path)
        if type(row['size']) is not int or row['size']<0 or row['size']>MAX_TRANSFER_BYTES or file.stat().st_size!=row['size']:
            raise ProviderError('RM-06 handoff file size mismatch')
        if not isinstance(row['sha256'],str) or len(row['sha256'])!=64 or _hash_file(file)!=row['sha256']:
            raise ProviderError('RM-06 handoff digest mismatch')
        checked.append((path,file))
    return checked


def consume_handoffs(root: Path, input_dir: Path, graph: dict[str, Any], job: dict[str, Any], source: str, run: str, attempt: int) -> None:
    by_id={item['id']:item for item in graph['jobs']}
    if input_dir.is_symlink():
        raise ProviderError('RM-06 symlink input handoff root')
    checked=[]
    for dep in job['handoffs']:
        checked.extend(_load_manifest(input_dir/dep,graph=graph,parent=by_id[dep],source=source,run=run,attempt=attempt))
    # Verify ALL bytes and destinations before writing ANY file.
    for rel,_ in checked:
        dest=_regular(root,rel,must_exist=False)
        if dest.exists() or dest.is_symlink():
            raise ProviderError('RM-06 handoff would overwrite a source/previous output: '+rel)
    copied=[]
    try:
        for rel,src in checked:
            dst=_regular(root,rel,must_exist=False)
            dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(src,dst)
            copied.append(dst)
    except BaseException:
        for item in copied:
            item.unlink(missing_ok=True)
        raise


def stage_outputs(root: Path, outdir: Path, graph: dict[str, Any], job: dict[str, Any], source: str, run: str, attempt: int) -> None:
    if not job['outputs']:
        return
    staging=outdir/job['id']
    if staging.exists() or staging.is_symlink():
        raise ProviderError('RM-06 refuses to reuse an output staging directory')
    checked=[];total=0
    for rel in job['outputs']:
        path=_regular(root,rel)
        size=path.stat().st_size;total+=size
        if size>MAX_TRANSFER_BYTES or total>MAX_TRANSFER_BYTES:
            raise ProviderError('RM-06 output handoff exceeds byte budget')
        checked.append((rel,path,size,_hash_file(path)))
    staging.mkdir(parents=True)
    for rel,src,size,digest in checked:
        dst=staging/'payload'/rel
        dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(src,dst)
        if _hash_file(dst)!=digest:
            raise ProviderError('RM-06 changed output during staging')
    receipt={'schema_version':SCHEMA,'kind':'devtool.actions.node-handoff','graph_sha256':graph['graph_sha256'],
             'source_commit':source,'workflow_sha256':hashlib.sha256(workflow_yaml(graph).encode()).hexdigest(),'run_id':run,'attempt':attempt,'producer_node':job['node'],
             'producer_job':job['id'], 'files':[{'path':rel,'size':size,'sha256':sha} for rel,_,size,sha in checked],
             'authenticated':False, 'release_qualified':False}
    (staging/'receipt.json').write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')


def run_node(root: Path, *, target: str, workflow: str, devtool_sha: str, graph_sha: str,
             job_id: str, inputs: Path, outputs: Path, host_profiles: str = '', cache_locks: bool = False) -> dict[str, Any]:
    root=root.resolve()
    graph=compile_graph(root,target=target,workflow=workflow,devtool_sha=devtool_sha,
                        host_profiles=host_profiles,cache_locks=cache_locks)
    if graph_sha!=graph['graph_sha256']:
        raise ProviderError('RM-06 frozen graph digest changed before native node execution')
    from .actions_adoption import _git
    source,run,attempt=_gha_context(root)
    selected=next((item for item in graph['jobs'] if item['id']==job_id),None)
    if selected is None:
        raise ProviderError('RM-06 unknown compiled GitHub node')
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        expected={'ubuntu-24.04-x64':'Linux','macos-15-intel':'macOS','windows-2022-x64':'Windows'}[selected['profile_id']]
        if os.environ.get('RUNNER_OS')!=expected or os.environ.get('RUNNER_ARCH')!='X64':
            raise ProviderError('RM-07 selected hosted profile does not match runner OS/architecture')
    consume_handoffs(root,inputs,graph,selected,source,run,attempt)
    if selected['dotnet_coverage']:
        dist=root/'dist'
        if dist.is_symlink() or (dist.exists() and any(dist.glob('*/coverage.cobertura.xml'))):
            raise ProviderError('RM-09 existing collector coverage may not be reused as native evidence')
    for path in selected['outputs']:
        out=_regular(root,path,must_exist=False)
        if out.exists() or out.is_symlink():
            raise ProviderError('RM-06 node output already exists before execution: '+path)
    cfg=load_wrapper_project_config(str(root),None)
    from .workflow_runtime import build_direct_job_specs
    from .runners import run_specs
    specs=build_direct_job_specs(cfg, f'{target}:job:{selected["job"]}',target_name=target)
    if not specs:
        raise ProviderError('RM-06 native job resolver returned no executable specs')
    node_receipts=[]
    if selected['provider']=='shell-ci':
        import subprocess as _subprocess
        dialect=selected['shell_review']['dialect']
        if dialect!='posix':
            try:
                check=_subprocess.run([dialect,'--version'],capture_output=True,text=True,timeout=8)
            except (OSError,_subprocess.TimeoutExpired):
                raise ProviderError('RM-14 reviewed shell executable unavailable') from None
            import re as _re
            if check.returncode or not _re.search((r'GNU bash, version 5\.' if dialect=='bash' else r'zsh 5\.'),check.stdout+' '+check.stderr):
                raise ProviderError('RM-14 actual shell major version differs from reviewed pin')
        from .actions_rm14_shell import inspect_shell_job, ShellError
        if len(specs)!=1 or specs[0].argv!=selected['shell_review']['argv']:
            raise ProviderError('RM-14 native shell job diverged from reviewed argv')
        try:again=inspect_shell_job(root,specs[0].argv,
              cfg.targets[target].jobs[selected['job']].metadata or {})
        except ShellError as exc:raise ProviderError(str(exc)) from None
        if again['source_digest']!=selected['shell_review']['source_digest']:
            raise ProviderError('RM-14 shell source bytes changed after review')
        if selected['shell_review']['operation']=='test':
            folder=_regular(root,'dist/report.xml',must_exist=False).parent
            folder.mkdir(parents=True,exist_ok=True)
    if selected['provider']=='cpp':
        from .actions_rm13_cpp import cpp_project, CppError
        from dataclasses import replace as _replace
        from .runners import run_specs as _run
        import re as _re
        if len(specs)!=1 or specs[0].argv!=selected['cpp_base_argv']:
            raise ProviderError('RM-13 native C/C++ direct-job command diverges from reviewed plan')
        try:reviewed=cpp_project(root,manager=selected['cpp_manager'],preset=selected['cpp_project']['preset'],
                                 build_type=selected['cpp_project']['build_type'])
        except CppError as exc:raise ProviderError(str(exc)) from None
        if reviewed['source_digest']!=selected['cpp_project']['source_digest']:
            raise ProviderError('RM-13 reviewed C/C++ source changed before native execution')
        cc=selected['cpp_compiler'];version=selected['cpp_version']
        if cc=='msvc':
            probe=['cl']
        else:
            probe=[cc+'-'+version,'--version']
        import subprocess as _sp
        try:p=_sp.run(probe,capture_output=True,text=True,timeout=12)
        except (OSError,_sp.TimeoutExpired):raise ProviderError('RM-13 preinstalled compiler unavailable') from None
        if cc=='msvc':
            ok=('Version 19.4' in (p.stdout+p.stderr))
        else:
            ok=p.returncode==0 and bool(_re.search(r'\b'+_re.escape(version)+r'\.\d+\b',p.stdout+p.stderr))
        if not ok:raise ProviderError('RM-13 compiler pin differs from installed compiler')
        buildtool=selected['cpp_manager'];toolcmd=['cmake','--version'] if buildtool=='cmake' else ['meson','--version']
        try:tool_result=_sp.run(toolcmd,capture_output=True,text=True,timeout=12)
        except (OSError,_sp.TimeoutExpired):raise ProviderError('RM-13 missing declared native build system') from None
        tool_version=selected['cpp_manager_version']
        found=tool_result.stdout+tool_result.stderr
        if tool_result.returncode or not _re.search(r'\b'+_re.escape(tool_version)+r'(?:\.|\s|$)',found):
            raise ProviderError('RM-13 installed build system differs from reviewed version')
        env=dict(specs[0].env)
        if cc!='msvc':
            env.update({'CC':cc+'-'+version,'CXX':('g++-' if cc=='gcc' else 'clang++-')+version})
        env['CMAKE_BUILD_PARALLEL_LEVEL']='2'
        env['MESON_WRAP_MODE']='nodownload'
        env['CCACHE_DISABLE']='1'
        if selected['cpp_sanitizer']!='none':
            if cc=='msvc':env['CL']='/fsanitize=address'
            else:
                flags='-fsanitize='+selected['cpp_sanitizer']+' -fno-omit-frame-pointer'
                env['CFLAGS']=env['CXXFLAGS']=env['LDFLAGS']=flags
        spec=_replace(specs[0],env=env)
        setup=(['cmake','--preset',reviewed['preset']] if selected['cpp_manager']=='cmake' else
               ['meson','setup',reviewed['profile']['build_dir'],'--native-file','ci/meson-'+reviewed['preset']+'.ini',
                '--buildtype',{'Debug':'debug','Release':'release','RelWithDebInfo':'debugoptimized'}[reviewed['build_type']]])
        if selected['cpp_operation']!='configure':
            cfg_spec=_replace(spec,name=spec.name+' [reviewed native configure]',argv=setup)
            boot,_=_run(cfg,[cfg_spec],verbose=True,dry_run=False,continue_on_error=False)
            if not boot or any(x.returncode!=0 or x.skipped for x in boot):
                raise ProviderError('RM-13 native configure step failed; no artifact accepted')
            if selected['cpp_operation']=='test':
                cmd=(['cmake','--build',reviewed['profile']['build_dir']] if selected['cpp_manager']=='cmake' else
                     ['meson','compile','-C',reviewed['profile']['build_dir']])
                built,_=_run(cfg,[_replace(spec,name=spec.name+' [reviewed native build]',argv=cmd)],verbose=True,dry_run=False,continue_on_error=False)
                if not built or any(x.returncode!=0 or x.skipped for x in built):
                    raise ProviderError('RM-13 native build step failed before test/package')
        if selected['cpp_operation']=='test' and selected['cpp_manager']=='cmake':
            report=root/reviewed['profile']['build_dir']/'dist/cpp-tests.xml'
            if report.exists() or report.is_symlink():raise ProviderError('RM-13 CTest report exists before test execution')
            report.parent.mkdir(parents=True,exist_ok=True)
        specs=[spec]
    if selected['provider']=='node':
        # Repo scripts and tool configuration are executable code. Enforce the
        # checkout's exact Git source before any dependency installation.
        import subprocess as _subprocess
        if _subprocess.run(['git','-C',str(root),'diff','--quiet','HEAD','--'],check=False).returncode != 0:
            raise ProviderError('RM-11 tracked source changed after reviewed commit')
        from dataclasses import replace
        from .actions_rm11_node import node_workspace, NodeError
        import shutil as _shutil
        if len(specs)!=1 or specs[0].argv!=selected['node_base_argv']:
            raise ProviderError('RM-11 native manager script resolver diverged from source review')
        try:ws=node_workspace(root,manager_selection=selected['node_manager'])
        except NodeError as exc:raise ProviderError(str(exc)) from None
        if ws['source_digest']!=selected['node_workspace']['source_digest']:
            raise ProviderError('RM-11 manager source drift before installation')
        env=dict(specs[0].env)
        manager=selected['node_manager']
        import subprocess as _subprocess
        try:
            version_probe=_subprocess.run([manager,'--version'],cwd=root,capture_output=True,
                                         text=True,timeout=12,check=False)
        except (OSError,_subprocess.TimeoutExpired):
            raise ProviderError('RM-11 reviewed package manager is not installed') from None
        if version_probe.returncode or version_probe.stdout.strip()!=selected['node_manager_version']:
            raise ProviderError('RM-11 installed manager version differs from reviewed pin')
        store=str((Path(os.environ.get('RUNNER_TEMP',str(root)))/'devtool-node-store'/manager).resolve())
        # Persistent host caches are never part of consumer source/artifact handoffs.
        env.update({'COREPACK_ENABLE_AUTO_PIN':'0','COREPACK_ENABLE_DOWNLOAD_PROMPT':'0',
                    'npm_config_ignore_scripts':'true','npm_config_audit':'false',
                    'npm_config_cache':store,'BUN_INSTALL_CACHE_DIR':store,'YARN_CACHE_FOLDER':store,
                    'YARN_ENABLE_SCRIPTS':'false','YARN_ENABLE_IMMUTABLE_INSTALLS':'true'})
        bootstrap={'npm':['npm','ci','--ignore-scripts','--no-audit','--no-fund'],
                   'pnpm':['pnpm','install','--frozen-lockfile','--ignore-scripts','--store-dir',store],
                   'yarn':['yarn','install','--immutable','--mode=skip-builds'],
                   'bun':['bun','install','--frozen-lockfile','--ignore-scripts']}[manager]
        installer=replace(specs[0],name=specs[0].name+' [frozen install]',argv=bootstrap,env=env)
        installed,_=run_specs(cfg,[installer],verbose=True,dry_run=False,continue_on_error=False)
        if not installed or any(r.returncode!=0 or r.skipped for r in installed):
            raise ProviderError('RM-11 frozen dependency install failed')
        specs=[replace(specs[0],env=env)]
    if selected['python_native_version']:
        from .actions_rm08_native import provision_python_manager
        specs=provision_python_manager(root,selected,specs)
    if selected['provider']=='jvm':
        from .actions_rm12_jvm import jvm_project, JvmError
        import subprocess as _subprocess
        if len(specs)!=1 or specs[0].argv!=selected['jvm_base_argv']:
            raise ProviderError('RM-12 JVM native direct-job argv diverged from review')
        if _subprocess.run(['git','-C',str(root),'diff','--quiet','HEAD','--'],check=False).returncode:
            raise ProviderError('RM-12 tracked source modified after review')
        try: actual=jvm_project(root,manager=selected['jvm_manager'])
        except JvmError as exc:raise ProviderError(str(exc)) from None
        if actual['source_digest']!=selected['jvm_project']['source_digest']:
            raise ProviderError('RM-12 JVM project source drift before native execution')
        try:
            jdk=_subprocess.run(['javac','-version'],capture_output=True,text=True,timeout=8)
            version=(jdk.stdout+' '+jdk.stderr).strip()
        except (OSError,_subprocess.TimeoutExpired):
            raise ProviderError('RM-12 missing pinned javac toolchain') from None
        if jdk.returncode or not __import__('re').search(r'javac '+__import__('re').escape(selected['toolchain_version'])+r'(?:\.|\s|$)',version):
            raise ProviderError('RM-12 installed Java SDK differs from reviewed pin')
        # The exact build tool pin is verified even in local native execution.
        tool=['mvn','-version'] if selected['jvm_manager']=='maven' else ['gradle','--version']
        try:
            probe=_subprocess.run(tool,capture_output=True,text=True,timeout=15)
        except (OSError,_subprocess.TimeoutExpired):
            raise ProviderError('RM-12 reviewed JVM build tool is not installed') from None
        version_output=probe.stdout+' '+probe.stderr
        import re as _re
        expected=selected['jvm_tool_version']
        regex=(r'Apache Maven '+_re.escape(expected)+r'(?:\s|$)' if selected['jvm_manager']=='maven'
               else r'Gradle '+_re.escape(expected)+r'(?:\s|$)')
        if probe.returncode or not _re.search(regex,version_output):
            raise ProviderError('RM-12 installed Maven/Gradle version differs from reviewed pin')
        from dataclasses import replace as _replace
        controlled=dict(specs[0].env)
        controlled.update({'ORG_GRADLE_JAVA_INSTALLATIONS_AUTO_DOWNLOAD':'false',
                           'GRADLE_OPTS':'-Dorg.gradle.java.installations.auto-download=false',
                           'MAVEN_OPTS':'-Djava.awt.headless=true'})
        specs=[_replace(specs[0],env=controlled)]
    dotnet_shared_store=None
    if selected['provider']=='dotnet' and selected['dotnet_command']!='restore':
        # FC-06: each selected solution gets a real locked restore in ONE native
        # job, with the SAME newly allocated package store. No GitHub `needs`
        # edge is mistaken for shared filesystem state.
        from dataclasses import replace
        if len(specs)!=1 or specs[0].argv[:3]!=['dotnet',selected['dotnet_command'],selected['dotnet_selected']]:
            raise ProviderError('RM-09 native .NET job resolver changed after review')
        reviewed=selected.get('dotnet_shared_restore')
        if reviewed:
            import tempfile as _tempfile
            import subprocess as _subprocess
            from .actions_rm09_dotnet import (solution_graph as _solution_graph,
                review_shared_restore as _review_shared_restore,DotNetError)
            try:
                candidate=_solution_graph(root,selected=reviewed['selected'])
                again=_review_shared_restore(root,candidate,reviewed['selected'],reviewed['solutions'],
                    framework=reviewed['framework'],rid=reviewed['rid'],host_os=reviewed['host_os'])
            except DotNetError as exc:raise ProviderError(str(exc)) from None
            if again['review_sha256']!=reviewed['review_sha256']:
                raise ProviderError('FC-06 shared restore review changed before native execution')
            try:
                sdk=_subprocess.run(['dotnet','--version'],capture_output=True,text=True,timeout=12)
            except (OSError,_subprocess.TimeoutExpired):
                raise ProviderError('FC-06 pinned .NET SDK unavailable for shared restore') from None
            if sdk.returncode or sdk.stdout.strip()!=reviewed['sdk_version']:
                raise ProviderError('FC-06 actual .NET SDK differs from reviewed global.json pin')
            dotnet_shared_store=Path(_tempfile.mkdtemp(prefix='devtool-fc06-nuget-'))
            env={**specs[0].env,'NUGET_PACKAGES':str(dotnet_shared_store),
                 'DOTNET_SKIP_FIRST_TIME_EXPERIENCE':'1','NUGET_XMLDOC_MODE':'skip'}
            specs=[replace(specs[0],env=env)]
            for solution in reviewed['solutions']:
                bootstrap=replace(specs[0],name=specs[0].name+' [locked shared restore '+solution+']',
                                  argv=['dotnet','restore',solution,'--locked-mode'])
                restored,_=run_specs(cfg,[bootstrap],verbose=True,dry_run=False,continue_on_error=False)
                if not restored or any(x.returncode!=0 or x.skipped for x in restored):
                    raise ProviderError('FC-06 shared locked restore failed; downstream job refused')
        else:
            # Compatibility path is correctly labelled repeated independent restore.
            selected_spec=specs[0]
            bootstrap=replace(selected_spec,name=selected_spec.name+' [locked restore]',
                              argv=['dotnet','restore',selected['dotnet_selected'],'--locked-mode'])
            restored,_=run_specs(cfg,[bootstrap],verbose=True,dry_run=False,continue_on_error=False)
            if not restored or any(x.returncode!=0 or x.skipped for x in restored):
                raise ProviderError('RM-09 native locked restore failed; no downstream output accepted')
    if selected['provider']=='rust-cargo':
        # Native runner receives an exact preinstalled toolchain selector. Cargo
        # is forbidden from fetching missing registries or toolchains.
        from dataclasses import replace
        if len(specs)!=1 or specs[0].argv!=selected['rust_base_argv']:
            raise ProviderError('RM-10 direct Cargo native job resolver diverged from review')
        from .actions_rm10_cargo import feature_cell_argv, CargoError
        try: reviewed_command=feature_cell_argv(specs[0].argv,selected['rust_feature_cell'])
        except CargoError as exc:raise ProviderError(str(exc)) from None
        env=dict(specs[0].env)
        env.update({'RUSTUP_TOOLCHAIN':selected['toolchain_version'],
                    'RUSTUP_AUTO_INSTALL':'0','CARGO_NET_OFFLINE':'true',
                    'RUSTFLAGS':'','CARGO_ENCODED_RUSTFLAGS':'','RUSTDOCFLAGS':'',
                    'RUSTC_WRAPPER':'','CARGO_BUILD_RUSTC_WRAPPER':'',
                    'CARGO_TARGET_DIR':'target',
                    'CARGO_BUILD_TARGET':selected['rust_target'],
                    'CARGO_NET_GIT_FETCH_WITH_CLI':'false'})
        if selected['rust_target']=='aarch64-unknown-linux-gnu':
            import shutil as _shutil
            if not _shutil.which('aarch64-linux-gnu-gcc'):
                raise ProviderError('RM-10 preinstalled aarch64 cross linker is unavailable')
            env['CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER']='aarch64-linux-gnu-gcc'
        specs=[replace(specs[0],argv=reviewed_command,env=env)]
    if selected['auto_coverage']:
        # Instrument the same reviewed native target-local job, rather than
        # running an extra unowned test command from the hosted exporter.
        from dataclasses import replace
        from tempfile import TemporaryDirectory
        if len(specs)!=1 or specs[0].argv[:3] not in (['python','-m','pytest'],['python3','-m','pytest']):
            raise ProviderError('RM-08 coverage native resolver diverged from reviewed pytest job')
        report_path=_regular(root,'dist/coverage.xml',must_exist=False)
        if report_path.exists() or report_path.is_symlink():
            raise ProviderError('RM-08 coverage output exists before instrumentation')
        report_path.parent.mkdir(parents=True,exist_ok=True)
        with TemporaryDirectory(prefix='devtool-coverage-') as temp:
            env=dict(specs[0].env)
            env['COVERAGE_FILE']=str(Path(temp)/'.coverage')
            original=specs[0]
            tests=replace(original,name=original.name+' [coverage tests]',
                          argv=[original.argv[0],'-m','coverage','run','-m','pytest',*original.argv[3:]],env=env)
            xml=replace(original,name=original.name+' [coverage report]',
                        argv=[original.argv[0],'-m','coverage','xml','-o','dist/coverage.xml'],env=env)
            results,_=run_specs(cfg,[tests],verbose=True,dry_run=False,continue_on_error=False)
            if any(r.returncode!=0 or r.skipped for r in results):
                raise ProviderError('RM-08 instrumented native pytest job failed; no coverage report accepted')
            reports,_=run_specs(cfg,[xml],verbose=True,dry_run=False,continue_on_error=False)
            results+=reports
    else:
        results,_=run_specs(cfg,specs,verbose=True,dry_run=False,continue_on_error=False)
    if any(result.returncode!=0 or result.skipped for result in results):
        if selected['provider']=='shell-ci':
            raise ProviderError('RM-14 shell native node '+selected['node']+' ('+
                                selected['shell_review']['operation']+') did not succeed')
        raise ProviderError('RM-06 native target-local job did not succeed')
    if dotnet_shared_store is not None:
        from .actions_rm09_dotnet import verify_shared_restore_store,DotNetError
        try:
            receipt=verify_shared_restore_store(root,selected['dotnet_shared_restore'],
                dotnet_shared_store,sdk_version=selected['toolchain_version'],
                restored_solutions=selected['dotnet_shared_restore']['solutions'])
            receipt.update({'source_commit':source,'graph_sha256':graph_sha,
                            'run_id':run,'attempt':attempt,'node':selected['node'],
                            'store_identity_sha256':hashlib.sha256(str(dotnet_shared_store).encode()).hexdigest(),
                            'store_isolation':'new per native job, shared only within this job'})
            node_receipts.append(receipt)
        except DotNetError as exc:raise ProviderError(str(exc)) from None
    smoke=[]
    if selected['sdist_smoke']:
        from .actions_distributions import distribution_contract, _python_source
        from .actions_rm08_projects import smoke_sdist
        contract=distribution_contract(root,target=target,workflow=workflow)
        sd=next((item['expected'] for item in contract['entries']
                 if item['expected']['path'] in selected['outputs'] and item['kind']=='sdist'),None)
        if sd is None:raise ProviderError('RM-08 sdist smoke lost owned archive identity')
        scripts=_python_source(root)['entrypoints']
        if len(scripts)!=1:raise ProviderError('RM-08 sdist smoke requires exactly one console script')
        smoke.append(smoke_sdist(root,sd['path'],name=sd['name'],version=sd['version'],entrypoint=scripts[0]))
    if selected['wheel_smoke']:
        from .actions_distributions import distribution_contract
        from .actions_rm08_projects import smoke_wheel
        contract=distribution_contract(root,target=target,workflow=workflow)
        wheel=next((item['expected'] for item in contract['entries']
                    if item['expected']['path'] in selected['outputs'] and item['kind']=='wheel'),None)
        if wheel is None:
            raise ProviderError('RM-08 wheel smoke lost owned distribution contract')
        smoke.append(smoke_wheel(root,wheel['path'],name=wheel['name'],
                                 version=wheel['version'],entrypoint=wheel['entrypoints'][0]))
    repeat_build=[]
    if selected['reproducible_go']:
        from .actions_rm08_projects import verify_go_repeat_build
        repeat_build.append(verify_go_repeat_build(root,selected['go_command'],selected['go_env']))
    coverage=[]
    if selected['coverage_report']:
        from .actions_rm08_projects import merge_coverage_xml
        if 'dist/coverage.xml' not in selected['outputs']:
            raise ProviderError('RM-08 coverage reporter must declare its output')
        coverage.append(merge_coverage_xml(root,['dist/coverage.xml']))
    if selected['provider']=='dotnet':
        from .actions_rm09_dotnet import (merge_trx, DotNetError, collect_dotnet_coverage,
                                          verify_publish_receipt, make_publish_receipt)
        if selected['dotnet_coverage']:
            try:
                coverage.append(collect_dotnet_coverage(root,selected['node']))
            except DotNetError as exc:raise ProviderError(str(exc)) from None
        if selected['dotnet_command']=='publish':
            try:
                receipt=make_publish_receipt(root,selected['outputs'],
                    selected['dotnet_selected'],selected['dotnet_graph']['sources'],selected['node'])
                verify_publish_receipt(root,receipt)
                node_receipts.append(receipt)
            except DotNetError as exc:raise ProviderError(str(exc)) from None
        from .actions_rm09_dotnet import merge_trx, DotNetError
        trx=[p for p in selected['outputs'] if p.endswith('.trx')]
        if selected['dotnet_command']=='test':
            try:merge_trx(root,trx,expected_project=selected['dotnet_selected'])
            except DotNetError as exc:raise ProviderError(str(exc)) from None
        if selected['dotnet_command']=='pack':
            from .actions_rm09_dotnet import verify_nupkg
            package=selected['dotnet_package']
            files=[p for p in selected['outputs'] if p.endswith('.nupkg')]
            if not package or len(files)!=1:
                raise ProviderError('RM-09 pack lost declared NuGet identity')
            try:verify_nupkg(_regular(root,files[0]),**package)
            except DotNetError as exc:raise ProviderError(str(exc)) from None
    if selected['provider']=='node' and selected['node_kind']=='build':
        # The manifest is generated by the trusted native runner, not by an
        # unchecked package script; it attests only explicitly owned outputs.
        intended=[p for p in selected['outputs'] if p!='dist/manifest.json']
        manifest=_regular(root,'dist/manifest.json',must_exist=False)
        if manifest.exists() or manifest.is_symlink():
            raise ProviderError('RM-11 source job must not pre-create trusted manifest')
        manifest.parent.mkdir(parents=True,exist_ok=True)
        signed={}
        for rel in intended:
            signed[rel.removeprefix('dist/')]=_hash_file(_regular(root,rel))
        manifest.write_text(json.dumps({'files':signed},sort_keys=True)+'\n')
    if selected['provider']=='node':
        from .actions_rm11_node import verify_js_report, verify_js_artifacts, NodeError
        try:
            if selected['node_kind']=='test':
                node_receipts.append(verify_js_report(root,selected['outputs'][0],'test'))
            elif selected['node_kind'] in ('pack','build'):
                node_receipts+=verify_js_artifacts(root,selected['outputs'],selected['node_kind'],
                                                   selected['node_workspace'],selected['node_package_path'])
        except NodeError as exc:raise ProviderError(str(exc)) from None
    shell_receipts=[]
    if selected['provider']=='shell-ci' and selected['shell_review']['operation']=='test':
        from .actions_rm14_shell import verify_bats_report, ShellError
        try:shell_receipts.append(verify_bats_report(root))
        except ShellError as exc:raise ProviderError('RM-14 shell native node '+selected['node']+': '+str(exc)) from None
    cpp_receipts=[]
    if selected['provider']=='cpp' and selected['cpp_operation'] in ('package','symbols'):
        from .actions_rm13_cpp import verify_binary,CppError
        import zipfile as _zipfile
        binary_name=selected['cpp_binary_name']
        fmt=selected['cpp_abi']
        build=selected['cpp_project']['profile']['build_dir']
        rel=build+'/'+(binary_name+'.exe' if fmt=='pe-x86_64' else binary_name)
        candidate=_regular(root,rel)
        try:cpp_receipts.append(verify_binary(candidate,abi=fmt,symbols=selected['cpp_symbols']))
        except CppError as exc:raise ProviderError(str(exc)) from None
        if selected['cpp_operation']=='symbols':
            rel_out=selected['outputs'][0]
            dest=_regular(root,rel_out,must_exist=False)
            if dest.exists() or dest.is_symlink():raise ProviderError('RM-13 symbols artifact already exists')
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(candidate,dest)
            if _hash_file(candidate)!=_hash_file(dest):raise ProviderError('RM-13 copied symbol artifact differs')
        else:
            dest=_regular(root,'dist/'+binary_name+'.zip',must_exist=False)
            if dest.exists() or dest.is_symlink():raise ProviderError('RM-13 package output already exists')
            dest.parent.mkdir(parents=True,exist_ok=True)
            with _zipfile.ZipFile(dest,'w',compression=_zipfile.ZIP_STORED) as out:
                item=_zipfile.ZipInfo('bin/'+candidate.name,date_time=(1980,1,1,0,0,0))
                item.external_attr=0o100755 << 16
                out.writestr(item,candidate.read_bytes())
                item=_zipfile.ZipInfo('byte-receipt.json',date_time=(1980,1,1,0,0,0))
                item.external_attr=0o100644 << 16
                out.writestr(item,json.dumps({'sha256':_hash_file(candidate),'source_digest':
                    selected['cpp_project']['source_digest'],'abi':fmt},sort_keys=True)+'\n')
    if selected['provider']=='cpp':
        from .actions_rm13_cpp import normalize_ctest_report,verify_binary,verify_archive,CppError
        try:
            if selected['cpp_operation']=='test':
                generated=_regular(root,selected['cpp_project']['profile']['build_dir']+
                    ('/dist/cpp-tests.xml' if selected['cpp_manager']=='cmake' else '/meson-logs/testlog.junit.xml'))
                cpp_receipts.append(normalize_ctest_report(generated))
                final=_regular(root,'dist/cpp-tests.xml',must_exist=False)
                if final.exists() or final.is_symlink():raise ProviderError('RM-13 CTest report would overwrite output')
                final.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(generated,final)
                if _hash_file(generated)!=_hash_file(final):raise ProviderError('RM-13 report staging changed bytes')
            for path in selected['outputs']:
                if path=='dist/cpp-tests.xml':continue
                p=_regular(root,path)
                if path.endswith(('.a','.zip')):cpp_receipts.append(verify_archive(p,expected_abi=selected['cpp_abi']))
                else:cpp_receipts.append(verify_binary(p,abi=selected['cpp_abi'],symbols=selected['cpp_symbols']))
        except CppError as exc:raise ProviderError(str(exc)) from None
    jvm_receipts=[]
    if selected['provider']=='jvm':
        from .actions_rm12_jvm import aggregate_junit, verify_jar, verify_desktop_bundle, JvmError
        from .actions_rm12_jvm import materialize_jvm_package
        try:
            if selected['jvm_operation']=='test':
                jvm_receipts.append(aggregate_junit(root,selected['jvm_project'],selected['jvm_selected']))
            elif selected['jvm_operation']=='package':
                jvm_receipts+=materialize_jvm_package(root,selected['jvm_project'],selected['jvm_selected'],
                    selected['jvm_artifact_name'],desktop=selected['jvm_desktop_bundle'])
        except JvmError as exc:raise ProviderError(str(exc)) from None
    rust_receipts=[];rust_test_evidence=[]
    if selected['provider']=='rust-cargo':
        if selected['rust_json_capture']:
            from .actions_rm10_cargo import verify_native_cargo_output, CargoError
            if len(results)!=1:
                raise ProviderError('RM-10 expected one native Cargo result')
            try:
                evidence=verify_native_cargo_output(results[0].output,operation=selected['rust_command'],
                   source_sha256=selected['rust_workspace']['source_sha256'],
                   version=selected['toolchain_version'],run_id=run,attempt=attempt,
                   expected_features=[] if selected['rust_feature_cell']=='none' else selected['rust_feature_cell'].split(','),
                   target=selected['rust_target'],argv=specs[0].argv,rust_env=specs[0].env)
            except CargoError as exc:raise ProviderError(str(exc)) from None
            rust_test_evidence.append(evidence)
        from .actions_rm10_cargo import verify_rust_binary, verify_crate, CargoError
        for rel in selected['outputs']:
            path=_regular(root,rel)
            try:
                if rel.endswith('.crate'):
                    name=selected['rust_package']
                    package=next(p for p in selected['rust_workspace']['members'].values() if p['name']==name)
                    rust_receipts.append(verify_crate(path,name=name,version=package['version']))
                else:
                    rust_receipts.append(verify_rust_binary(path,target=selected['rust_target']))
            except CargoError as exc:raise ProviderError(str(exc)) from None
    if selected['provider']=='rust-cargo' and selected['rust_json_capture'] and selected['rust_command']=='build' and selected['outputs']:
        reported={Path(path).resolve() for event in rust_test_evidence for path in event['artifacts']}
        for rel in selected['outputs']:
            if (root/rel).resolve() not in reported:
                raise ProviderError('RM-10 compiler JSON did not attest declared native binary artifact: '+rel)
    stage_outputs(root,outputs,graph,selected,source,run,attempt)
    return {'status':'LOCAL_JOB_SUCCEEDED', 'node':selected['node'], 'job_id':job_id,
            'graph_sha256':graph_sha, 'workflow_sha256':hashlib.sha256(workflow_yaml(graph).encode()).hexdigest(), 'github_source':source,'run_id':run,'attempt':attempt,
            'wheel_smoke':smoke,'coverage':coverage,'repeat_build':repeat_build,'node_receipts':node_receipts,'shell_receipts':shell_receipts,'cpp_receipts':cpp_receipts,'jvm_receipts':jvm_receipts,'rust_receipts':rust_receipts,'rust_test_evidence':rust_test_evidence,'uploaded_artifacts_authenticated':False,'release_qualified':False}


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description='Internal RM-06 reviewed multi-job runner; no auth/qualification claim')
    parser.add_argument('action',choices=['run-node'])
    for flag in ('root','target','workflow','devtool-sha','graph-sha','job-id','inputs','outputs'):
        parser.add_argument('--'+flag,required=True)
    parser.add_argument('--host-profiles',default='')
    parser.add_argument('--cache-locks',action='store_true')
    ns=parser.parse_args(argv)
    try:
        result=run_node(Path(ns.root),target=ns.target,workflow=ns.workflow,devtool_sha=ns.devtool_sha,
                        graph_sha=ns.graph_sha,job_id=ns.job_id,inputs=Path(ns.inputs),outputs=Path(ns.outputs),
                        host_profiles=ns.host_profiles,cache_locks=ns.cache_locks)
    except (ProviderError, ValueError, OSError) as exc:
        print(f'RM06_REFUSED: {exc}',file=sys.stderr)
        return 2
    print(json.dumps(result,sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

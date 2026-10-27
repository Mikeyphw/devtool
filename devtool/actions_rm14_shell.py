"""RM-14: read-only, source-bound Shell/Termux hosted job review.

No discovery-time evaluation, sourced files, environment interpolation, network
installers, or unconstrained shell expression execution.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

class ShellError(ValueError):pass

_PATH=re.compile(r'[A-Za-z0-9_./ -]{1,180}\Z')
_DIALECTS={'bash':('bash','5'), 'posix':('sh','posix'), 'zsh':('zsh','5')}
_TOOLS={'shellcheck','shfmt','bats','bash','sh','zsh'}
_REPORT='dist/report.xml'

def _tracked_source(root:Path, rel:str)->bytes:
    if not isinstance(rel,str) or not _PATH.fullmatch(rel) or rel.startswith('/') or any(x in ('.','..','') for x in rel.split('/')):
        raise ShellError('RM-14 unsafe or unquoted shell source path')
    current=root
    for part in rel.split('/'):
        current=current/part
        if current.is_symlink():raise ShellError('RM-14 symlink source refused')
    if not current.is_file() or current.stat().st_size>524288:
        raise ShellError('RM-14 missing or unbounded reviewed source')
    proc=subprocess.run(['git','-C',str(root),'ls-files','--error-unmatch','--',rel],capture_output=True,timeout=8)
    if proc.returncode:raise ShellError('RM-14 shell source must be tracked')
    raw=current.read_bytes()
    if b'\x00' in raw:raise ShellError('RM-14 binary shell source refused')
    try:raw.decode('utf-8')
    except UnicodeError:raise ShellError('RM-14 shell source requires UTF-8') from None
    return raw

def _shebang(data:bytes)->str:
    line=data.split(b'\n',1)[0].decode('utf-8',errors='replace')
    if not line.startswith('#!'):raise ShellError('RM-14 explicit trusted shebang required')
    cmd=line[2:].strip().split()
    if not cmd:raise ShellError('RM-14 empty shebang')
    if cmd[0] in ('/usr/bin/env','/bin/env'):
        if len(cmd)!=2:raise ShellError('RM-14 unreviewed env shebang options')
        base=cmd[1]
    else:
        if len(cmd)!=1 or not cmd[0].startswith(('/bin/','/usr/bin/','/data/data/com.termux/files/usr/bin/')):
            raise ShellError('RM-14 unreviewed shell executable path')
        base=cmd[0].rsplit('/',1)[-1]
    return {'bash':'bash','sh':'posix','dash':'posix','zsh':'zsh','bats':'bats'}.get(base,'unknown')

def inspect_shell_job(root:Path, argv:list[str], meta:dict, *,host_os:str='linux')->dict:
    if not isinstance(meta,dict):raise ShellError('RM-14 invalid shell metadata')
    dialect=meta.get('ci_shell_dialect')
    if dialect not in _DIALECTS:raise ShellError('RM-14 explicit bash/posix/zsh dialect required')
    version=meta.get('ci_shell_version')
    if version!=_DIALECTS[dialect][1]:raise ShellError('RM-14 shell version must be reviewed major or posix')
    host=meta.get('ci_shell_host','hosted')
    if host not in ('hosted','termux'):raise ShellError('RM-14 invalid shell host policy')
    if host=='termux' and host_os!='android':raise ShellError('RM-14 Termux-only job refused on ordinary hosted Ubuntu')
    if host=='hosted' and host_os=='android':raise ShellError('RM-14 hosted job must not claim Android parity')
    if not isinstance(argv,list) or len(argv)<2 or len(argv)>7 or any(not isinstance(s,str) for s in argv):
        raise ShellError('RM-14 bounded literal argv required')
    tool=argv[0]
    if tool not in _TOOLS:raise ShellError('RM-14 no reviewed shell tool or executable')
    script=argv[-1]
    raw=_tracked_source(root,script)
    declared=_shebang(raw)
    body=raw.decode('utf-8')
    # Detect Android-specific syntax and Tasker/Termux integrations without
    # running discovery-time scripts. Hosted syntax success is not device parity.
    termux_pattern=r'(?i)(?:/data/data/com\.termux|\btermux-[a-z]|\btasker\b|com\.joaomgcd|\$\{?PREFIX\}?|\b(?:am\s+(?:start|broadcast)|cmd\s+package|pm\s+grant)\b)'
    if host=='hosted' and re.search(termux_pattern,body):
        raise ShellError('RM-14 Tasker/Termux-sensitive source is Android-native only')
    if tool=='bats':
        if declared!='bats' or not script.endswith('.bats'):
            raise ShellError('RM-14 Bats requires tracked Bats shebang/filename')
        if dialect!='bash':raise ShellError('RM-14 Bats tests require Bash dialect')
        expected=['bats','--report-formatter','junit','--output','dist',script]
        operation='test'
    else:
        if declared!=dialect:raise ShellError('RM-14 declared dialect and script shebang differ')
        if tool in ('bash','sh','zsh'):
            if tool!=_DIALECTS[dialect][0]:raise ShellError('RM-14 interpreter differs from reviewed dialect')
            expected=[tool,'-n',script];operation='syntax'
        elif tool=='shellcheck':
            if dialect=='zsh':raise ShellError('RM-14 ShellCheck has no Zsh dialect; use shfmt -d')
            expected=['shellcheck','--norc','--shell='+('sh' if dialect=='posix' else 'bash'),'--severity=style',script]
            operation='lint'
        else:
            if dialect=='zsh':
                raise ShellError('RM-14 reviewed Ubuntu shfmt 3.8 lacks Zsh formatting; use zsh -n')
            expected=['shfmt','-d','-ln='+dialect,script];operation='format'
    if argv!=expected:
        raise ShellError('RM-14 unsafe command argv; only exact reviewed tools and flags are allowed')
    # Source directives are executable code at runtime. Syntax/lint/formats do not
    # execute the script, Bats is executable and therefore needs additional review.
    if operation=='test':
        body=raw.decode('utf-8')
        if re.search(r'(?m)^\s*(?:load|source|\.)\s',body) or re.search(r'(?i)\b(?:curl|wget|su|sudo|eval|exec|ssh|scp|nc|ncat)\b',body):
            raise ShellError('RM-14 Bats script has unreviewed runtime imports or operations')
        if re.search(r'(?i)(?:/data/data/com\.termux|\btermux-|\b(?:am|pm|cmd|settings)\s)',body) and host!='termux':
            raise ShellError('RM-14 Termux/Android script cannot execute on hosted Linux')
    digests={script:hashlib.sha256(raw).hexdigest()}
    return {'provider':'shell-ci','toolchain':'shell','api_version':1,'version':version,'matrix_versions':[version],
            'dialect':dialect,'host_policy':host,'operation':operation,'script':script,'argv':argv,
            'source_sha256':digests,'source_digest':hashlib.sha256(json.dumps(digests,sort_keys=True).encode()).hexdigest(),
            'lockfiles':[script], 'cache_enabled':False,'report_adapter':'bats-junit' if operation=='test' else 'exit-status',
            'output_role':'local-bytes-only','release_qualified':False}

def verify_bats_report(root:Path, rel:str='dist/report.xml')->dict:
    if rel!=_REPORT:raise ShellError('RM-14 wrong owned Bats report')
    path=root/rel
    if path.is_symlink() or not path.is_file() or path.stat().st_size>2_000_000:
        raise ShellError('RM-14 missing, symlinked or unbounded Bats report')
    raw=path.read_bytes()
    if any(marker in raw.upper() for marker in (b'<!DOCTYPE',b'<!ENTITY',b'<?XML-STYLESHEET')):
        raise ShellError('RM-14 XML entity/doctype refused')
    try:document=ET.fromstring(raw)
    except ET.ParseError:raise ShellError('RM-14 malformed Bats JUnit XML') from None
    suites=[document] if document.tag=='testsuite' else list(document) if document.tag=='testsuites' else []
    if not suites or any(s.tag!='testsuite' for s in suites):raise ShellError('RM-14 unexpected JUnit suite structure')
    total=failed=skipped=0;identities=set()
    for s in suites:
        cases=list(s)
        if not cases or any(c.tag!='testcase' for c in cases):raise ShellError('RM-14 missing or unreviewed test-case entries')
        def number(name):
            raw_value=s.attrib.get(name,'0')
            if not re.fullmatch(r'\d{1,7}',raw_value):raise ShellError('RM-14 invalid JUnit counter')
            return int(raw_value)
        n=number('tests'); f=number('failures')+number('errors'); sk=number('skipped')
        if n!=len(cases) or f>n or sk>n:raise ShellError('RM-14 inconsistent JUnit counts')
        counted_failed=counted_skipped=0
        for c in cases:
            ident=(c.attrib.get('classname'),c.attrib.get('name'))
            if not all(ident) or ident in identities:raise ShellError('RM-14 missing/duplicate test identity')
            identities.add(ident)
            for v in c:
                if v.tag in ('failure','error'):counted_failed+=1
                elif v.tag=='skipped':counted_skipped+=1
                else:raise ShellError('RM-14 unreviewed JUnit testcase content')
        if counted_failed!=f or counted_skipped!=sk:raise ShellError('RM-14 JUnit failed/skipped counts differ from cases')
        total+=n;failed+=f;skipped+=sk
    if total==0 or failed>0 or total==skipped:raise ShellError('RM-14 Bats report lacks passing tests or contains failures')
    return {'kind':'bats-junit','tests':total,'failed':failed,'skipped':skipped,
            'sha256':hashlib.sha256(raw).hexdigest(),'release_qualified':False}

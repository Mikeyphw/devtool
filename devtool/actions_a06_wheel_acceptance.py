"""Source-bound native acceptance for ACTIONS-A06 wheel data repair."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import hashlib,json,os
from pathlib import Path
import re,subprocess,sys

PARENT='08eb13991b5d1c623b64fdd8f9524418ee27207e'
PATHS=('pyproject.toml','devtool/actions_a06_wheel_acceptance.py',
       'docs/DEVTOOL_IMPROVEMENTS.md','docs/DEVTOOL_ACTIONS_WHEEL_DISTRIBUTION_A06.md',
       'tests/test_actions_a06_wheel_distribution.py')

def git(root:Path,*argv:str)->str:
    p=subprocess.run(['git','-C',str(root),*argv],text=True,capture_output=True,timeout=25)
    if p.returncode: raise ValueError('Git check failed: '+' '.join(argv))
    return p.stdout.strip()

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo',type=Path,required=True)
    opts=ap.parse_args(argv)
    root=opts.repo.expanduser().resolve()
    try:
        head=git(root,'rev-parse','HEAD')
        if git(root,'rev-parse','HEAD^')!=PARENT:raise ValueError('A06 must directly follow accepted A04')
        if git(root,'branch','--show-current')!='feature/rmp01-security-remediation':raise ValueError('Unexpected branch')
        if git(root,'status','--porcelain','--untracked-files=all'):raise ValueError('Dirty source tree')
        scope=git(root,'diff-tree','--no-commit-id','--name-only','-r','HEAD').splitlines()
        if sorted(scope)!=sorted(PATHS):raise ValueError('Unexpected commit scope')
        hashes={}
        for rel in PATHS:
            path=root/rel
            if path.is_symlink() or not path.is_file():raise ValueError('Missing source: '+rel)
            data=path.read_bytes()
            saved=subprocess.run(['git','-C',str(root),'show','HEAD:'+rel],capture_output=True,timeout=25)
            if saved.returncode or saved.stdout!=data:raise ValueError('Source differs from HEAD: '+rel)
            hashes[rel]=hashlib.sha256(data).hexdigest()
        prev=subprocess.run(['git','-C',str(root),'show','HEAD^:docs/DEVTOOL_IMPROVEMENTS.md'],capture_output=True,check=True).stdout
        if not (root/'docs/DEVTOOL_IMPROVEMENTS.md').read_bytes().startswith(prev):
            raise ValueError('Append-only improvements ledger violated')
        proc=subprocess.run([sys.executable,'-m','pytest','-q','tests/test_actions_a06_wheel_distribution.py'],
                            cwd=root,capture_output=True,text=True,timeout=210)
        if proc.returncode:raise ValueError('Wheel distribution tests failed: '+proc.stdout[-1800:]+proc.stderr[-500:])
        count=re.search(r'(\d+) passed(?:, (\d+) skipped)?',proc.stdout)
        if not count or int(count.group(1))!=3 or int(count.group(2) or 0):
            raise ValueError('Expected three native tests, no skips')
        now=datetime.now(timezone.utc)
        rec={'schema_version':1,'producer':'devtool-actions-a06-wheel-distribution',
             'overall':'PASS_ACTIONS_A06_WHEEL_LOCAL_ACCEPTANCE',
             'source_commit':head,'source_parent':PARENT,'source_tree':git(root,'rev-parse','HEAD^{tree}'),
             'source_sha256':hashes,'pytest_passed':3,'pytest_skipped':0,
             'wheel_archive_checked':True,'isolated_wheel_install_checked':True,
             'github_run_qualified':False,'remote_wheel_qualified':False,
             'android_device_qualified':False,'release_qualified':False,'timestamp_utc':now.isoformat()}
        dest=Path.home()/'.local/state/devtool/actions/a06'
        dest.mkdir(parents=True,exist_ok=True)
        out=dest/('acceptance-'+now.strftime('%Y%m%dT%H%M%S%fZ')+'.json')
        fd=os.open(out,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as h:json.dump(rec,h,sort_keys=True,indent=2);h.write('\n')
        print(json.dumps({'overall':rec['overall'],'source_commit':head,'pytest_passed':3,
                          'pytest_skipped':0,'receipt':str(out),'remote_wheel_qualified':False},sort_keys=True))
        return 0
    except (ValueError,OSError,subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:
        print(json.dumps({'overall':'REFUSED_ACTIONS_A06_WHEEL_ACCEPTANCE','reason':str(exc)},sort_keys=True),file=sys.stderr)
        return 2
if __name__=='__main__':raise SystemExit(main())

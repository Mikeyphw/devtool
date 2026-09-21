from __future__ import annotations
import argparse, os, subprocess, sys
from .test_protocol import emit

def main(argv=None):
    p=argparse.ArgumentParser(prog="devtool-test"); sub=p.add_subparsers(dest="cmd",required=True)
    b=sub.add_parser("begin"); b.add_argument("--id",required=True); b.add_argument("--name",default="")
    e=sub.add_parser("end"); e.add_argument("--id",required=True); e.add_argument("--name",default="")
    for name,status in (("pass","passed"),("fail","failed"),("error","error"),("skip","skipped"),("cancel","cancelled")):
        q=sub.add_parser(name); q.add_argument("id"); q.add_argument("--suite",default="default"); q.add_argument("--name",default=""); q.add_argument("--message",default=""); q.set_defaults(status=status)
    r=sub.add_parser("run"); r.add_argument("--id",required=True); r.add_argument("--name",default=""); r.add_argument("--runner",default="command"); r.add_argument("command",nargs=argparse.REMAINDER)
    a=p.parse_args(argv)
    if a.cmd=="begin": emit({"type":"suite_started","id":a.id,"name":a.name or a.id}); return 0
    if a.cmd=="end": emit({"type":"suite_finished","id":a.id,"name":a.name or a.id}); return 0
    if a.cmd in {"pass","fail","error","skip","cancel"}: emit({"type":"case","suite":a.suite,"id":a.id,"name":a.name or a.id,"status":a.status,"message":a.message}); return 0
    cmd=list(a.command); cmd=cmd[1:] if cmd[:1]==["--"] else cmd
    if not cmd: p.error("run requires a command after --")
    emit({"type":"suite_started","id":a.id,"name":a.name or a.id}); rc=subprocess.call(cmd); emit({"type":"case","suite":a.id,"id":a.id+"#process","name":a.name or a.id,"status":"passed" if rc==0 else "failed","message":"" if rc==0 else f"process exited {rc}"}); emit({"type":"suite_finished","id":a.id}); return rc
if __name__=="__main__": raise SystemExit(main())

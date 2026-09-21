"""Versioned devtool.test/v1 event protocol for embedded validators/hooks."""
from __future__ import annotations
import json, os
from dataclasses import dataclass, field
from typing import Any

PROTOCOL = "devtool.test/v1"

def emit(event: dict[str, Any]) -> None:
    fd_raw=os.environ.get("DEVTOOL_TEST_EVENT_FD","").strip()
    if not fd_raw: return
    payload={"protocol":PROTOCOL, **event}
    os.write(int(fd_raw),(json.dumps(payload,sort_keys=True,separators=(",",":"))+"\n").encode())

def parse_line(line: str) -> dict[str, Any] | None:
    try: data=json.loads(line)
    except (TypeError,ValueError): return None
    if not isinstance(data,dict) or data.get("protocol")!=PROTOCOL: return None
    return data

@dataclass
class EventAccumulator:
    suites: dict[str,dict[str,Any]]=field(default_factory=dict)
    def feed(self,event:dict[str,Any]):
        typ=str(event.get("type") or ""); sid=str(event.get("suite") or event.get("id") or "")
        if typ=="suite_started": self.suites.setdefault(sid,{"id":sid,"name":event.get("name") or sid,"cases":[]})
        elif typ=="case":
            sid=str(event.get("suite") or "default"); s=self.suites.setdefault(sid,{"id":sid,"name":sid,"cases":[]}); s["cases"].append(dict(event))
        elif typ=="suite_finished": self.suites.setdefault(sid,{"id":sid,"name":event.get("name") or sid,"cases":[]}).update({"finished":True})
    def to_result(self,result_id="embedded"):
        from .test_model import TestCaseResult,TestFailure,TestResult,TestStatus,TestSuiteResult
        suites=[]
        status_map={x.value:x for x in TestStatus}
        for sid,s in self.suites.items():
            cases=[]
            for i,c in enumerate(s["cases"]):
                st=status_map.get(str(c.get("status") or "error"),TestStatus.ERROR); msg=str(c.get("message") or "")
                cases.append(TestCaseResult(str(c.get("id") or f"{sid}#{i+1}"),str(c.get("name") or c.get("id") or f"case-{i+1}"),st,
                    duration_seconds=c.get("duration_seconds"),failure=TestFailure(msg) if msg and st in {TestStatus.FAILED,TestStatus.ERROR,TestStatus.XPASS} else None,
                    metadata={"evidence":"protocol"}))
            suites.append(TestSuiteResult(sid,str(s.get("name") or sid),tuple(cases),metadata={"evidence":"protocol"}))
        return TestResult(result_id,tuple(suites),metadata={"evidence":"protocol","protocol":PROTOCOL})

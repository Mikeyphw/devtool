from __future__ import annotations
"""TEST-X03 canonical performance evidence and safe test-runtime planning."""
from dataclasses import dataclass, field, asdict
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence
import hashlib, json, os, platform, time

PHASES=("startup","collection","setup","call","teardown")

@dataclass(frozen=True, slots=True)
class TestPerformanceSample:
    __test__=False
    check_id:str; phase:str; seconds:float; file:str=""; tool:str=""; subprocess_seconds:float=0.0; git_seconds:float=0.0
    def __post_init__(self):
        if self.phase not in PHASES: raise ValueError(f"invalid test performance phase: {self.phase}")
        if min(self.seconds,self.subprocess_seconds,self.git_seconds)<0: raise ValueError("performance durations must be >= 0")

@dataclass(frozen=True, slots=True)
class TestPerformanceProfile:
    __test__=False
    schema_version:int; profile_id:str; selection_id:str; host_profile:str; samples:tuple[TestPerformanceSample,...]; file_totals:Mapping[str,float]; total_seconds:float; budget_seconds:float|None=None; streamed_log:str=""
    def __post_init__(self):
        object.__setattr__(self,"samples",tuple(self.samples)); object.__setattr__(self,"file_totals",MappingProxyType(dict(self.file_totals)))
    @property
    def within_budget(self): return self.budget_seconds is None or self.total_seconds <= self.budget_seconds
    def to_dict(self):
        return {"schema_version":self.schema_version,"profile_id":self.profile_id,"selection_id":self.selection_id,"host_profile":self.host_profile,"samples":[asdict(x) for x in self.samples],"file_totals":dict(self.file_totals),"total_seconds":self.total_seconds,"budget_seconds":self.budget_seconds,"within_budget":self.within_budget,"streamed_log":self.streamed_log}

def host_profile(*, system:str|None=None, prefix:str|None=None)->str:
    sysname=(system or platform.system()).lower(); pref=prefix if prefix is not None else os.environ.get("PREFIX","")
    return "termux" if "com.termux" in pref else ("desktop" if sysname in {"linux","darwin","windows"} else sysname)

def build_performance_profile(selection_id:str, samples:Iterable[TestPerformanceSample], *, budget_seconds:float|None=None, streamed_log:str="", host:str|None=None)->TestPerformanceProfile:
    values=tuple(samples); totals={}
    for s in values:
        key=s.file or "<unknown>"; totals[key]=round(totals.get(key,0.0)+s.seconds,9)
    total=round(sum(s.seconds for s in values),9)
    payload={"selection_id":selection_id,"host":host or host_profile(),"samples":[asdict(s) for s in values],"total":total}
    pid="tprof-"+hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":")).encode()).hexdigest()[:24]
    return TestPerformanceProfile(1,pid,selection_id,host or host_profile(),values,totals,total,budget_seconds,streamed_log)

def append_profile_sample(path:str|Path, sample:TestPerformanceSample)->None:
    target=Path(path); target.parent.mkdir(parents=True,exist_ok=True)
    payload={"schema_version":1,"kind":"test-performance-sample",**asdict(sample)}
    with target.open("a",encoding="utf-8") as handle:
        handle.write(json.dumps(payload,sort_keys=True,separators=(",",":"))+"\n")

def read_profile_samples(path:str|Path)->tuple[TestPerformanceSample,...]:
    result=[]
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        row=json.loads(line)
        if row.get("kind")!="test-performance-sample": continue
        result.append(TestPerformanceSample(**{k:row[k] for k in ("check_id","phase","seconds","file","tool","subprocess_seconds","git_seconds")}))
    return tuple(result)

@dataclass(frozen=True, slots=True)
class QualifiedPythonEnvironment:
    environment_id:str; interpreter:str; qualification_id:str; ready:bool
    def direct_argv(self, argv:Sequence[str])->tuple[str,...]:
        if not self.ready: raise RuntimeError("python environment is not qualified")
        vals=tuple(str(x) for x in argv)
        if not vals: raise ValueError("argv is required")
        return (self.interpreter,*vals)

@dataclass(frozen=True, slots=True)
class RuntimePolicy:
    lane:str; timeout_seconds:float; heartbeat_seconds:float; workers:int; budget_seconds:float|None

def runtime_policy(lane:str, *, host:str|None=None, workers:int=2)->RuntimePolicy:
    lane=str(lane); hp=host or host_profile()
    table={"fast":(120,15,180),"integration":(600,30,900),"external":(900,30,1200),"system":(1800,60,2400),"full":(3600,60,4800)}
    if lane not in table: raise ValueError(f"unknown test lane: {lane}")
    timeout,heartbeat,budget=table[lane]
    if hp=="termux": budget*=1.5
    return RuntimePolicy(lane,float(timeout),float(heartbeat),max(1,int(workers)),float(budget))

@dataclass(frozen=True, slots=True)
class ShardExecution:
    shard_id:str; check_ids:tuple[str,...]; resources:tuple[str,...]=(); serial_constraints:tuple[str,...]=()

def schedule_shards(shards:Iterable[ShardExecution], *, workers:int)->tuple[tuple[ShardExecution,...],...]:
    """Deterministic waves; conflicting resources/serial constraints never share a wave."""
    pending=list(shards); waves=[]; width=max(1,int(workers))
    while pending:
        wave=[]; used=set()
        for shard in list(pending):
            locks=set(shard.resources)|set(shard.serial_constraints)
            if len(wave)>=width or used & locks: continue
            wave.append(shard); used|=locks; pending.remove(shard)
        if not wave: wave=[pending.pop(0)]
        waves.append(tuple(wave))
    return tuple(waves)

@dataclass(frozen=True, slots=True)
class ShardOutcome:
    shard_id:str; check_ids:tuple[str,...]; passed:int=0; failed:int=0; skipped:int=0; profile_path:str=""; junit_path:str=""; log_path:str=""

def aggregate_shards(expected_check_ids:Sequence[str], outcomes:Iterable[ShardOutcome])->dict[str,Any]:
    vals=tuple(outcomes); seen=[c for o in vals for c in o.check_ids]; expected=tuple(sorted(dict.fromkeys(expected_check_ids)))
    duplicate=sorted({c for c in seen if seen.count(c)>1}); missing=sorted(set(expected)-set(seen)); unexpected=sorted(set(seen)-set(expected))
    complete=not (duplicate or missing or unexpected) and len(seen)==len(expected)
    return {"complete":complete,"missing":missing,"duplicate":duplicate,"unexpected":unexpected,"passed":sum(o.passed for o in vals),"failed":sum(o.failed for o in vals),"skipped":sum(o.skipped for o in vals),"attachments":[{"shard_id":o.shard_id,"junit":o.junit_path,"log":o.log_path,"profile":o.profile_path} for o in vals]}

def run_attachment_manifest(run_id:str, selection_id:str, aggregate:Mapping[str,Any])->dict[str,Any]:
    return {"schema_version":3,"run_id":run_id,"selection_id":selection_id,"complete":bool(aggregate.get("complete")),"attachments":list(aggregate.get("attachments") or ()),"summary":{k:int(aggregate.get(k,0)) for k in ("passed","failed","skipped")}}

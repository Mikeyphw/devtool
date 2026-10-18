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
    """A complete selection requires both membership and terminal case counts."""
    vals=tuple(outcomes)
    seen=[c for o in vals for c in o.check_ids]
    expected=tuple(sorted(dict.fromkeys(expected_check_ids)))
    duplicate=sorted({c for c in seen if seen.count(c)>1})
    missing=sorted(set(expected)-set(seen))
    unexpected=sorted(set(seen)-set(expected))
    shard_ids=[o.shard_id for o in vals]
    invalid_shards=sorted({shard_id for shard_id in shard_ids if not shard_id or shard_ids.count(shard_id)>1})
    negative_counts=any(
        isinstance(value,bool) or not isinstance(value,int) or value<0
        for o in vals for value in (o.passed,o.failed,o.skipped)
    )
    count_mismatch=any(o.passed+o.failed+o.skipped!=len(o.check_ids) for o in vals)
    complete=(
        bool(expected) and len(expected)==len(expected_check_ids)
        and not (duplicate or missing or unexpected or invalid_shards or negative_counts or count_mismatch)
        and len(seen)==len(expected)
    )
    return {
        "complete":complete,
        "missing":missing,"duplicate":duplicate,"unexpected":unexpected,
        "invalid_shards":invalid_shards,
        "count_mismatch":bool(count_mismatch or negative_counts),
        "expected_check_count":len(expected),"observed_check_count":len(seen),
        "passed":sum(o.passed for o in vals),
        "failed":sum(o.failed for o in vals),
        "skipped":sum(o.skipped for o in vals),
        "attachments":[{"shard_id":o.shard_id,"case_ids":list(o.check_ids),"passed":o.passed,"failed":o.failed,"skipped":o.skipped,"junit":o.junit_path,"log":o.log_path,"profile":o.profile_path} for o in vals],
    }

def _valid_terminal_attachments(attachments: list[Any], counts: Mapping[str, int], expected: int) -> bool:
    """Bind projected completion to each shard's selected cases and outcomes."""
    if not attachments:
        return False
    cases: list[str] = []
    totals = {key: 0 for key in counts}
    shard_ids: set[str] = set()
    for shard in attachments:
        if not isinstance(shard, dict):
            return False
        shard_id = shard.get("shard_id")
        ids = shard.get("case_ids")
        if not isinstance(shard_id, str) or not shard_id or shard_id in shard_ids:
            return False
        if not isinstance(ids, list) or not ids or any(not isinstance(x, str) or not x for x in ids):
            return False
        shard_ids.add(shard_id)
        values = [shard.get(key) for key in counts]
        if any(type(value) is not int or value < 0 for value in values):
            return False
        if sum(values) != len(ids):
            return False
        for key, value in zip(counts, values):
            totals[key] += value
        cases.extend(ids)
    return len(cases) == expected and len(set(cases)) == expected and totals == dict(counts)

def run_attachment_manifest(run_id:str, selection_id:str, aggregate:Mapping[str,Any])->dict[str,Any]:
    attachments=list(aggregate.get("attachments") or ())
    raw_counts=[aggregate.get(k,0) for k in ("passed","failed","skipped")]
    numeric_counts=all(type(v) is int and v>=0 for v in raw_counts)
    counts=dict(zip(("passed","failed","skipped"),raw_counts)) if numeric_counts else {"passed":0,"failed":0,"skipped":0}
    expected=aggregate.get("expected_check_count")
    observed=aggregate.get("observed_check_count")
    valid_expected=type(expected) is int and expected>0
    safe_complete=(
        aggregate.get("complete") is True
        and numeric_counts and valid_expected
        and type(observed) is int and observed==expected
        and sum(counts.values())==expected
        and _valid_terminal_attachments(attachments,counts,expected)
        and not any(aggregate.get(key) for key in ("missing","duplicate","unexpected","invalid_shards","count_mismatch"))
    )
    return {"schema_version":3,"run_id":run_id,"selection_id":selection_id,"complete":safe_complete,"attachments":attachments,"summary":counts}

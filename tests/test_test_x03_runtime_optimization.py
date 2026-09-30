from devtool.test_runtime_optimization import *

def test_profile_has_phase_file_tool_git_and_budget_truth():
 p=build_performance_profile("sel",[TestPerformanceSample("a","collection",1.0,"tests/a.py",tool="pytest"),TestPerformanceSample("a","call",2.0,"tests/a.py",subprocess_seconds=.5,git_seconds=.2)],budget_seconds=4,host="desktop",streamed_log="profile.ndjson")
 assert p.total_seconds==3 and p.file_totals["tests/a.py"]==3 and p.within_budget and p.to_dict()["streamed_log"]=="profile.ndjson"

def test_termux_and_desktop_budgets_and_heartbeat_are_lane_aware():
 d=runtime_policy("integration",host="desktop"); t=runtime_policy("integration",host="termux")
 assert d.timeout_seconds==600 and d.heartbeat_seconds==30 and t.budget_seconds>d.budget_seconds

def test_qualified_environment_is_restore_once_direct_interpreter_boundary():
 q=QualifiedPythonEnvironment("env1","/qualified/python","qual1",True)
 assert q.direct_argv(("-m","pytest","tests/a.py"))[:3]==("/qualified/python","-m","pytest")
 try: QualifiedPythonEnvironment("e","python","q",False).direct_argv(("-m","pytest")); assert False
 except RuntimeError: pass

def test_resource_locks_make_parallelism_conservative_and_deterministic():
 a=ShardExecution("s1",("a",),("repo",)); b=ShardExecution("s2",("b",),("repo",)); c=ShardExecution("s3",("c",),("net",))
 waves=schedule_shards((a,b,c),workers=3)
 assert [[s.shard_id for s in w] for w in waves]==[["s1","s3"],["s2"]]

def test_complete_shard_aggregation_and_run_v3_attachments():
 agg=aggregate_shards(("a","b"),(ShardOutcome("s1",("a",),passed=1,junit_path="a.xml",log_path="a.log",profile_path="a.json"),ShardOutcome("s2",("b",),failed=1,junit_path="b.xml")))
 assert agg["complete"] and agg["passed"]==1 and agg["failed"]==1
 m=run_attachment_manifest("run","sel",agg); assert m["schema_version"]==3 and len(m["attachments"])==2

def test_aggregation_fails_closed_on_missing_or_duplicate_membership():
 a=aggregate_shards(("a","b"),(ShardOutcome("s1",("a","a")),))
 assert not a["complete"] and a["missing"]==["b"] and a["duplicate"]==["a"]


def test_streamed_profile_log_is_ndjson_and_round_trips(tmp_path):
 path=tmp_path/"profile.ndjson"; sample=TestPerformanceSample("a","setup",.25,"tests/a.py",tool="git",git_seconds=.1)
 append_profile_sample(path,sample); append_profile_sample(path,TestPerformanceSample("a","teardown",.1,"tests/a.py"))
 assert read_profile_samples(path)==(sample,TestPerformanceSample("a","teardown",.1,"tests/a.py"))

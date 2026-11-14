"""Current public Devtool delivery CLI behavior through its canonical parser."""
from devtool.cli import build_parser


def test_delivery_commands_are_schema_registered():
    parser = build_parser()
    for operation in ("plan", "start", "status", "resume", "report", "push", "pr", "ci"):
        argv = ["delivery", "session", operation]
        argv += (["--id", "a" * 32] if operation in ("status", "resume", "report", "push", "pr", "ci") else [])
        args = parser.parse_args(argv)
        assert args.command == "delivery" and args.delivery_command == "session" and args.delivery_session_command == operation


def test_delivery_flags_are_current_and_safe():
    args = build_parser().parse_args(["delivery", "session", "plan", "--base", "main", "--head", "feature/a"])
    assert args.base == "main" and args.head == "feature/a"


def test_existing_delivery_artifact_plan_is_preserved():
    args = build_parser().parse_args(["delivery", "plan", "sample.zip"])
    assert args.command == "delivery"
    assert args.delivery_command == "plan"
    assert args.artifact == "sample.zip"


def test_delivery_pr_and_ci_flags_preserved_in_canonical_schema():
    args=build_parser().parse_args(["delivery","session","ci","--id","a"*32,
                                    "--checks","3","--interval","2",
                                    "--require-workflow","Unit CI",
                                    "--require-workflow","Android CI"])
    assert args.checks==3 and args.interval==2.0
    assert args.require_workflow==["Unit CI","Android CI"]
    pr=build_parser().parse_args(["delivery","session","pr","--id","a"*32,"--yes"])
    assert pr.yes is True and pr.delivery_session_command=="pr"

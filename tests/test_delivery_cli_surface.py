"""Current public Devtool delivery CLI behavior through its canonical parser."""
from devtool.cli import build_parser


def test_delivery_commands_are_schema_registered():
    parser = build_parser()
    for operation in ("plan", "start", "status", "resume", "report"):
        argv = ["delivery", "session", operation]
        argv += (["--id", "a" * 32] if operation in ("status", "resume", "report") else [])
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

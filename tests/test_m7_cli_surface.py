from __future__ import annotations

from devtool.core_parser import build_canonical_parser, parser_identity_projection
from devtool.m7_credential_cli import run


def test_m7_canonical_commands_and_options_are_registered():
    parser=build_canonical_parser()
    for command in ('doctor','repair','drift','predelete','postrecreate'):
        argv=['credentials',command,'--repo','Mikeyphw/devtool','--auth','app']
        if command=='drift':argv+=['--baseline','/private/baseline.json']
        if command=='postrecreate':argv+=['--previous-repository-id','700']
        parsed=parser.parse_args(argv)
        assert parsed.command=='credentials'
        assert parsed.credential_action==command
        assert parsed.credential_repository=='Mikeyphw/devtool'
        assert parsed.credential_auth=='app'
    projection=parser_identity_projection()
    assert ('credentials','doctor') in projection['commands']
    assert (('credentials','doctor'),'--probe-read') in projection['options']


def test_m7_canonical_command_supports_readonly_probe_and_identity_options():
    args=build_canonical_parser().parse_args(['credentials','doctor',
         '--repo','Mikeyphw/devtool','--auth','app','--probe-read'])
    assert args.credential_probe_read is True
    fork=build_canonical_parser().parse_args(['credentials','doctor',
        '--repo','Mikeyphw/devtool','--head-repo','ForkOwner/devtool','--auth','app'])
    assert fork.credential_head_repository=='ForkOwner/devtool'
    assert callable(run)

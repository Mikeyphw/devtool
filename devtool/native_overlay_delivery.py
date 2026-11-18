"""Thin native source-bound apply-to-delivery continuation.

No patch, Git mutation, network dispatch, PR engine, or duplicate journal.
The native overlay transaction must already have committed and written a
qualified receipt. This entrypoint only adopts or inspects its durable session.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .delivery_session import DeliverySessionError, adopt_applied, status, resume


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    adopt = sub.add_parser('adopt-committed', help='Adopt an already native-committed, qualified overlay')
    adopt.add_argument('--repo', required=True, type=Path)
    adopt.add_argument('--overlay', required=True, type=Path)
    adopt.add_argument('--base', default='main')
    adopt.add_argument('--base-repo',default=None,help='Explicit upstream base GitHub repository')
    adopt.add_argument('--head-repo',default=None,help='Must match checked-out fork origin')
    adopt.add_argument('--base-sha',default=None,help='Optional pinned upstream base SHA')
    observe = sub.add_parser('status', help='Read-only native delivery session status')
    observe.add_argument('--id', required=True)
    observe = sub.add_parser('resume', help='Read-only source-bound session reconciliation')
    observe.add_argument('--id', required=True)
    args = parser.parse_args(argv)
    try:
        if args.operation == 'adopt-committed':
            result = adopt_applied(args.repo, args.overlay, base=args.base,
                                   base_repository=args.base_repo,
                                   head_repository=args.head_repo, base_sha=args.base_sha)
        else:
            result = {'status': status, 'resume': resume}[args.operation](args.id)
    except (DeliverySessionError, OSError, RuntimeError, ValueError) as exc:
        print('NATIVE_OVERLAY_DELIVERY_REFUSED: '+str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

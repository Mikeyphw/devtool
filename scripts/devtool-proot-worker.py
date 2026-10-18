#!/usr/bin/env python3
"""Safe, explicit RS10 service manager and Devtool worker entrypoint for Termux.

Does not install/reconfigure SSH, alter Devtool profiles, delete guest files, or
silently kill tmux/Devtool jobs. 'ensure' recreates only absent service sessions.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time

AGENT = 'devtool-proot-agent'
BRIDGE = 'devtool-proot-bridge'
MARKER = 'RS12_GUEST_READY'


class Refusal(Exception):
    pass


def run(argv: list[str], *, timeout: float = 12.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Refusal(f'Command failed: {argv[0]}: {exc}') from exc


def base_dir() -> Path:
    base = Path.home() / '.local/share/devtool/proot-bridge'
    if not base.is_dir() or base.is_symlink():
        raise Refusal(f'RS10 bridge directory unavailable/unsafe: {base}')
    return base


def settings(base: Path) -> tuple[int, Path]:
    if 'com.termux' not in os.environ.get('PREFIX', ''):
        raise Refusal('Run in native Termux, not Ubuntu PRoot or su')
    for name in ('tmux', 'ssh'):
        if not shutil.which(name):
            raise Refusal(f'Missing native Termux executable: {name}')
    for name in ('run-agent.sh', 'run-server.sh', 'host_client.py', 'known_hosts', 'sshd_config'):
        path = base / name
        if not path.is_file() or path.is_symlink():
            raise Refusal(f'RS10 bridge file absent/unsafe: {name}')
    cfg = (base / 'sshd_config').read_text(encoding='utf-8')
    settings = {}
    for line in cfg.splitlines():
        words = line.strip().split(None, 1)
        if len(words) == 2 and not words[0].startswith('#'):
            settings[words[0].lower()] = words[1].strip()
    if settings.get('listenaddress') != '127.0.0.1':
        raise Refusal('Bridge must be localhost only; refusing other SSH listener')
    if settings.get('authenticationmethods', '').lower() != 'publickey':
        raise Refusal('Bridge must use publickey authentication')
    port = settings.get('port', '')
    if not port.isdecimal() or not 1024 <= int(port) <= 65535:
        raise Refusal(f'Unexpected SSH port: {port!r}')
    key = Path.home() / '.ssh/devtool_proot_ed25519'
    if not key.is_file() or key.is_symlink():
        raise Refusal('Missing or unsafe pinned RS10 SSH identity key')
    return int(port), key


def active(name: str) -> bool:
    return run(['tmux', 'has-session', '-t', '=' + name], timeout=4).returncode == 0


def ssh_argv(base: Path, port: int, key: Path) -> list[str]:
    user = run(['id', '-un'], timeout=3)
    if user.returncode or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', user.stdout.strip()):
        raise Refusal('Unable to determine safe Termux username')
    return [
        'ssh', '-F', '/dev/null', '-p', str(port), '-i', str(key),
        '-o', f'UserKnownHostsFile={base / "known_hosts"}',
        '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=3',
        user.stdout.strip() + '@127.0.0.1',
    ]


def status(base: Path, port: int, key: Path) -> dict:
    services = {AGENT: active(AGENT), BRIDGE: active(BRIDGE)}
    listener = False
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=.5):
            listener = True
    except OSError:
        pass
    healthy = False
    error = ''
    guest_sessions: list[str] | None = None
    if listener:
        p = run([*ssh_argv(base, port, key),
                 "printf 'RS12_GUEST_READY\\n'; tmux list-sessions -F '#{session_name}' 2>/dev/null || true"], timeout=9)
        if p.returncode == 0 and p.stdout.startswith(MARKER + '\n'):
            healthy = True
            guest_sessions = [s.strip() for s in p.stdout.splitlines()[1:] if s.strip()]
        else:
            error = (p.stderr or p.stdout or f'ssh exited {p.returncode}').strip()[-400:]
    elif services[AGENT] or services[BRIDGE]:
        error = 'Service session exists but SSH listener is unavailable'
    return {
        'ok': bool(healthy and all(services.values())),
        'healthy_ssh': healthy,
        'listener': listener,
        'port': port,
        'sessions': services,
        'guest_tmux_sessions': guest_sessions,
        'error': error,
    }


def start(base: Path, port: int, key: Path) -> dict:
    before = status(base, port, key)
    if before['ok']:
        return before
    started = []
    for name, command in ((AGENT, 'run-agent.sh'), (BRIDGE, 'run-server.sh')):
        if active(name):
            continue
        p = run(['tmux', 'new-session', '-d', '-s', name, str(base / command)], timeout=10)
        if p.returncode != 0:
            raise Refusal(f'Could not start {name}: {(p.stderr or p.stdout).strip()}')
        started.append(name)
        if name == AGENT:
            # Agent must be resident before sshd begins accepting commands.
            sock = base / 'ipc/agent.sock'
            for _ in range(40):
                if sock.is_socket():
                    break
                if not active(name):
                    break
                time.sleep(.1)
    for _ in range(16):
        current = status(base, port, key)
        if current['ok']:
            current['started'] = started
            return current
        if not all(current['sessions'].values()):
            break
        time.sleep(.25)
    raise Refusal('RS10 services did not become healthy; existing sessions preserved. '
                  'Inspect: tmux capture-pane -pt devtool-proot-agent -S -80; '
                  'tmux capture-pane -pt devtool-proot-bridge -S -80')


def stop(base: Path, port: int, key: Path, *, confirmed: bool, force: bool) -> dict:
    if not confirmed:
        raise Refusal('Stopping resident services requires --yes')
    before = status(base, port, key)
    if before['guest_tmux_sessions'] is None and any(before['sessions'].values()) and not force:
        raise Refusal('Cannot inspect guest jobs; refusing shutdown. Diagnose bridge or explicitly use --yes --force.')
    if before['guest_tmux_sessions'] and not force:
        names = ', '.join(before['guest_tmux_sessions'][:8])
        raise Refusal(f'Guest tmux sessions may contain active work: {names}; refusing shutdown')
    stopped = []
    for name in (BRIDGE, AGENT):
        if active(name):
            p = run(['tmux', 'kill-session', '-t', '=' + name], timeout=6)
            if p.returncode != 0:
                raise Refusal(f'Could not stop {name}: {p.stderr.strip()}')
            stopped.append(name)
    return {'ok': True, 'stopped': stopped, 'forced': force}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('status', 'start', 'ensure', 'stop', 'restart', 'exec'))
    p.add_argument('arguments', nargs=argparse.REMAINDER, help='For exec: -- command [args...]')
    p.add_argument('--yes', action='store_true', help='Explicitly approve stop/restart')
    p.add_argument('--force', action='store_true', help='DANGEROUS: stop even if guest jobs cannot be ruled out')
    p.add_argument('--json', action='store_true')
    a = p.parse_args(argv)
    # argparse.REMAINDER preserves 'exec -- ...' literally. For service actions
    # also accept natural flag position: 'stop --yes' / 'status --json'.
    if a.action != 'exec':
        permitted = {'--yes', '--force', '--json'}
        unexpected = [x for x in a.arguments if x not in permitted]
        if unexpected:
            p.error('unexpected arguments: ' + ' '.join(unexpected))
        a.yes = a.yes or '--yes' in a.arguments
        a.force = a.force or '--force' in a.arguments
        a.json = a.json or '--json' in a.arguments
    try:
        base = base_dir()
        port, key = settings(base)
        if a.action == 'status':
            result = status(base, port, key)
        elif a.action in ('start', 'ensure'):
            result = start(base, port, key)
        elif a.action == 'stop':
            result = stop(base, port, key, confirmed=a.yes, force=a.force)
        elif a.action == 'restart':
            stop(base, port, key, confirmed=a.yes, force=a.force)
            result = start(base, port, key)
        else:
            start(base, port, key)
            arguments = a.arguments
            if arguments and arguments[0] == '--':
                arguments = arguments[1:]
            if not arguments:
                raise Refusal('exec requires -- command [arguments...]')
            cmd = ['devtool', '-r', os.getcwd(), 'ssh', 'exec', 'proot-worker', '--', *arguments]
            return subprocess.call(cmd)
        if a.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print('OK' if result.get('ok') else 'NOT_HEALTHY', json.dumps(result, sort_keys=True))
        return 0 if result.get('ok') else 2
    except Refusal as exc:
        if a.json:
            print(json.dumps({'ok': False, 'error': str(exc)}))
        else:
            print('REFUSED:', exc, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

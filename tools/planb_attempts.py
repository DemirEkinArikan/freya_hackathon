#!/usr/bin/env python3
"""Plan B final window: parallel kit-builder attempts per brief; the best self-test is exported.

    <planb venv>/bin/python tools/planb_attempts.py --brief <brief.md> <tool-api.json> [--brief ...] [--attempts 2]

Each attempt is one planb/agent_builder.py run in its own working copy, <work-dir>/<run>/<brief>-a<k>/kit, outside
this repository, so no instruction file of the repository reaches the builder. Attempts start --stagger seconds apart
and each one is killed, with everything it started, after --timeout seconds. A team has one Freya workspace, so
every attempt self-tests there and their simulations take turns through the shared lease (planb/freya.py holds
FREYA_LEASE), at most 10 calls at once. Attempt k of a brief reuses its own workflow from earlier runs
(<work-dir>/freya-agents.json), so workflows are updated in place. Per brief, the attempt with a saved workflow and
the highest final self-test pass rate (the last line of its results/log.jsonl), then the shortest elapsed time, is
exported with freya.export_workflow to <out-dir>/<brief>.workflow.json, and its trace is copied next to it with
every key redacted. <out-dir>/planb-summary.json lists every attempt. Exit 3 when a brief has no export. Keys reach
the builders only through their environment and are never printed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from builder.config import read_local_environment

PLANB = ROOT / 'planb'
# State a kit run writes; a working copy starts without it.
RUN_STATE = shutil.ignore_patterns('traces', 'out', 'results', 'builders', 'briefs', '__pycache__', '.freya-agents.json', '.personas.json')
EXPORT = 'import freya, sys; freya.export_workflow(sys.argv[1], sys.argv[2])'


def _main_checkout():
    """The main checkout that every worktree shares (the private .env and the simulation lease)."""
    try:
        common = subprocess.run(['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'], cwd=ROOT,
                                capture_output=True, text=True).stdout.strip()
    except OSError:
        common = ''
    return Path(common).parent if common else ROOT


def _json(text):
    try:
        return json.loads(text)
    except ValueError:
        return None


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='milliseconds')


def _environment(args):
    """The builders' environment and the secrets in it. No Claude session, login or second workspace key leaks in."""
    freya = {**read_local_environment(args.env_file), **{k: v for k, v in os.environ.items() if k.startswith('FREYA_')}}
    claude = args.anthropic_key_file.read_text().strip() if args.anthropic_key_file.is_file() else os.environ.get('ANTHROPIC_API_KEY', '')
    if not claude or not freya.get('FREYA_API_KEY') or not freya.get('FREYA_BANK_TEAM_KEY'):
        raise ValueError('needs the Claude key file (or ANTHROPIC_API_KEY), FREYA_API_KEY and FREYA_BANK_TEAM_KEY')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('CLAUDE', 'ANTHROPIC', 'OPENAI', 'FREYA'))}
    env.update({k: v for k, v in freya.items() if k != 'FREYA_API_KEY_2'})   # one workspace per team
    env.update({'ANTHROPIC_API_KEY': claude, 'FREYA_SIM_CONCURRENCY': '10', 'FREYA_LEASE': str(args.lease),
                'PATH': f'{Path(sys.executable).parent}{os.pathsep}{os.environ.get("PATH", "")}'})   # `python3` = this venv
    secrets = {v for k, v in {**freya, 'ANTHROPIC_API_KEY': claude}.items() if any(s in k for s in ('KEY', 'TOKEN', 'SECRET', 'AUTH')) and len(v) >= 8}
    return env, sorted(secrets, key=len, reverse=True)


def _redact(text, secrets):
    count = sum(text.count(secret) for secret in secrets)
    for secret in secrets:
        text = text.replace(secret, '[REDACTED]')
    return text, count


def _start(attempt, env, slots):
    home, kit = attempt['dir'], attempt['dir'] / 'kit'
    shutil.copytree(PLANB, kit, ignore=RUN_STATE)
    (kit / 'briefs').mkdir()
    inputs = []
    for path in attempt['inputs']:
        (shutil.copytree if path.is_dir() else shutil.copy2)(path, kit / 'briefs' / path.name)
        inputs.append(f'briefs/{path.name}')
    if slots:   # this attempt's workflow from earlier runs: the builder updates it instead of creating one
        (kit / '.freya-agents.json').write_text(json.dumps(slots, indent=1))
    (home / 'claude-config').mkdir()   # an empty Claude config per attempt: the API key, never this machine's login
    with open(home / 'builder.log', 'wb') as log:
        attempt['process'] = subprocess.Popen([sys.executable, 'agent_builder.py', *inputs], cwd=kit,
                                              env={**env, 'CLAUDE_CONFIG_DIR': str(home / 'claude-config')},
                                              stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    attempt['started'], attempt['started_at'] = time.monotonic(), _now()
    print(json.dumps({'brief': attempt['label'], 'attempt': attempt['k'], 'pid': attempt['process'].pid,
                      'started_at': attempt['started_at'], 'kit': str(kit)}), flush=True)


def _kill(process):
    """SIGKILL the builder, its process group and every descendant, including one that started its own session."""
    table = subprocess.run(['ps', '-A', '-o', 'pid=', '-o', 'ppid='], capture_output=True, text=True).stdout.split()
    children = {}
    for pid, parent in zip(table[::2], table[1::2]):
        children.setdefault(int(parent), []).append(int(pid))
    tree, stack = [], [process.pid]
    while stack:
        tree.append(stack.pop())
        stack.extend(children.get(tree[-1], []))
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass
    for pid in tree:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return process.wait()


def _finish(attempt, code, timed_out, registry):
    kit = attempt['dir'] / 'kit'
    slots = _json((kit / '.freya-agents.json').read_text()) if (kit / '.freya-agents.json').is_file() else None
    if isinstance(slots, dict) and slots:
        registry.setdefault(attempt['label'], {})[f'a{attempt["k"]}'] = slots
    files = sorted(kit.glob('out/*.json'), key=lambda path: (path.name.endswith('.export.json'), path.stat().st_mtime))
    ids = _json(files[-1].read_text()) if files else None
    if not (isinstance(ids, dict) and ids.get('workflow_id')):
        ids = list(slots.values())[-1] if isinstance(slots, dict) and slots else {}
    log = kit / 'results' / 'log.jsonl'
    runs = [row for row in map(_json, log.read_text().splitlines() if log.is_file() else []) if isinstance(row, dict)]
    last = runs[-1] if runs else {}
    passed, total = last.get('passed'), last.get('total')
    traces = sorted(kit.glob('traces/*.jsonl'))
    attempt['result'] = {
        'attempt': attempt['k'], 'pid': attempt['process'].pid, 'started_at': attempt['started_at'], 'exit': code,
        'timed_out': timed_out, 'elapsed_s': round(time.monotonic() - attempt['started'], 1),
        'self_test': {key: last.get(key) for key in ('passed', 'total', 'agent_id', 'at')} if last else None,
        'pass_rate': passed / total if isinstance(passed, (int, float)) and isinstance(total, (int, float)) and total > 0 else None,
        'workflow_file': str(files[-1]) if files else None, 'workflow_id': ids.get('workflow_id'), 'agent_id': ids.get('agent_id'),
        'trace': str(traces[-1]) if traces else None, 'kit': str(kit), 'log': str(attempt['dir'] / 'builder.log')}
    print(json.dumps({'brief': attempt['label'], **{key: attempt['result'][key] for key in
                      ('attempt', 'exit', 'timed_out', 'elapsed_s', 'pass_rate', 'workflow_id')}}), flush=True)


def _select(results):
    """Highest final self-test pass rate, then the shortest elapsed time. Only an attempt that saved a workflow counts."""
    ready = [result for result in results if result['workflow_id']]
    return min(ready, key=lambda result: (-(result['pass_rate'] if result['pass_rate'] is not None else -1), result['elapsed_s']),
               default=None)


def _export(best, label, out, env, secrets):
    out.mkdir(parents=True, exist_ok=True)
    workflow = out / f'{label}.workflow.json'
    done = subprocess.run([sys.executable, '-c', EXPORT, best['workflow_id'], str(workflow)], cwd=best['kit'],
                          env=env, capture_output=True, text=True, timeout=300)
    if done.returncode:
        return {'export_error': _redact(done.stderr[-600:], secrets)[0]}
    record, redacted = {'workflow': str(workflow)}, 0
    trace = Path(best['trace']) if best['trace'] else None
    for source, key, name in ((trace, 'trace', '.trace.jsonl'), (trace and trace.with_suffix('.txt'), 'trace_notes', '.trace.txt')):
        if source and source.is_file():
            text, count = _redact(source.read_text(encoding='utf-8', errors='replace'), secrets)
            (out / f'{label}{name}').write_text(text, encoding='utf-8')
            record[key], redacted = str(out / f'{label}{name}'), redacted + count
    record['trace_redactions'] = redacted
    return record


def main(argv=None):
    checkout = _main_checkout()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--brief', nargs='+', action='append', required=True, type=Path, metavar='PATH',
                        help="one brief: its Markdown file(s) and its tool API .json (or a folder); repeat per brief")
    parser.add_argument('--attempts', type=int, default=2, help='parallel builder attempts per brief')
    parser.add_argument('--stagger', type=float, default=60, help='seconds between consecutive starts')
    parser.add_argument('--timeout', type=float, default=1800, help='hard wall clock per attempt, in seconds')
    parser.add_argument('--work-dir', type=Path, default=Path(tempfile.gettempdir()) / 'planb-attempts')
    parser.add_argument('--out-dir', type=Path, default=ROOT / 'out')
    parser.add_argument('--env-file', type=Path, default=checkout / '.env', help="private .env, read as data only")
    parser.add_argument('--lease', type=Path, default=checkout / 'artifacts' / '.simulation.lock',
                        help="the workspace's shared simulation lease")
    parser.add_argument('--anthropic-key-file', type=Path, default=Path.home() / '.freya-anthropic-key')
    args = parser.parse_args(argv)
    try:
        briefs = {}
        for paths in args.brief:
            label = paths[0].stem if paths[0].is_file() else paths[0].name
            if label in briefs or not all(path.exists() for path in paths) or args.attempts < 1:
                raise ValueError(f'{label}: needs existing input paths, a label of its own and --attempts of at least 1')
            briefs[label] = [path.resolve() for path in paths]
        env, secrets = _environment(args)
        args.lease.parent.mkdir(parents=True, exist_ok=True)
        run = args.work_dir / dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        run.mkdir(parents=True)
        git = lambda *command: subprocess.run(['git', *command], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        commit = git('rev-parse', 'HEAD') or None   # the builder that every attempt copies
        source = {'source_commit': commit, 'source_clean': commit and not git('status', '--porcelain', '--', 'planb', 'tools/planb_attempts.py')}
        registry_file = args.work_dir / 'freya-agents.json'
        registry = _json(registry_file.read_text()) if registry_file.is_file() else None
        registry = registry if isinstance(registry, dict) else {}
        # The briefs take turns, so every brief has a running attempt as early as possible.
        pending = [{'label': label, 'k': k, 'inputs': paths, 'dir': run / f'{label}-a{k}'}
                   for k in range(1, args.attempts + 1) for label, paths in briefs.items()]
        attempts, running, next_start = list(pending), [], time.monotonic()
        while pending or running:
            if pending and time.monotonic() >= next_start:
                attempt = pending.pop(0)
                attempt['dir'].mkdir()
                _start(attempt, env, registry.get(attempt['label'], {}).get(f'a{attempt["k"]}'))
                running.append(attempt)
                next_start = time.monotonic() + args.stagger
            for attempt in list(running):
                code, timed_out = attempt['process'].poll(), False
                if code is None and time.monotonic() - attempt['started'] >= args.timeout:
                    code, timed_out = _kill(attempt['process']), True
                if code is not None:
                    running.remove(attempt)
                    _finish(attempt, code, timed_out, registry)
                    registry_file.write_text(json.dumps(registry, indent=1))
            time.sleep(0.2)
        summary = {'run': str(run), 'started': attempts[0]['started_at'], 'ended': _now(), **source,
                   'attempts_per_brief': args.attempts, 'stagger_s': args.stagger, 'timeout_s': args.timeout, 'briefs': {}}
        for label, paths in briefs.items():
            results = [attempt['result'] for attempt in attempts if attempt['label'] == label]
            best = _select(results)
            summary['briefs'][label] = {'inputs': [str(path) for path in paths], 'selected': best['attempt'] if best else None,
                                        **(_export(best, label, args.out_dir, env, secrets) if best else {}), 'attempts': results}
        text = json.dumps(summary, indent=2) + '\n'
        args.out_dir.mkdir(parents=True, exist_ok=True)
        for path in (run / 'summary.json', args.out_dir / 'planb-summary.json'):
            path.write_text(text)
        print(text, end='')
        return 0 if all('workflow' in record for record in summary['briefs'].values()) else 3
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

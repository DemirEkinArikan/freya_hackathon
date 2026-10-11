#!/usr/bin/env python3
"""T18 final window: parallel builder attempts per hidden brief, declared-order selection.

launch    Start N new attempts per label at once from this frozen checkout. Each is one
          unattended builder/build.py run with its own output directory and new slot,
          inside the builder's own 1,800-second deadline.
select    Rank every attempt of a label by package_submission.ATTEMPT_ORDER and write
          artifacts/final/<label>/selection.json. Exit 2 while an attempt still runs,
          3 when no attempt is eligible. Attempt directories are never changed.
manifest  Write artifacts/submission/manifest.json from both selections.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from builder.config import load_local_environment
from tools import package_submission as package
from tools import verify_submission_bundle as verifier

FINAL = 'artifacts/final'
LABELS = ('hidden-1', 'hidden-2')
DIAGNOSTIC_EVENTS = {'policy_validation', 'validation', 'policy_format_validation', 'workflow_format_validation'}


def _spec():
    return json.loads((ROOT / 'artifacts/hidden-inputs/spec.json').read_text(encoding='utf-8'))


def _attempts(label):
    found = {int(path.name[8:]): path for path in (ROOT / FINAL / label).glob('attempt-*') if path.name[8:].isdigit()}
    return [found[number] for number in sorted(found)]


def _read(path, parse):
    try:
        return parse(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _trace(text):
    return [package.strict_json(line) for line in text.splitlines() if line.strip()]


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def _state(directory):
    """The attempt's one run directory, manifest, trace and whether its builder still runs."""
    runs = [path for path in directory.iterdir() if path.is_dir() and (path / 'trace.jsonl').is_file()]
    if len(runs) > 1:
        raise ValueError(f'{directory.name} holds {len(runs)} runs; an attempt is exactly one builder run')
    run_dir = runs[0] if runs else None
    events = (_read(run_dir / 'trace.jsonl', _trace) or []) if run_dir else []
    run = _read(run_dir / 'manifest.json', package.strict_json) if run_dir else None
    ended = bool(events) and package._event_name(events[-1]) == 'end'
    # A successful builder writes build-provenance.json after its end event; a failed one never does.
    finished = isinstance(run, dict) and ended and (run.get('status') == 'failed' or (run_dir / 'build-provenance.json').is_file())
    pid = (_read(directory / 'launch.json', json.loads) or {}).get('pid')
    running = not finished and type(pid) is int and _alive(pid)
    return run_dir, run if isinstance(run, dict) else None, events, ended, running


def evaluate(directory, inputs):
    """One attempt's declared criteria from its own artifacts; nothing is written."""
    run_dir, run, events, ended, running = _state(directory)
    policy = _read(run_dir / 'policy.json', package.strict_json) if run_dir else None
    declared = policy.get('unsupported_constraints') if isinstance(policy, dict) else None
    entry = {'attempt': int(directory.name[8:]), 'run': run_dir.relative_to(ROOT).as_posix() if run_dir else None,
             'status': 'running' if running else run.get('status') if run else 'no_manifest',
             'exported': False, 'local_validation': False, 'package_validation': False,
             'diagnostics': sum(len(event['errors']) if isinstance(event.get('errors'), list) and event['errors'] else 1
                                for event in events
                                if package._event_name(event) in DIAGNOSTIC_EVENTS and event.get('passed') is False)
                            + (len(declared) if isinstance(declared, list) else 0),
             'finished_at': events[-1].get('time') if ended else None}
    for key, name in (('manifest', 'manifest.json'), ('trace', 'trace.jsonl'), ('export', 'submission.workflow.json')):
        path = run_dir / name if run_dir else None
        entry[key + '_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest() if path and path.is_file() else None
    notes = [str(run['error'])[:300]] if run and run.get('error') else []
    if running or not run or run.get('status') != 'exported' or not entry['export_sha256']:
        return entry, notes
    entry['exported'] = True
    export = _read(run_dir / 'submission.workflow.json', package.strict_json)
    briefs = [(Path(path).name, (ROOT / path).read_text(encoding='utf-8')) for path in inputs['briefs']]
    api = package.strict_json((ROOT / inputs['tool_api']).read_text(encoding='utf-8'))
    checks = {'local_validation': lambda: verifier.check_recompilation(run_dir, run, export, briefs, api),
              'package_validation': lambda: (package.validate_trace(events, export),
                                             package.validate_run(run_dir / 'manifest.json', run, export, events, inputs))}
    for key, check in checks.items():
        try:
            check()
            entry[key] = True
        except Exception as exc:  # Any failure only makes this attempt ineligible.
            notes.append(f'{key}: {type(exc).__name__}: {str(exc)[:300]}')
    return entry, notes


def launch(args):
    if sys.version_info[:2] != (3, 11):
        raise ValueError('Run the launcher with Python 3.11; every builder inherits its interpreter')
    if args.attempts < 1:
        raise ValueError('--attempts must be at least 1')
    spec, env = _spec(), dict(os.environ)
    for label in args.labels:
        if not all((ROOT / path).is_file() for path in [*spec[label]['briefs'], spec[label]['tool_api']]):
            raise ValueError(f'{label}: an input file in spec.json is missing')
    load_local_environment(args.env_file, env)
    env.pop('OPENAI_API_KEY', None)
    if not env.get('FREYA_API_KEY', '').strip():
        raise ValueError('FREYA_API_KEY is not available to the builders')
    if shutil.which('codex', path=env.get('PATH')) is None:
        raise ValueError('The Codex CLI is not on PATH')
    running = sum(_state(directory)[4] for label in LABELS for directory in _attempts(label))
    requested = args.attempts * len(args.labels)
    if running + requested > args.max_running:
        raise ValueError(f'{running} builders are running; {requested} more would exceed --max-running {args.max_running}')
    for label in args.labels:
        inputs = spec[label]
        for _ in range(args.attempts):
            existing = _attempts(label)
            number = int(existing[-1].name[8:]) + 1 if existing else 1
            relative = f'{FINAL}/{label}/attempt-{number}'
            (ROOT / relative).mkdir(parents=True)  # Never reuses an attempt directory.
            started = dt.datetime.now(dt.timezone.utc)
            argv = [sys.executable, 'builder/build.py', *(item for brief in inputs['briefs'] for item in ('--brief', brief)),
                    '--tool-api', inputs['tool_api'], '--output-dir', relative,
                    '--slot', f'final-{label}-a{number}-{started:%Y%m%dT%H%M%SZ}',
                    '--simulation-lease', str(args.simulation_lease),
                    '--model', args.model, '--reasoning-effort', args.reasoning_effort]
            with open(ROOT / relative / 'builder.log', 'wb') as log:
                process = subprocess.Popen(argv, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                           stderr=subprocess.STDOUT, start_new_session=True)
            record = {'label': label, 'attempt': number, 'pid': process.pid, 'started_at': started.isoformat(), 'argv': argv}
            (ROOT / relative / 'launch.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
            print(json.dumps({'label': label, 'attempt': number, 'pid': process.pid, 'output_dir': relative}), flush=True)
    return 0


def select(args):
    spec, waiting, missing = _spec(), False, False
    for label in args.labels:
        directories = _attempts(label)
        numbers = [int(path.name[8:]) for path in directories]
        if not numbers or numbers != list(range(1, len(numbers) + 1)):
            raise ValueError(f'{label}: attempts must be attempt-1..attempt-N with none missing; found {numbers}')
        entries = []
        for directory in directories:
            entry, notes = evaluate(directory, spec[label])
            entries.append(entry)
            print(json.dumps({'label': label, **{key: entry[key] for key in ('attempt', 'status', *package.ATTEMPT_ORDER)},
                              'notes': notes}))
        if any(entry['status'] == 'running' for entry in entries):
            print(json.dumps({'label': label, 'selection': 'waiting for running attempts'}))
            waiting = True
            continue
        best = min(entries, key=package.attempt_rank)
        eligible = all(best[key] for key in package.ATTEMPT_ORDER[:3])
        record = {'version': 1, 'label': label, 'order': list(package.ATTEMPT_ORDER),
                  'selected': best['attempt'] if eligible else None, 'attempt_count': len(entries), 'attempts': entries}
        (ROOT / FINAL / label / 'selection.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'label': label, 'selected': record['selected'], 'of': len(entries),
                          'run': best['run'] if eligible else None}))
        missing = missing or not eligible
    return 2 if waiting else 3 if missing else 0


def manifest(args):
    spec, runs, commands, selections, chosen = _spec(), {}, [], {}, {}
    for label in LABELS:
        selections[label] = f'{FINAL}/{label}/selection.json'
        record = json.loads((ROOT / selections[label]).read_text(encoding='utf-8'))
        if type(record.get('selected')) is not int:
            raise ValueError(f'{label}: no eligible selected attempt')
        entry = record['attempts'][record['selected'] - 1]
        started = json.loads((ROOT / FINAL / label / f'attempt-{entry["attempt"]}' / 'launch.json').read_text(encoding='utf-8'))
        runs[label] = entry['run']
        commands.append(shlex.join(started['argv']))
        chosen[label] = f'attempt {entry["attempt"]} of {record["attempt_count"]}'
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    value = {'team': args.team, 'repo': package.repository_url(), 'commit': head, 'run_command': '\n'.join(commands),
             'workflows': {label: f'{run}/submission.workflow.json' for label, run in runs.items()},
             'traces': {label: f'{run}/trace.jsonl' for label, run in runs.items()},
             'runs': {label: f'{run}/manifest.json' for label, run in runs.items()},
             'inputs': {label: spec[label] for label in LABELS}, 'selections': selections}
    output = ROOT / 'artifacts/submission/manifest.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'commit': head, 'selected': chosen, 'runs': runs}, indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    start = commands.add_parser('launch')
    start.add_argument('--env-file', type=Path, required=True, help="Private .env, read by the data-only parser")
    start.add_argument('--model', required=True)
    start.add_argument('--reasoning-effort', required=True, choices=('low', 'medium', 'high', 'xhigh'))
    start.add_argument('--simulation-lease', type=Path, required=True)
    start.add_argument('--attempts', type=int, default=3, help='New attempts per label')
    start.add_argument('--max-running', type=int, default=6, help='Machine cap on concurrently running builders')
    start.add_argument('--labels', nargs='+', choices=LABELS, default=list(LABELS))
    pick = commands.add_parser('select')
    pick.add_argument('--labels', nargs='+', choices=LABELS, default=list(LABELS))
    commands.add_parser('manifest').add_argument('--team', required=True)
    args = parser.parse_args(argv)
    try:
        return {'launch': launch, 'select': select, 'manifest': manifest}[args.command](args)
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

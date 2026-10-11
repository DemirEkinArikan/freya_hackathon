#!/usr/bin/env python3
"""T18 final window: parallel builder attempts per hidden brief, declared-order selection.

launch    Start one round of the attempt matrix per label from this frozen checkout,
          interleaved and --stagger-seconds apart. Every attempt runs Claude
          (--provider claude --model claude-opus-5-5) with ANTHROPIC_API_KEY from
          ~/.freya-anthropic-key. Round 1 is two strict attempts; round 2, only where
          round 1 left no selected attempt, is two relaxed attempts (the builder's relaxed
          declaration option). The old Codex cells (high + medium) run only with
          --providers claude codex, spread over the logged-in Codex accounts. Each attempt
          is one unattended builder/build.py run with its own output directory and new
          slot, inside the builder's own 1,800-second deadline. A staging health probe
          before each start is recorded.
select    Rank every attempt of a label by package_submission.ATTEMPT_ORDER and write
          artifacts/final/<label>/selection.json. Exit 2 while an attempt still runs,
          3 when no attempt is eligible. Attempt directories are never changed.
manifest  Write artifacts/submission/manifest.json from both selections.
health    Append one read-only staging probe to artifacts/final/health.jsonl.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from builder.build import parser as builder_parser
from builder.config import load_local_environment
from builder.platform import FreyaClient
from builder.runtime import Deadline
from tools import package_submission as package
from tools import verify_submission_bundle as verifier

FINAL = 'artifacts/final'
LABELS = ('hidden-1', 'hidden-2')
DIAGNOSTIC_EVENTS = {'policy_validation', 'validation', 'policy_format_validation', 'workflow_format_validation'}
# Tonight's attempt matrix per brief: (provider, declaration mode, reasoning effort) for each round.
# Claude everywhere (founder, 18:05 Istanbul): the Codex cells run only with --providers ... codex.
# high is the strongest Claude effort that fits 30 minutes: the first policy call on public A+B took 353 s
# at high and 808 s at xhigh, against the builder's 900 s policy cap; max returned no policy (issue 19).
ROUNDS = {1: (('claude', 'strict', 'high'), ('claude', 'strict', 'high'), ('codex', 'strict', 'high'), ('codex', 'strict', 'medium')),
          2: (('claude', 'relaxed', 'high'), ('claude', 'relaxed', 'high'), ('codex', 'relaxed', 'high'), ('codex', 'relaxed', 'medium'))}


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


def _probe(env):
    """One read-only staging request, appended to health.jsonl. Evidence only, never a launch gate."""
    started, record = time.monotonic(), {'time': dt.datetime.now(dt.timezone.utc).isoformat(), 'ok': True}
    try:
        FreyaClient(api_key=env.get('FREYA_API_KEY', ''), deadline=Deadline(10)).request('GET', '/api/v2/workspaces')
    except Exception as exc:  # Keep only the status: platform errors can echo a response body.
        status = re.search(r'HTTP (\d{3})', str(exc))
        record.update(ok=False, error=f'HTTP {status[1]}' if status else 'transport' if 'transport' in str(exc) else type(exc).__name__)
    record['seconds'] = round(time.monotonic() - started, 2)
    (ROOT / FINAL).mkdir(parents=True, exist_ok=True)
    with open(ROOT / FINAL / 'health.jsonl', 'a', encoding='utf-8') as log:
        log.write(json.dumps(record) + '\n')
    return record


def _env(env_file):
    env = dict(os.environ)
    load_local_environment(env_file, env)
    env.pop('OPENAI_API_KEY', None)
    return env


def _anthropic_key(env):
    """The Claude API key: ~/.freya-anthropic-key, else ANTHROPIC_API_KEY. Never printed or recorded."""
    path = Path.home() / '.freya-anthropic-key'
    return (path.read_text(encoding='utf-8').strip() if path.is_file() else '') or env.get('ANTHROPIC_API_KEY', '').strip()


def _codex_builds():
    """Builder processes on this machine: every command line that names builder/build.py."""
    return len(subprocess.run(['pgrep', '-f', 'builder/build.py'], capture_output=True, text=True).stdout.split())


def _codex_accounts():
    """Logged-in Codex accounts: ~/.codex and every ~/.codex-acct<N> with an auth.json and no DISABLED marker."""
    home = Path.home()
    return [path for path in (home / '.codex', *sorted(home.glob('.codex-acct*')))
            if (path / 'auth.json').is_file() and not (path / 'DISABLED').exists()]


def _relaxed_option():
    """The frozen builder's relaxed declaration option (--declarations relaxed) as argv, or None while it has none."""
    for action in builder_parser()._actions:
        if action.choices and 'relaxed' in action.choices:
            return [action.option_strings[0], 'relaxed']
    return None


def _admit(round_number, label):
    """A round starts only where it can still improve the brief's recorded selection."""
    attempts = _attempts(label)
    if not attempts:
        if round_number == 2:
            raise ValueError(f'{label}: round 2 runs only after round 1')
        return
    record = _read(ROOT / FINAL / label / 'selection.json', json.loads) or {}
    if record.get('attempt_count') != len(attempts) or any(_state(directory)[4] for directory in attempts):
        raise ValueError(f'{label}: run select once every attempt has finished, then start another round')
    chosen = record['attempts'][record['selected'] - 1] if type(record.get('selected')) is int else None
    # Every model-plan export ranks before a deterministic fallback export, so any round can still improve one.
    if chosen and not chosen.get('deterministic_fallback') and (round_number == 2 or chosen.get('mode') == 'strict'):
        raise ValueError(f'{label}: attempt {chosen["attempt"]} ({chosen.get("mode")}) is selected; '
                         f'round {round_number} cannot improve it')


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
    launched = _read(directory / 'launch.json', json.loads) or {}
    entry = {'attempt': int(directory.name[8:]), 'provider': launched.get('provider'), 'mode': launched.get('mode'),
             'reasoning_effort': launched.get('reasoning_effort'),
             'run': run_dir.relative_to(ROOT).as_posix() if run_dir else None,
             'status': 'running' if running else run.get('status') if run else 'no_manifest',
             'exported': False, 'local_validation': False, 'package_validation': False,
             'deterministic_fallback': bool(run and run.get('plan_origin') == 'deterministic_fallback'),
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
    spec = _spec()
    for label in args.labels:
        if not all((ROOT / path).is_file() for path in [*spec[label]['briefs'], spec[label]['tool_api']]):
            raise ValueError(f'{label}: an input file in spec.json is missing')
        _admit(args.round, label)
    relaxed = _relaxed_option() if args.round == 2 else []
    if relaxed is None:
        raise ValueError('This builder has no relaxed declaration option, so round 2 cannot run from this checkout')
    cells = [cell for cell in ROUNDS[args.round] if cell[0] in args.providers]
    choices = {action.dest: action.choices or () for action in builder_parser()._actions}
    if any(provider not in choices.get('provider', ()) or effort not in choices['reasoning_effort'] for provider, _, effort in cells):
        raise ValueError('This builder cannot run every attempt of the round (--provider or effort missing); '
                         'freeze a commit with the Claude generator')
    env = _env(args.env_file)
    if not env.get('FREYA_API_KEY', '').strip():
        raise ValueError('FREYA_API_KEY is not available to the builders')
    key = _anthropic_key(env) if 'claude' in args.providers else ''
    if 'claude' in args.providers and (not key or importlib.util.find_spec('anthropic') is None):
        raise ValueError('Claude attempts need ~/.freya-anthropic-key (or ANTHROPIC_API_KEY) and the anthropic package')
    if 'codex' in args.providers and shutil.which('codex', path=env.get('PATH')) is None:
        raise ValueError('The Codex CLI is not on PATH')
    running = sum(_state(directory)[4] for label in LABELS for directory in _attempts(label))
    requested = len(cells) * len(args.labels)
    if running + requested > args.max_running:
        raise ValueError(f'{running} builders are running; {requested} more would exceed --max-running {args.max_running}')
    accounts = _codex_accounts()
    # Interleaved and spread, so one platform blip cannot reach every attempt at the same moment.
    for index, ((provider, mode, effort), label) in enumerate((cell, label) for cell in cells for label in args.labels):
        if index:
            time.sleep(args.stagger_seconds)
        inputs, existing = spec[label], _attempts(label)
        number = int(existing[-1].name[8:]) + 1 if existing else 1
        # A brief's Codex attempts of one round use different accounts, so one account's outage cannot fail the brief.
        account = accounts[(number - 1 + LABELS.index(label)) % len(accounts)] if provider == 'codex' and accounts else None
        relative = f'{FINAL}/{label}/attempt-{number}'
        (ROOT / relative).mkdir(parents=True)  # Never reuses an attempt directory.
        health = _probe(env)
        started = dt.datetime.now(dt.timezone.utc)
        model = args.model if provider == 'claude' else args.codex_model
        argv = [sys.executable, 'builder/build.py', *(item for brief in inputs['briefs'] for item in ('--brief', brief)),
                '--tool-api', inputs['tool_api'], '--output-dir', relative,
                '--slot', f'final-{label}-a{number}-{started:%Y%m%dT%H%M%SZ}',
                '--simulation-lease', str(args.simulation_lease), '--provider', provider,
                '--model', model, '--reasoning-effort', effort, *(relaxed if mode == 'relaxed' else [])]
        builds = _codex_builds()
        # A Claude attempt gets the key and no Codex account; the key is never printed or recorded.
        extra = {'ANTHROPIC_API_KEY': key} if provider == 'claude' else {'CODEX_HOME': str(account)} if account else {}
        with open(ROOT / relative / 'builder.log', 'wb') as log:
            process = subprocess.Popen(argv, cwd=ROOT, env={**env, **extra},
                                       stdin=subprocess.DEVNULL, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
        record = {'label': label, 'attempt': number, 'round': args.round, 'provider': provider, 'model': model,
                  'mode': mode, 'reasoning_effort': effort,
                  'pid': process.pid, 'started_at': started.isoformat(), 'argv': argv,
                  'codex_account': account.name if account else None,
                  'builder_processes_before_start': builds, 'staging_health': health}
        (ROOT / relative / 'launch.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'label': label, 'attempt': number, 'provider': provider, 'mode': mode, 'reasoning_effort': effort,
                          'pid': process.pid, 'codex_account': record['codex_account'], 'output_dir': relative,
                          'staging_health': health}),
              flush=True)
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
            print(json.dumps({'label': label, **{key: entry[key] for key in ('attempt', 'status', 'provider', 'reasoning_effort',
                                                                              *package.ATTEMPT_ORDER)}, 'notes': notes}))
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
                          'mode': best['mode'] if eligible else None, 'run': best['run'] if eligible else None}))
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


def health(args):
    print(json.dumps(_probe(_env(args.env_file))))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    start = commands.add_parser('launch')
    start.add_argument('--env-file', type=Path, required=True, help="Private .env, read by the data-only parser")
    start.add_argument('--model', default='claude-opus-5-5', help='Model of the Claude attempts')
    start.add_argument('--providers', nargs='+', choices=('claude', 'codex'), default=['claude'],
                       help='Claude everywhere by default; add codex for the optional Codex cells (high + medium)')
    start.add_argument('--codex-model', default='gpt-6.1-sol', help='Model of the optional Codex attempts')
    start.add_argument('--simulation-lease', type=Path, required=True)
    start.add_argument('--round', type=int, required=True, choices=sorted(ROUNDS),
                       help='1: two strict Claude attempts per label; 2: two relaxed ones, only after round 1')
    start.add_argument('--max-running', type=int, default=8, help='Machine cap on concurrently running builders')
    start.add_argument('--stagger-seconds', type=float, default=60, help='Pause between consecutive starts')
    start.add_argument('--labels', nargs='+', choices=LABELS, default=list(LABELS))
    pick = commands.add_parser('select')
    pick.add_argument('--labels', nargs='+', choices=LABELS, default=list(LABELS))
    commands.add_parser('manifest').add_argument('--team', required=True)
    commands.add_parser('health').add_argument('--env-file', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        return {'launch': launch, 'select': select, 'manifest': manifest, 'health': health}[args.command](args)
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Plan B builder for the final case: from the case folder and our bank mock to one exported phone agent.

    python3 planb/build.py <case folder or .zip> <mock url>

1. Claude runs in a fresh copy of planb/ outside the repository (<temp>/planb-final/<start>/kit), so no repository
   instruction file reaches it. tools/case_folder.py turns the case folder into kit/out/case/ (INDEX.md lists every
   document, BRIEF.md first).
2. Claude (Claude Agent SDK, skill .claude/skills/build-phone-agent) reads it, writes the phone agent with every tool
   URL on the mock, saves it to the slot `final` (the same workflow on every run), self-tests it on the mock's test
   customers through Freya simulations of our one workspace under the shared lease, and fixes what fails. The run
   stops at 29 minutes.
3. The saved workflow is exported to submission/final.workflow.json.

Everything it prints is the official trace (start and end time, every model call with its model and a one-line purpose,
the tests, what was created on Freya), with keys removed; it is also written to traces/final.log. Both files get a
timestamped copy, so a later run never loses an earlier one. Keys come from the environment, else from the main
checkout's .env and ~/.freya-anthropic-key: ANTHROPIC_API_KEY, FREYA_API_KEY and FREYA_BANK_TEAM_KEY. Optional:
FREYA_LEASE (default: the main checkout's artifacts/.simulation.lock), BUILDER_EFFORT (default max).
"""
import asyncio, datetime, hashlib, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

KIT = Path(__file__).resolve().parent
REPO = KIT.parent
sys.path += [str(KIT), str(REPO)]   # appended, so nothing here shadows a module of whoever imports this file
from builder.config import read_local_environment
from tools import case_folder

SLOT, LIMIT = 'final', 1740   # 30 minutes per build, tests included, minus a minute for the export
WORKFLOW, TRACE = REPO / 'submission' / 'final.workflow.json', REPO / 'traces' / 'final.log'
WORK, KEYFILE = Path(tempfile.gettempdir()) / 'planb-final', Path.home() / '.freya-anthropic-key'
RUN_STATE = shutil.ignore_patterns('traces', 'out', 'results', 'builders', 'briefs', '__pycache__', '.freya-agents.json', '.personas.json')
SECRETS = ('ANTHROPIC_API_KEY', 'FREYA_API_KEY', 'FREYA_API_KEY_2', 'FREYA_BANK_TEAM_KEY', 'CLOUDFLARE_API_TOKEN')
KEYLIKE = re.compile(r'fak_[\w-]+|sk-ant-[\w-]+|sk-[\w-]{20,}')
PROMPT = """Use the build-phone-agent skill to build the phone agent for the bank case in out/case/.
- Start with out/case/INDEX.md. It lists every document of the case in reading order (BRIEF.md first, README.md second),
  with its readable version under out/case/text/ and the original under out/case/files/. Read every document, and open
  every image (the flow drawing) with the Read tool.
- The case's service contract is the tool API file. The bank for this case is our mock at {mock} (also $MOCK_URL):
  every tool URL is {mock} followed by the operation's path from the contract, never the contract's own server address.
- The example conversations show the tone, the flow and the language the bank wants: the agent speaks the language of
  the case's customers.
- There are no practice cases for this case: self-test on the case's test customers, which the mock knows
  (tools/run_cases.py already talks to the mock).
- Save the agent with the slot name `{slot}` and write your trace notes to out/{slot}.notes.txt."""

trace_file, secrets = None, []


def say(*parts):
    line = ' '.join(str(p) for p in parts)
    for s in secrets:
        line = line.replace(s, '[key removed]')
    line = KEYLIKE.sub('[key removed]', line)
    print(line, flush=True)
    trace_file.write(line + '\n')
    trace_file.flush()


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def one_line(text, n=200):
    text = ' '.join(str(text).split())
    return text if len(text) <= n else text[:n - 3] + '...'


def git(*args):
    return subprocess.run(['git', '-C', str(REPO), *args], capture_output=True, text=True).stdout.strip()


def purpose(block):
    if hasattr(block, 'name'):
        i = block.input or {}
        return f"{block.name}: {one_line(i.get('command') or i.get('file_path') or i.get('pattern') or json.dumps(i))}"
    return 'says: ' + one_line(block.text)


def slot_state(kit):
    try:
        return json.loads((kit / '.freya-agents.json').read_text()).get(SLOT)
    except (OSError, ValueError):
        return None


async def session(mock, kit):
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, SystemMessage, TextBlock, ToolUseBlock, query
    options = ClaudeAgentOptions(
        cwd=kit, model=os.environ.get('BUILDER_MODEL', 'claude-opus-5-5'), effort=os.environ.get('BUILDER_EFFORT', 'max'),
        fallback_model=os.environ.get('BUILDER_FALLBACK_MODEL', 'claude-sonnet-5-5'), skills=['build-phone-agent'],
        setting_sources=['project'], allowed_tools=['Read', 'Write', 'Edit', 'Glob', 'Grep', 'Bash'], permission_mode='dontAsk',
        max_turns=150,   # a test runs up to 11 minutes in the foreground; the builder stops when Claude ends its turn
        env={'BASH_DEFAULT_TIMEOUT_MS': '900000', 'BASH_MAX_TIMEOUT_MS': '1200000', 'CLAUDE_CODE_DISABLE_BACKGROUND_TASKS': '1'})
    say(f'builder: model {options.model}, effort {options.effort}, fallback {options.fallback_model}, skill build-phone-agent')
    raw = kit / 'traces' / 'messages.jsonl'   # every message, for us; it stays outside the repository
    raw.parent.mkdir(exist_ok=True)
    calls, stream = {}, query(prompt=PROMPT.format(mock=mock, slot=SLOT), options=options)
    with raw.open('w') as out:
        try:
            async for m in stream:
                out.write(json.dumps({'type': type(m).__name__, 'message': m}, default=lambda o: getattr(o, '__dict__', str(o))) + '\n')
                if isinstance(m, SystemMessage) and m.subtype == 'init':
                    say(f"[{time.strftime('%H:%M:%S')}] Claude session started: model {m.data.get('model')}, skills {m.data.get('skills')}")
                elif isinstance(m, AssistantMessage):
                    for b in m.content:
                        if not isinstance(b, (TextBlock, ToolUseBlock)):
                            continue
                        key = m.message_id or id(m)
                        if key in calls:
                            say(f'      also: {purpose(b)}')
                        else:
                            calls[key] = (len(calls) + 1, m.model)
                            say(f"[{time.strftime('%H:%M:%S')}] model call {len(calls)} ({m.model}): {purpose(b)}")
                elif isinstance(m, ResultMessage):
                    say(f'Claude finished: {m.subtype}, {m.num_turns} turns, {m.duration_ms / 1000:.0f} s, ${m.total_cost_usd or 0:.2f}'
                        + (f'; errors: {one_line(getattr(m, "errors", None), 500)}' if getattr(m, 'errors', None) else ''))
                    for k, v in (m.model_usage or {}).items():   # every model the session called, Claude Code's helpers included
                        helper = '' if k in {c[1] for c in calls.values()} else '; Claude Code\'s own helper calls (such as summaries), not listed one by one'
                        say(f"  model {k}: {v.get('inputTokens', 0)} input (+{v.get('cacheReadInputTokens', 0)} cached), {v.get('outputTokens', 0)} output tokens, ${v.get('costUSD', 0):.2f}{helper}")
                    say('Claude\'s last message:', one_line(m.result or '', 2000))
        finally:
            await stream.aclose()   # stops the Claude process when the time limit cancels the loop
            say(f'model calls: {len(calls)}; raw messages: {raw}')


def summary(path, mock):
    """Steps, tools and tool URLs of the exported workflow. Returns the URLs that are not on the mock."""
    w = json.loads(path.read_text())
    tools, urls = set(), []

    def walk(x):
        if isinstance(x, dict):
            f, u = x.get('function'), x.get('url')
            if isinstance(f, dict) and f.get('name') not in (None, 'code_exec'):
                tools.add(f['name'])
            if isinstance(u, dict) and isinstance(u.get('default'), str):
                urls.append(u['default'])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(w['workflow'])
    nodes = w['workflow'].get('nodes') or {}
    names = list(nodes) if isinstance(nodes, dict) else [n.get('id') for n in nodes]
    say(f"created on Freya: workflow id {w['workflow_id']}, agent id {w['agent_id']}, updated {w.get('updated_at')}")
    say(f"steps: {len(names)} ({', '.join(map(str, names))}); tools: {len(tools)} ({', '.join(sorted(tools))})")
    off = [u for u in urls if not u.startswith(mock.rstrip('/') + '/')]
    say(f'tool URLs: {len(urls)}, ' + ('all on the mock' if not off else f'NOT on the mock: {sorted(set(off))}'))
    return off


def main(case, mock):
    global trace_file, secrets
    t0, stamp = time.time(), time.strftime('%Y%m%d-%H%M%S')
    common = git('rev-parse', '--path-format=absolute', '--git-common-dir')
    checkout = Path(common).parent if common else REPO   # the main checkout: our private .env and the shared lease
    local = read_local_environment(checkout / '.env')
    for k in ('FREYA_API_KEY', 'FREYA_API_URL', 'FREYA_BANK_TEAM_KEY', 'FREYA_WORKSPACE_ID'):   # never the second key
        if not os.environ.get(k) and local.get(k):
            os.environ[k] = local[k]
    if not os.environ.get('ANTHROPIC_API_KEY') and KEYFILE.is_file():
        os.environ['ANTHROPIC_API_KEY'] = KEYFILE.read_text().strip()
    missing = [k for k in ('ANTHROPIC_API_KEY', 'FREYA_API_KEY', 'FREYA_BANK_TEAM_KEY') if not os.environ.get(k)]
    if missing:
        sys.exit(f'missing keys: {", ".join(missing)}')
    secrets = sorted({os.environ[k] for k in SECRETS if len(os.environ.get(k) or '') >= 8}, key=len, reverse=True)
    for k in [k for k in os.environ if k.startswith('CLAUDE')]:   # never a parent Claude session's login or settings
        del os.environ[k]
    os.environ.setdefault('FREYA_LEASE', str(checkout / 'artifacts' / '.simulation.lock'))
    os.environ['PATH'] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', '')   # `python3` = this Python
    TRACE.parent.mkdir(parents=True, exist_ok=True)
    trace_file, code = TRACE.open('w'), 1
    try:
        say('Plan B builder: final case run')
        say('start:', now())
        say('command: python3 planb/build.py', case, mock)
        code = run(case, mock, t0, stamp)
    except Exception as e:
        say(f'the builder failed: {type(e).__name__}: {one_line(e, 500)}')
    finally:
        say(f'end: {now()} (elapsed {int(time.time() - t0) // 60} min {int(time.time() - t0) % 60} s); result:', 'OK' if code == 0 else 'FAILED')
        trace_file.close()
        shutil.copy(TRACE, TRACE.with_name(f'final-{stamp}.log'))
    return code


def run(case, mock, t0, stamp):
    import freya   # after the keys are in the environment: freya.py reads them when it is imported
    dirty = git('status', '--porcelain', '--untracked-files=no')
    say(f"repo commit: {git('rev-parse', 'HEAD') or 'unknown'}{' (with uncommitted changes)' if dirty else ''}")
    skill = KIT / '.claude/skills/build-phone-agent/SKILL.md'
    say(f'skill: {skill.relative_to(REPO)} sha256 {hashlib.sha256(skill.read_bytes()).hexdigest()[:16]}')
    host = '{0.scheme}://{0.netloc}'.format(urllib.parse.urlsplit(mock))
    try:   # our mock (mock/worker.js) reports the contract's base path and its operations here
        with urllib.request.urlopen(urllib.request.Request(host + '/_mock/health', headers={'User-Agent': 'Mozilla/5.0 (planb-build)'}), timeout=15) as r:
            health = json.loads(r.read() or b'{}')
    except (urllib.error.HTTPError, ValueError):
        health = {}   # it answers, but is not our mock
    except Exception as e:
        say(f'stopped: the mock at {host} does not answer ({e}). Deploy it before the builder runs.')
        return 1
    base = mock.rstrip('/') if urllib.parse.urlsplit(mock).path.strip('/') else host + (health.get('base_path') or '')
    os.environ.update(MOCK_URL=base, FREYA_BANK_HOST=host, CLAUDE_CONFIG_DIR=str(WORK / 'claude-config'))
    os.environ.setdefault('FREYA_SIM_CONCURRENCY', '10')   # official rules: no load tests
    say(f"mock: tool base URL {base}; the mock reports {health.get('customers', '?')} customers and {len(health.get('operations') or [])} operations")
    Path(os.environ['FREYA_LEASE']).parent.mkdir(parents=True, exist_ok=True)
    say(f"self-tests: Freya simulations in workspace {freya.workspace_id()}, at most {os.environ['FREYA_SIM_CONCURRENCY']} calls at once, lease {os.environ['FREYA_LEASE']}")
    kit, registry = WORK / stamp / 'kit', WORK / 'freya-agents.json'
    shutil.copytree(KIT, kit, ignore=RUN_STATE)
    if registry.is_file():   # the slot's workflow from earlier runs, so it is updated in place
        shutil.copy(registry, kit / '.freya-agents.json')
    say(f'working copy: {kit}')
    entries = case_folder.convert(case, kit / 'out' / 'case')
    kinds = {}
    for e in entries:
        kinds[e['kind']] = kinds.get(e['kind'], 0) + 1
    say(f'case: {len(entries)} documents ({", ".join(f"{v} {k}" for k, v in sorted(kinds.items()))}) -> out/case/INDEX.md')
    for e in entries:
        if e['error'] or e['kind'] == 'binary':
            say(f"  not converted: {e['source']} ({e['error'] or 'binary'}); Claude gets the original")
    before = slot_state(kit)
    try:
        asyncio.run(asyncio.wait_for(session(base, kit), LIMIT - (time.time() - t0)))
    except asyncio.TimeoutError:
        say(f'stopped: the time limit ({LIMIT} s) was reached')
    except Exception as e:
        say(f'the Claude session failed: {type(e).__name__}: {one_line(e, 500)}')
    log, notes = kit / 'results' / 'log.jsonl', kit / 'out' / f'{SLOT}.notes.txt'
    for line in (log.read_text().splitlines() if log.is_file() else []):
        t = json.loads(line)
        say(f"self-test at {t['at']}: {t['passed']} / {t['total']} passed, {t['seconds']} s, agent {t['agent_id']}, failed {t['failed']}")
    if notes.is_file():
        say('builder notes (written by Claude):')
        for line in notes.read_text().splitlines():
            say('  ' + line)
    entry = slot_state(kit)
    if (kit / '.freya-agents.json').is_file():
        shutil.copy(kit / '.freya-agents.json', registry)
    if not entry or entry == before:
        say('no workflow was saved in this run, so nothing was exported')
        return 1
    WORKFLOW.parent.mkdir(parents=True, exist_ok=True)
    freya.export_workflow(entry['workflow_id'], str(WORKFLOW))
    copy = WORKFLOW.with_name(f'final-{stamp}.workflow.json')
    shutil.copy(WORKFLOW, copy)
    off = summary(WORKFLOW, base)
    say(f'export: {WORKFLOW.relative_to(REPO)} (copy {copy.relative_to(REPO)})')
    return 1 if off else 0


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2]))

# T09 code-validation preparation

This parallel branch owns `validate_code` hardening and its independent
regressions. The primary Codex thread retains T02 runtime isolation, T05 frozen
policy orchestration and T08 action guards. The shared local coordination files
are under `artifacts/thread-coordination/` in the main checkout; reviewed
checkpoints are also recorded on the task issues. Branches remain separate.

Before this change, five source patterns returned no local validation errors:
aliasing `eval` to another name, aliasing `json.load` through a `from` import,
importing `json.tool.main` under the allowed `json` root, wildcard imports, and
`while 1`. The regression checks parse these sources and never execute them.

The validator now rejects references to unsafe builtins, including storage in
aliases/containers and use as callbacks. Allowed module names are exact, so an
approved module does not authorize all submodules. Imported unsafe functions
and wildcard imports are rejected before their names can be hidden by an
alias. Truthy literal while conditions, including negative numbers, are
rejected. Approved JSON/decimal/calendar aliases, bounded `for` loops, false
literal loops and existing fallback checks remain supported.

This is a conservative source check, not an operating-system sandbox or a
proof that arbitrary Python terminates. Generated workflow behavior and
action enforcement still require the primary guard compiler and hosted
evidence. This branch does not change `guard_context`, generated guards,
controller behavior, model permissions or the execution boundary.

Verification:

```sh
python3.11 -m unittest discover -s tests -p 'test_code_safety_regressions.py' -v
python3.11 -m unittest discover -s tests -v
python3.11 tools/package_submission.py --check
```

T09 remains open until the guarded compiler is integrated and the full
structural/semantic acceptance checks have evidence. No banking generation or
Freya execution is part of this work.

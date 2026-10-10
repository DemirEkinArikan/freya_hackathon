# T05 platform readback comparison

Freya can preserve an authored workflow while adding serialization defaults.
The previous controller compared JSON exactly after removing `agent_config`, so
it rejected the successful T03 deployment. `builder/readback.py` now compares
all authored values recursively and admits only explicit defaults at reviewed
locations. Missing fields, changed types, changed list length/order, additional
nodes/tools/hooks or state variables, and unknown fields (even null/empty) fail.
Readback failure stops before hosted tests, export or a model repair attempt.

Evidence is the synthetic T03 staging run completed at 2026-10-10 19:57 Istanbul,
workflow `53673c26-6204-4134-91ec-b0eaf0af8480`, published version 2. It retained
all authored values and added 38 fields outside the adapter-owned agent config.
`tests/fixtures/platform-readback.json` preserves the synthetic authored/stored
pair, with agent config reduced to the fixed-model assertion. It contains no
credentials or customer records and is never a generation input. Original local
artifacts remain under `artifacts/t03-naci-acceptance-20261010/`.

The admitted additions are empty root `global_nodes`/`test_variables`, reviewed
common fields on conversation/end_call nodes, `mode: exact` on their direct
matches_intent conditions, and an empty end-call function description. These
rules do not match arbitrary nested parameter schemas or other dictionary keys.
If a field was explicitly authored, it remains mandatory and immutable, even if
its value happens to equal a default.

Only conversation/end_call types and direct matches_intent conditions have
retained live evidence here. Defaults for code/api_request nodes, nested condition
groups, other conditions, tool schemas and mappings remain unaccepted until their
actual serialization is reviewed. Do not weaken this boundary to complete a build.
The kit's max_node_turns example default is 10, but this live server returned null
for an omitted cap; this comparator admits only that observed addition. An authored
cap may never be changed to null.

The root `agent_config` continues to belong to the platform adapter, as before this
change. The comparator also requires its provider/model to remain experimental-5.
It does not claim an audit of all platform-managed voice/call settings. Authored
agent_config, if supplied, is compared rather than discarded. Guarded compilation
continues to reject model-authored configuration.

Successful comparisons emit `deployment_readback` with `verified: true` and
`accepted_default_fields`. The full stored definition goes unchanged to hosted
tests and export. This check proves structural preservation; it does not prove
that a generated banking policy is complete or a conversation is correct.

Verification:

```sh
python3.11 -m unittest discover -s tests -p 'test_readback.py' -v
python3.11 -m unittest discover -s tests -v
```

T05 still needs a complete valid public banking build within 30 minutes and live
acceptance of its generated guarded workflow. This local change does not close
T05, merge T02 PR #48, start a simulation or authorize new platform mutations.

# Pinned organizer contract

Kit: **build-the-builder v3, 10 October 2026**. Preserve the supplied client and
references as vendor material. Kickoff email confirms staging, a workspace API
key and a stable bank team key of at least 16 characters.

## Authorities

1. Current organizer kit and later explicit organizer updates.
2. Supplied brief files and the matching tool OpenAPI for each build.
3. Public website for schedule/ranking; generic API docs are secondary.

The current website and kit v3 both specify 200 workflows per workspace; retain
persistent in-place slots. One concurrent **simulation**, not one customer within
a simulation; preserve the kit's documented concurrency. No numeric final-window
testing quota is published in the reviewed materials.

The current written scope is debt collection with the same seven tool names and
English conversations. Fields, enums, identity questions, calendars and outcome
tables may change. Earlier kickoff remarks mentioned banking/insurance more
broadly; use the current written scope while requesting reconciliation.

## Known interface and policy traps

- Staging: `https://staging-app.freyavoice.ai`, browser-like User-Agent required.
- Updating JSON does not publish it. Save, publish and inspect the deployed
  definition and version.
- Generated JSON omits `agent_config`; enforce the event model during deployment.
- Extraction, routing and dialogue have separate prompts. Dialogue instructions
  do not enforce route guards. Variables persist across nodes.
- Python code nodes use `vars`; source must not contain template delimiters.
- Identity has three wrong answers total. DOB ambiguity hints and unreadable DOB
  do not consume a wrong answer. Successful factors remain verified.
- All date rules use the supplied `now`, never the host clock.
- Valid amounts include the entire floor-to-balance interval. Hardship allows
  valid volunteered commitments but no persuasion.
- Brief A writes no notes. Brief B stages non-promise notes until non-transfer
  closing: every transfer prohibits notes before and after it.
- Transfers preserve recorded promises and abandon agreed-but-unwritten ones.
- Recorded changes go to the app. Exactly one final `close_call` follows goodbye.
- The official HTTP 500 write fault means nothing was committed and allows the
  specified retry. Unsupported post-commit timeouts are local advisory probes.

## Benchmark evidence boundaries

The supplied runner sends only `{"case": "PUB-xx"}` to session creation. A local
case JSON does not seed accounts, identities, calendars, policy or faults.
Hosted variants must retain the selected official seed's facts. Independent
policy/schema/calendar adaptation is assessed locally, not claimed as observed
Freya conversation behavior. No custom external tool service is attached to a
competition agent.

## Final window

Sunday 11 October 2026: hidden briefs open 17:00 UTC / **20:00 Istanbul**;
submission closes 20:00 UTC / **23:00 Istanbul**. Customer cases remain hidden.
Final submission instructions are still to be sent. Do not hardcode a final
submission address or hidden-specific values.

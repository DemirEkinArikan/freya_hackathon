# T05 real Brief A attempt — 10 October 2026

First accepted readback changes were committed as `53b92cb` before the live run.
The run used the unmodified organizer Brief A and its supplied OpenAPI, model
`gpt-6.1-sol`, high reasoning, persistent target slot `t05-naci-brief-a`, and a
30-minute configured build budget. No evaluator fixtures were generator inputs.

The real isolation audit passed. Policy extraction returned after about 323 seconds;
a review attempt returned after about 225 seconds. Both were rejected for the same
unresolved source condition: rule 30 proposes the current week's last business day,
while rule 19 excludes past promise dates; the next-week exception explicitly
covers today being the last business day, leaving later days unaddressed. No bank
amendment was supplied. We did not erase the unsupported constraint or invent an
outcome/date. The third redundant call was interrupted; the raw run manifest thus
records an incomplete model turn. This was not a successful banking build.

Local evidence: `artifacts/t05-brief-a-20261010/20261010T171914579731Z/`, including
`policy.rejected-1.json`, `policy.rejected-2.json`, `trace.jsonl`, and `manifest.json`.
No valid frozen contract, guarded plan, banking workflow, publication or export
was produced by that run. No simulation or bank tool call was started.

The first rejected policy also attributed API HTTP/nonpersistence facts to a brief
quotation and omitted members of an executable identity profile. Followup source
changes add exact optional OpenAPI provenance, expose the compiler's required-rule
interface, aggregate independent validation errors into one repair, and stop an
unresolved source ambiguity after one review. They do not reinterpret bank policy.

UTC trace timestamps spanned substantially more time than the monotonic budget
reported. The precise environmental cause was not established. Deadline and
subprocess limits now check both wall and monotonic elapsed time, conservatively
rejecting a late result after a suspend or forward wall-clock change. Tests cover
suspend, rollback, expired export reserve and termination of a late process group.
This is source-level regression evidence, not a new successful full build.

Independent code/API serialization was nevertheless checked on Freya using a
separate synthetic workflow. See T05-READBACK.md. This probe proves preservation,
publication and export, not execution of generated banking code or API calls.

Remaining: authoritative resolution of the source ambiguity; a fresh unmodified
(or explicitly amended and labelled) Brief A full build under the final source;
validation of any further platform normalization; and end-to-end export/trace
acceptance within the time budget. T05 must remain open. Scored conversation
behavior and broader generalization are separate from a structurally valid build.

# Authored entry routing

The restricted action plan accepts `passthrough` and
`passthrough_default_transition` on public conversation nodes. The kit defines
`passthrough: true` as routing after entry without a new caller turn, including
silent entry nodes. Ordinary conversation nodes continue to wait for caller
input when the flag is absent or false.

An immediate route must declare a public default target and finish its
transitions with an `always` fallback to that same target. Boolean flags are
strict booleans. An explicitly supplied default must be a nonempty declared
public node name; nulls, malformed values, unknown targets and private API
targets are rejected. These fields do not permit authored API/code nodes or
override a guarded action's controller routes.

Compilation copies the declared conversation scheduling fields and continues
to seal action execution. Exact compiled-workflow comparison rejects changes
to those routes after compilation. The structured-generation outer schema
already carries the plan as a JSON string tied to the frozen contract hash,
so it needs no new wire properties.

The declaration hook supplies no scheduling default, speech text, banking rule,
or policy outcome. Separate generation guidance decides which sourced steps
route after entry and which require a fresh caller response. Static declaration
and compilation checks do not establish actual platform timing or speech
compliance. The current evaluator replay routes conversation conditions without
first distinguishing an entry event from a caller event; its execution alone
cannot establish these platform scheduling semantics.

References: organizer `build-the-builder/docs/workflow-format.md`, node fields,
entry behavior, tool-call transitions and passthrough nodes.

# Kit entry routing in generated dialogue plans

The phone-plan prompts distinguish an entry event from a new caller answer.
The pinned kit states that silent entry waits unless a node is passthrough.
State-only decisions can declare `passthrough: true`, a public default target and
a matching final `always` fallback. Caller-answer nodes retain their waiting
declaration; the generator must not turn every silent node into an immediate
route or reuse an older answer as fresh input.

Source-required ending speech needs a real completion trigger before guarded
closing. Short fixed TTS entry can use passthrough. For an LLM speaking step,
transition-level `is_tool_call: true` exposes an engine-provided transition tool
that the model can invoke after its words in the same message. This is not an
authored bank API tool, an operator inside `condition`, or permission to bypass
the guarded close. The alternative `is_tool_call_transition` spelling is not
documented or accepted as a transition field.

The controller may remap public targets through private entry gates. Therefore
task instructions must select the offered completion transition by its
description rather than hardcode a tool name derived from the authored node.
The kit derives the actual `transition_to_<target>` name from the compiled
target; private gate names are never customer speech.

The dedicated checks preserve approved frozen prompt inputs, declaration flags,
caller extraction, default/fallback consistency, top-level tool-call flags and
protected gate remapping. They prove static source transport and authenticated
compiled declarations. They do not execute models, Freya, hidden inputs or old
generated artifacts, and do not establish platform timing or speech compliance.
The current local replay evaluates conversation transitions without separating
entry and caller events, so it is deliberately not used as scheduling evidence.

References: organizer `build-the-builder/docs/workflow-format.md`, node fields,
entry behavior, tool-call transitions, passthrough nodes and silent end-call.

"""A small agentic builder: Claude reads the brief, writes the phone agent, tests it and fixes it.

    pip install claude-agent-sdk==0.2.165
    cd planb && python3 agent_builder.py briefs/<brief>.md briefs/<tool-api>.json

Pass every brief file and the brief's tool API file (the .json); without a .json the skill uses tools/openapi.json.
It runs Claude through the Claude Agent SDK with one skill, `.claude/skills/build-phone-agent`.
The skill is the builder's knowledge. Improve the skill and you improve every agent it builds.
Needs FREYA_API_KEY and FREYA_BANK_TEAM_KEY (see ../build-the-builder/env.example) and ANTHROPIC_API_KEY or a Claude login.
Every message of the run is written to traces/<start time>.jsonl. That file is the trace you submit.
"""
import asyncio, dataclasses, json, os, pathlib, sys, time

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, ToolUseBlock, query

KIT = pathlib.Path(__file__).resolve().parent


async def main(briefs):
    trace = KIT / "traces" / (time.strftime("%Y%m%d-%H%M%S") + ".jsonl")
    trace.parent.mkdir(exist_ok=True)
    options = ClaudeAgentOptions(
        cwd=KIT,
        model=os.environ.get("BUILDER_MODEL", "claude-opus-5-5"),
        effort=os.environ.get("BUILDER_EFFORT", "max"),  # founder 18:02: best Claude model at its best effort
        fallback_model=os.environ.get("BUILDER_FALLBACK_MODEL", "claude-sonnet-5-5"),
        skills=["build-phone-agent"],          # the skill in .claude/skills
        setting_sources=["project"],           # only this folder's settings, not your own
        allowed_tools=["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
        permission_mode="dontAsk",             # anything not listed above is refused
        max_turns=150,
        # A test run takes up to 11 minutes. Let a command run that long, and keep it in the foreground:
        # the builder stops when Claude ends its turn, so a test left in the background is never read.
        env={"BASH_DEFAULT_TIMEOUT_MS": "900000", "BASH_MAX_TIMEOUT_MS": "1200000", "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"},
    )
    prompt = "Use the build-phone-agent skill to build a phone agent for: " + " ".join(briefs)
    with trace.open("w") as out:
        out.write(json.dumps({"start": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "briefs": briefs, "model": options.model, "effort": options.effort,
                              "fallback_model": options.fallback_model,
                              "provider": "anthropic-api-key" if os.environ.get("ANTHROPIC_API_KEY") else "claude-login"}) + "\n")
        try:
            async for message in query(prompt=prompt, options=options):
                out.write(json.dumps({"type": type(message).__name__, **dataclasses.asdict(message)}, default=str) + "\n")
                out.flush()
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            print(block.text, flush=True)
                        elif isinstance(block, ToolUseBlock):
                            print(f"  [{block.name}] {str(block.input)[:120]}", flush=True)
                elif isinstance(message, ResultMessage):
                    print(f"\ndone: {message.num_turns} turns, {message.duration_ms / 1000:.0f} s, ${message.total_cost_usd or 0:.2f}. Trace: {trace}")
        finally:
            out.write(json.dumps({"end": time.strftime("%Y-%m-%dT%H:%M:%S%z")}) + "\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(asyncio.wait_for(main(sys.argv[1:]), 1740))   # the kit allows 30 minutes per build, tests included

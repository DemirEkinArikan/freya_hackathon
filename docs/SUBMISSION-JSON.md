# submission.json (final case)

The final case opens Sunday 17:00 UTC (20:00 Istanbul). Send `submission.json` before 20:00 UTC (23:00 Istanbul)
to the address in the organisers' kit e-mail. One submission per team. An entry without a trace is not ranked.

## What Demir fills in

1. `--team`: the team name we registered with.
2. `--member "Full Name <email>"`: one per member, at most two (a solo team lists one).
3. The submission e-mail address: from the organisers' kit e-mail (the hackathon page only says "the address in
   your kit email"). It is not part of the JSON.

Everything else comes from tonight's run: `--run-command` (with `{case_folder}` and `{mock_url}`), `--workflow`
(the `freya.export_workflow` file), `--trace` (the full builder output) and `--mock-url` (our Cloudflare Workers mock).

## Steps

```bash
git add -f submission/final.workflow.json traces/final.log   # both folders are git-ignored
git commit -m "Final submission: exported workflow and trace" && git push
python3 tools/make_submission.py --team "TEAM" --member "Name <name@example.com>" \
  --run-command "python3 builder/build.py {case_folder} {mock_url}" \
  --workflow submission/final.workflow.json --trace traces/final.log --mock-url https://OUR-MOCK.workers.dev
```

The tool prints the JSON to paste into the e-mail. It refuses when the workflow file's `workflow_id`/`agent_id`
are not both named in the trace, when either file holds a key (Freya, Anthropic, OpenAI, Cloudflare or any local
key value), or when the files are not committed unchanged in a pushed commit.

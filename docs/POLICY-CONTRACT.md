# Policy contract boundary

`compile_policy_contract(policy, sources, openapi)` accepts typed policy IR and
the exact ordered inputs read by the controller. It performs no I/O or model
calls. Each source declares `id`, `file`, `sha256` and `text`; its fingerprint is
`builder.safety.digest(text)`, the canonical JSON text fingerprint.

The policy must copy every source identifier, filename and fingerprint and
include a literal rule citation from each supplied document. A missing amendment
or a present but uncited source fails compilation. Rules carry `id`, `kind`,
`value`, `source: {id, quote}` and optional `overrides`. Top-level values cannot
shadow quoted rules. Explicit same-kind overrides resolve active values; source
or array order supplies no implicit precedence. Equal values retain all witnesses.
Cross-kind replacement/removal is unsupported and must be represented as an
unresolved constraint, rather than silently erasing another requirement.

The detached result contains effective rules and provenance, source fingerprints,
and the permitted operations with resolved request/response schemas, parameters
and security declarations. Changed fields or enums change the contract without
editing source. Conflicting schema compositions fail closed. The `sha256` covers
the result before the hash field is added.

Every accepted candidate that supplies policy now writes `policy.contract.json`.
Its hash appears in the run manifest and a `policy_contract` trace event. A legacy
bare workflow can still be inspected offline, but has no policy contract or
provenance claim.

## Extract policy independently

```sh
python3.11 builder/build.py \
  --brief build-the-builder/briefs/brief-a.md \
  --brief build-the-builder/briefs/brief-b.md \
  --tool-api build-the-builder/tools/openapi.json \
  --extract-policy-only
```

This uses the existing authenticated Codex CLI and its isolation controls. The
model receives the approved sources and API, without workflow runtime examples
or evaluator data. The existing transport still requires two JSON strings; its
workflow field is unused. The controller creates no workflow artifact or Freya
client, and rejects deployment/testing flags. Validation repairs remain bounded
by the run deadline. Model transport failures are retained rather than treated as
policy mistakes.

The status `policy_extracted` establishes extraction and structural provenance
checks. Literal quotation and source coverage cannot prove that every sentence
was interpreted correctly or that all operative rules were extracted. The result
explicitly records `semantic_correctness_verified: false`. Workflow enforcement
remains T08 work and conversation compliance requires separate evaluation.

The next controller stage should consume this frozen contract and reject policy
changes while generating or repairing the workflow. Full runtime generation is
still supported through the existing build path; extracting policy separately
does not yet replace that default pipeline.

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

Each `active_rules[kind].value` is the resolved value for that concept. Its
`rule_ids` and `provenance` identify the active witnesses. The additional
`superseded_rule_ids` and `superseded_provenance` retain every explicitly
overridden ancestor, including ancestors reached through a chain of amendments.
Historical witness entries contain the original rule ID, source ID and literal
quote; they do not contain or reactivate historical values. IDs and witnesses are
sorted deterministically, and the evidence is included in the contract hash.

The contract sets `provenance_is_operative: false`. All quoted provenance is
evidence for review, not instructions for execution. A quotation can include a
clause superseded by another rule. Consumers must use resolved active values and
their explicit scopes instead of treating witness text as additional rules.
Overrides replace a whole rule value: compilation does not infer which fields
of a historical value should survive a partial amendment. Ancestor evidence
preserves traceability without asserting that such inheritance was interpreted
correctly; semantic review is still required.

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

# Identity closing interface checkpoint

Fresh A+B extraction from checked source
`983d3558738b5e122979fd8cd553a4402795bfa1`, run
`20261010T215246913137Z`, returned a first candidate rejected for extra rule
fields. Its second candidate cleared that strict format check but failed the
existing cross-rule guard: `identity closing choice outcome must match its fixed
scope`. Both original rejected artifacts remain unchanged.

The candidate incorrectly placed a transferred outcome into the incomplete
identity scope with no recorded-transfer precondition. The approved source's
successful human handoff is represented by the kernel's protected transfer
fact. An identity choice cannot manufacture that fact or change its fixed
scope's outcome. Every optional choice must also use that outcome's sourced
reason vocabulary. This patch clarifies those existing requirements in the
value-free interface guidance; it changes no predicate, bank value, runtime
gate or generated artifact.

Independent review confirmed the candidate's literal source quotes and all
fourteen operation/status API witnesses. Its two-attempt limit and sourced
service-error exhaustion disposition correct the earlier extraction omissions.
Those improvements do not establish accepted policy, workflow or conversation
behavior. Automatic bounded repair remains the only mechanism changing the
candidate; reviewer findings do not enter generator context.

The parallel fresh A run `20261010T215248342248Z` had a passing isolation audit
and a successful CLI exit after 271.96 seconds, then failed the generation-stage
object contract. It produced no policy for validation. Preserve this as failed
transport evidence, with no parser relaxation or manually recovered policy.

The reviewed development-v2 inputs in PR81 now explicitly author the missing
response schemas and source bindings for local schema adaptation. They remain
synthetic, cannot be deployed, and introduce no fixture scripts or expected
records into exported generator inputs. Their readiness does not replace actual
audited generation. The separately owned bodyless-response reconciliation and
T05 dependency acceptance also remain necessary for the full deliverable.

Issue8 remains open; `tools/cowork.py complete` has not been run. Required source
checks and exact pushed/merged head are recorded in its accompanying checkpoint.

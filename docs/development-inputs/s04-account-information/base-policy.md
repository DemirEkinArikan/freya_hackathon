# Account-information service — synthetic development policy

This is newly authored synthetic development material, not a holdout or an
organizer/customer record. Its sole objective is to verify the caller, retrieve
their account information, read the permitted facts, and record the final call
outcome. There is no collections objective. Never request a payment promise,
record a note, arrange a callback, or transfer the caller.

## Authority and permissions

This base specification applies unless an explicitly identified later amendment
replaces the same rule. Read the supplied sources in this order: base-policy.md,
operations-email.txt, privacy-memo.txt. The email changes only the identity wrong
answer limit. The memo changes only account disclosures. No other statement is
superseded, and ordering alone does not override a rule. Amendments apply to
every call on this development service; there is no effective-date selector.

The only permitted API operations are verify_identity, get_account, and
close_call, supplied in bank.openapi.json. These names denote identity checking,
account retrieval, and finalclose respectively. The .invalid server is a
synthetic offline contract: do not deploy or call it. No authentication header,
URL, operation, response fact, or permission may be invented. Credentials, if a
real replacement contract is later approved, belong to the controller.

The read-only initial call variables are customer_id, first_name, last_name,
title, and now. Their meanings are respectively the opaque account subject,
public greeting name, public greeting surname, supplied form of address, and
audit timestamp. Each is required and nonempty. now must contain a full valid
calendar date, time, and explicit UTC offset (YYYY-MM-DDTHH:MM:SS+HH:MM or Z).
Seal the initial values; later dialogue cannot change them. Missing or malformed
initial values end the call safely with no bank API request. Never substitute a
host clock, customer identifier, greeting name, title, or timestamp.

The service operates every day at every hour. Business weekdays are Monday
through Sunday (0 through 6) and the holiday list is empty. No date arithmetic,
payment deadline, interest calculation, callback window, or calendar-dependent
outcome is authorized. now is an audit input, not a condition that selects rules.

## Identity and privacy

State the account-information purpose before asking for identity. Do not confirm
that an account/product exists or disclose balances, names of products, product
types, or any account fact until both required factors pass. Never disclose raw
identifiers, backend messages, answers, counters, or hidden workflow state.

Check birth_year first, then postal_suffix. The supplied identity wire codes are
F21 = birth_year and F22 = postal_suffix. Birth year is the caller's four decimal
digits; postal suffix is the final three decimal digits of their postal code.
Preserve leading zeros and join fragments only after the caller finishes. Ask
for the current factor again if incomplete; do not dispatch incomplete answers.
Corrections replace an unsubmitted answer. A previously passed factor remains
passed, and a failed later factor never clears it. Do not infer passing from a
plausible answer, a name, a caller assertion, accepted=true alone, or another
factor's result. Only accepted=true and matched=true for the submitted factor
prove that factor. Successful factors are never requested again.

The identity_wrong_limit is 3 across the call, shared by both factors. Each
accepted=true, matched=false identity answer consumes one unit; accepted=false
is a technical failure and does not count as an unsuccessful answer. Successful answers and
ordinary clarification turns consume none. There are no exempt hint codes
(identity_hint_exemptions is the empty list). The returned remaining value is
the cumulative number of wrong answers left under the current effective limit;
take the stricter observed count and never replenish it. Stop identity requests
as soon as the effective wrong limit is reached. At most the effective wrong
limit plus two answer submissions can occur; no automatic API retry is allowed.

A refused factor or caller withdrawal before verification is incomplete
identity. Wrong-number statements before any submitted factor are unreached
identity. Unclear identity/no answer before any submission is also unreached.
Never call get_account on a denied, incomplete, unreached, failed, or uncertain
identity. A definite, well-formed accepted=false identity receipt is a technical failure,
not proof of verification; use the incomplete/unclear outcome below. Missing,
malformed, contradictory, unrecognized, or transport-unknown identity evidence
ends safely in quarantine with no further bank request or bank close.

## Account readback

After both factors pass, call get_account at most once. It must return accepted
true and between one and two unique products (product_count_bounds min=1,max=2).
The policy applicability is multiple products. Read in the returned account
order (product_order mode=account_order). Product collections do not need any
promise or note resolution (resolve_all_products=false; record_notes=false).
This flag does not authorize a collections outcome or relax required readback.

Common disclosure fields on every product are label_text, kind_tag, balance_minor, and
currency_tag. Conditional account_disclosure_fields are tail_digits only for
K11 (card), installment_minor only for K12 (loan), and yield_bps only for K13
(deposit). Product type wire codes are K11=card, K12=loan, K13=deposit. Currency
wire codes are X91=GBP and X92=EUR. Speak the corresponding currency name and
interpret balance_minor and installment_minor as integer minor currency units
(100 units per currency unit); yield_bps is integer basis points. Do not
calculate interest or infer amounts. Read the label and applicable facts for
every returned product, identify the type in plain language, and invite the
caller to repeat or correct what they heard. Repetition makes no new API call.

Conditional fields must be present and match their declared scalar type only on
their matching product type. A loan need not contain tail_digits and a card need
not contain installment_minor or yield_bps. Missing required disclosures,
duplicate identifiers, unrecognized types/currencies, invalid counts or scalar
types, negative amounts, or contradictory API results are technical failures.
Never replace a missing value with zero, another product's value, a default, or
an internal identifier. Do not reveal age_days, arrears_amount, holding_ref,
subject_ref, response fault/advice, or a field belonging to a different type.

## Explicit schema bindings

All three requests bind customer_id to subject_ref. verify_identity binds field
to factor_tag and answer to factor_reply. close_call binds outcome to
completion_tag and reason to detail_tag. Response aliases are ok=accepted,
verified=matched, wrong_answers_left=remaining, hint=advice, error=fault;
get_account additionally binds products=holdings. Product aliases are
product_id=holding_ref, type=kind_tag, days_late=age_days, past_due=arrears_amount.
The last two are internal API facts with no collections/disclosure permission.
The remaining scalar fields retain their actual schema names.

The API uses outcome codes C81=informed, C82=not_verified, C83=not_reached,
C84=information_incomplete, C85=service_failure. Its reason codes are R14=failed,
R15=declined, R16=wrong_number, R17=unclear, R18=refused, R19=service_error. An absent
detail_tag means no reason and is permitted only for C81; every other outcome
requires its supplied nonempty reason. An empty-string reason is not allowed. These meanings are
normative even if a builder cannot represent them. informed does not mean paid,
promise, or no_promise. No opaque code may be rebound to a collections meaning.

## End states and failure discipline

Successful completion requires verified identity, a valid positive account
response, readback of all required facts for every returned product, and the
caller saying they have finished the information request. Record C81 with absent
detail_tag (no reason) only then. A loaded account alone is not completed readback. After
verification, a caller who stops before that completion gets C84/R18. A definite, well-formed
accepted=false account receipt gets C85/R19. A missing, malformed, contradictory,
unrecognized, or transport-unknown account result has uncertain evidence: end
safely in quarantine without another bank request or bank close. Do not claim unavailable information
was read or that a close was recorded before positive API evidence.

Exhausted identity gets C82/R14. Caller refusal/withdrawal before verification
gets C82/R15. A technical identity failure or incomplete/unclear identity gets
C82/R17. The fixed identity close table is failed=C82/R14,
incomplete=C82/R17, unreached=C83/R17. For incomplete identity the caller's
explicit refusal permits the additional C82/R15 choice without a callback;
for unreached identity an explicit wrong number permits C83/R16 without a
callback. No recorded_callback choice is permitted. Unreached unclear identity
gets C83/R17. Required reasons are exactly those pairs above.

Make exactly one close_call attempt on each authorized close path, after any
required ending speech. Never retry close_call, even after a definite failure.
accepted=true proves recording; accepted=false, missing/malformed results,
non-2xx transport, or uncertainty do not. Preserve the original attempted
request and end safely with a technical failure, without announcing recording.
Unknown identity or account API transport outcomes do not authorize another
request, a different outcome, or an invented fallback. Initial-input failure
has no authorized close because the subject is unavailable. An interrupted call
that cannot dispatch a close remains honestly unclosed, not a successful case.

No payment commitment, note, callback, transfer, payment-status assertion, or
collection disposition exists on any path. If a required condition/outcome is
unsupported, report the exact unsupported constraint and do not guess a default
or generate a policy that silently omits it.

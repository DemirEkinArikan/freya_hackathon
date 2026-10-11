# Conditional policy scope: inspection increment

Some source requirements can coexist on one supplied call date and conflict on
another. This increment represents one bounded case: a required offered date is
earlier than a source-owned lower bound equal to the supplied call date. It retains
both requirements and computes the condition. It grants no replacement date,
precedence exception, transfer or closing disposition.

**Runtime enforcement is not implemented here.** Default
`compile_policy_contract(policy, sources, openapi)` still rejects conditional
conflicts. Explicit `conditional_mode="inspect"` validates and hashes a detached
partial contract whose conditional descriptor has `runtime_enforced: false`.
Inspection artifacts cannot be substituted into the ordinary builder/publisher.
Plain string `unsupported_constraints` entries remain fatal in both modes; only a
degraded entry with a verified quoted transfer fallback is kept as a diagnostic. An
ordinary contract without this feature retains its existing shape and hash.

The optional feature lives inside the normal quoted rule inventory as the typed
kind `conditional_conflicts`. Its value is a nonempty list of closed records:

```json
{
  "id": "calendar-offer-conflict",
  "phase": "date_offer",
  "effect": "block_affected_route",
  "predicate": {
    "operator": "lt",
    "required_date": {"rule_id": "required-offer", "value_path": ["first_offer"]},
    "lower_bound": {"rule_id": "date-bound", "value_path": ["minimum"]},
    "calendar": {
      "weekdays": {"rule_id": "declared-weekdays", "value_path": []},
      "holidays": {"rule_id": "declared-holidays", "value_path": []}
    }
  }
}
```

These identifiers and paths are illustrative synthetic data. Each reference must
select an existing **active** typed rule/value path, with actual retained source
provenance. A superseded rule, missing path, recursive reference, claimed quote,
alternate calendar, unknown field, duplicate condition ID or fallback is rejected.
The weekdays and holidays bind the active `business_weekdays` and `holidays`
rules; they are never taken from the real clock or a built-in bank calendar.
Required-date and lower-bound references must retain distinct rules.

The required-date leaf is a closed declarative expression. Supported forms are:

- `{"op":"now_date"}`.
- `{"op":"literal_date","value":"<canonical ISO date>"}`.
- `{"op":"last_business_day_of_week","week_offset":<integer>,"week_start":<weekday integer>}`.
  The week start is explicit, Monday=0 through Sunday=6; eligible days come from
  the supplied weekday/holiday rules. Every calendar value comes from sources.
- `{"op":"if_date_equals_now","date":<expression>,"then":<expression>,"else":<expression>}`.
  Both branches are validated; the supplied call date chooses which is evaluated.

The lower-bound leaf must be exactly `{"op":"now_date"}`. No implicit calendar
roll, precedence, default day or fallback expression is inserted. Bounds of 64
conditions, expression depth eight, 64 expression nodes, 16 path components and
520 weeks in either direction limit this implementation's supported resources;
they are not banking requirements.

`evaluate_conditional_conflicts(contract, now, expected_contract_sha256=...)`
recomputes the descriptor from the active rules and source inventory. It verifies metadata and contract
hashes, then returns each condition's `clear`, `blocked` or `unknown` observation.
Missing/malformed clocks, a week without any declared business day, date overflow
or changed metadata produce `unknown`, with no replacement date. A supplied
timestamp uses its own local date; the function never reads system time. A clear
result establishes only that this predicate is false, not that an offer or write
satisfies every other requirement. Evaluation has no state, speech or tool side
effects.

The caller must supply a trusted compiled contract. Self-consistent hashes alone
do not authenticate a wholly replaced contract. The optional expected fingerprint
checks an independently frozen pin; runtime integration must provide that pin
from its protected compiler specification, never from model-extracted call data.

The descriptor retains the original date expression, calendar, rule/value-path
bindings, value hashes and source fingerprints/witnesses. Literal provenance and
structural consistency do not verify the interpretation of natural language.
Independent source-to-IR review remains necessary.

Before enabling this in a generated agent, a separate integration must bind each
affected source rule/path to its offer action, evaluate before speaking the date,
and block that route on both `blocked` and `unknown`. It must preserve other
eligible actions, freeze/repair isolation and existing write guards. A blocked
route cannot acquire an invented close or transfer outcome. Required runtime
tests include ordinary dates, weekends, holidays, alternate business weekdays,
missing clocks, multiple conditions and attempts to omit or bypass the guard.
Until that integration is independently tested, this feature is inspection only
and does not unblock full banking-builder acceptance.

All development tests use independently invented policies. No generated policies,
official customer fixtures, holdouts, models or Freya calls are used here.

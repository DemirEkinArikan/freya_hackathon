# Conditional account disclosures

An unconditional `account_disclosure_fields` array requires every selected
field on every returned product. That format remains supported and keeps its
strict missing-data rejection.

Sources that restrict a field to particular product types can instead supply:

```json
{
  "common": ["public_caption", "overdue_value"],
  "by_product_type": {
    "SUPPLIED_TYPE_A": ["public_suffix"]
  }
}
```

These names illustrate the interface only. Actual fields and product types
must come from the run's approved policy/API. The compiler invents no card
exception, type, field, fallback value or default disclosure permission.
Common fields remain mandatory on all products. The chosen type's extra fields
remain mandatory for that type; other products do not inherit them. Source
semantic type names may use an explicitly frozen `wire_codes.product_types`
mapping to supplied API codes. Ambiguous mapped keys are rejected.

The compiler uses the exact frozen `get_account` type-field alias/schema and
checks conditional keys against its enum when supplied. All selected fields
must be declared scalar fields; internal/customer identifiers and their aliases
are excluded. Compiled fact projection validates the actual type and every
chosen scalar value. Missing required data, malformed types, invalid values or
embedded identifiers still quarantine before account narration.

This fixes a generic representation gap found when the unchanged public-A
generated policy placed a type-specific field in its global array. The original
model artifacts are preserved; a fresh builder run must extract the new typed
form from approved source. Source/mock regressions establish compiler behavior,
not complete generated-policy adaptation, speech or hosted acceptance.

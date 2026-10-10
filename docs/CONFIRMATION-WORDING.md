# Prescribed confirmation wording

An optional typed `confirmation_question` rule carries the exact literal question
prescribed by an approved source. It must be nonempty text that appears verbatim
in its cited source quotation. It supports source-provided spelling, punctuation
and language; it contains no variable templates, control characters or private
engine/credential prefixes. If the source prescribes no
wording, omit the rule and the existing generic confirmation text is unchanged.

Its paired source-backed `confirmation_question_actions` array states exactly
where it applies: `commit_promises`, `commit_transfer` and/or `schedule_callback`.
The scope is nonempty and unique, with its own literal source provenance. There
is no inferred/global scope. A common question can explicitly cover several
actions; other confirmation nodes retain their legacy wording. This scalar
interface cannot encode different prescribed questions for different actions;
such a requirement must remain reported as unsupported. Selecting a wording
scope grants no action or confirmation requirement.

The compiler's confirmation node reads its protected snapshot and then the
supplied question. Only byte-exact controller recompilation grants that node
permission to read `bg_readback`; an authored copy or node prefix grants none.
The wording supplies no confirmation permission. It does not change consent
extraction, fresh-answer clearing, immutable terms, write ordering or API bodies.
Unclear/refused/missing answers still send no request.

Tests use independently quoted English and Turkish questions and exact compiled
consent transitions. These checks establish transport and guard invariants, not
speech interpretation or multilingual conversation quality. Existing generated
artifacts remain unchanged; adapting their source wording requires a fresh
approved-input build. No bank-specific phrase is added to static prompts.

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

## Read-back clauses and conversation language

Two optional typed rules add sourced words to the sealed confirmation. Each has
its own source quotation, so a read-back fact and a language statement may come
from different sentences of the source.

- `confirmation_readback` is `{actions, clauses}`.
- `confirmation_language` is `{actions, language}`.

`actions` is an explicit scope over the same three write roles. Each clause, and
the language statement, is copied verbatim and as whole words from its own
rule's quotation; `validate_policy` checks this, and so does the sealed compiler.
The texts contain no digits or other numerals, braces, control or format
characters, or engine/credential prefixes, so they cannot add a numeral, a
template or a field. The compiler checks only these literal properties, not
meaning. A value written in words, or a clause cut out of a prohibition,
is not detected; the guidance forbids both. Requests keep only their sealed
fields either way. If the plan selects an action a rule names, that action must
require confirmation; otherwise compilation fails rather than dropping the
wording.

- Clauses are spoken verbatim after the protected terms, in the same fixed line:
  `Please confirm these exact terms: {{bg_readback}}; <clause>. Do you agree?`.
  With a scoped `confirmation_question` the line is
  `{{bg_readback}}; <clause>. <question>`.
- A fixed line cannot follow a sourced language rule, which may also switch
  during the call (for example, begin in one language and change on the
  caller's request). The language statement is therefore the complete sourced
  statement, including any switch condition, and is used only when the fixed
  English line does not satisfy it. With `confirmation_language`, the sealed
  confirm node uses the `llm` entry. Its compiler-owned task text carries:
  - "Use the language this sourced rule requires at this point in the call",
    followed by the statement verbatim;
  - `{{bg_readback}}` and the clauses, verbatim;
  - the question: the sourced one, or whether the caller agrees to exactly
    these terms;
  - the unchanged wait/no-tools instruction.

  In this mode the dialogue model speaks the line, and word-for-word speech is
  not proven. The consent extraction, fresh-answer clearing, frozen request
  snapshot and refusal paths are unchanged.

Without either rule, every compiled confirmation is byte-identical to before.
`validation.py` lets only a `bg_*_confirm` node whose text equals the compiler's
output read `bg_readback`, and only after byte-exact recompilation. The sealed
kernel code is unchanged, so validator code-size headroom is unchanged.

Tests use independently quoted English and Turkish questions and exact compiled
consent transitions. These checks establish transport and guard invariants, not
speech interpretation or multilingual conversation quality. Existing generated
artifacts remain unchanged; adapting their source wording requires a fresh
approved-input build. No bank-specific phrase is added to static prompts.
